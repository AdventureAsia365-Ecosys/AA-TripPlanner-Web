# AA-1027 — lane l2-tp-gw-e3 (S264): gateway plan slice E3, plus the TripPlanner part of AA-1024 D8

Plan: root `docs/audits/2026-11-01-S263-gateway-gap-audit/plan.md` §1.E row E3 (gaps PC-2, RW-13, TP-2, TP-3 breaker
part) and row D8 (TripPlanner's Route table). CIS side: AA-CIS-App migration 341 (`catalog.v_llm_gateway_config_v1`
gains `route_deadline_s` and `deployment_routing`), a migration-only PR that merges and is applied first (MIG-4).

## Decisions
- **Dispatch by deployment.** Each model is called on its deployments from the view's `deployments` column (323),
  in priority order, with the deployment's `wire_id` (not the catalog's `bedrock_profile_ids`). The rules mirror CIS
  `vendor_route.deployment_plan`: enabled deployment and connection; `served_roles` include the stage's role (NULL =
  every role); the stage's `allowed_scopes` allow the connection's `data_scope` (no stage policy: `vendor_direct` only
  as the first candidate); then no open circuit.
- **What TripPlanner can call** (replaces `SUPPORTED_ACCOUNTS`): Bedrock only (T-11, TripPlanner stays on AWS) — a
  `task_role` connection with the Lambda role in the connection's region, or an `assume_role` connection whose
  `account_id_env` is `BEDROCK_ACCT_PRIMARY` with the satellite role and ExternalId from the Lambda env. Any other
  connection (an API-key vendor, an assume-role account TripPlanner has no env for) is listed in the error, never
  called. The view exposes only non-secret facts; account IDs, role names, ExternalIds, secret names and provider
  error text stay CIS's.
- **Breaker per connection, two sources** (`backend/shared/llm_breaker.py`):
  1. CIS state from the view (341): a tripped connection circuit (CIS `breaker.connection_health`: credential /
     quota on one model, access_denied on 2 models, newer than the connection's last answer) skips every deployment of
     the connection, **open or half-open**; a deployment circuit skips while open or while another caller holds its
     probe lease. TripPlanner takes no probe lease and writes no health row, so a half-open connection call from it
     could never close the circuit; CIS (a job, the daily probe, Test connection) does.
  2. The container's own breaker over its attempts with the CIS `RULES` numbers (access_denied / quota open at once
     for 300 s, credential 120 s, throttled after 3 for 30 s, other after 5 for 60 s, doubling on a failed probe), the
     container's connection circuit on one quota / credential failure or access_denied from 2 models; a content
     refusal (ValidationException, input too long) is not counted; a truncated reply counts as an answer.
- **Rows when a circuit skips.** No provider attempt, so no row on the connection. A model with nothing left to call
  gets one 0 USD `refused` row (class = the class that opened the circuit, `account` / `connection_key` / `provider`
  NULL, `quality_signal.not_called = circuit_open`), like the CIS gateway's `CircuitOpen` refused row. When nothing at
  all was attempted the route raises `CircuitOpen` (reason `circuit_open`): narration answers `basic` + `ai_paused`,
  search the keyword ranking (no throttle cooldown: the breaker holds the circuit).
- **Pricing from the shared core.** `aa_llm.pricing.quote` with `catalog_rates` of the catalog row and the
  deployment's price overrides; the ledger's `pricing_mode` is the quote's (`catalog`, `override`, `estimate`).
- **Before CIS 341** (SQLSTATE 42703 on the appended columns; the wider read is retried after `VIEW_RETRY_SECONDS`)
  the 323 columns are read and each deployment gets the legacy facts of the two connections TripPlanner has always
  called (`_LEGACY_CONNECTIONS`: acc3 satellite, acc2 Lambda role); other connections are unknown and never called.
  The same mirror serves the pre-323 table read and a model with no deployment row (from `bedrock_profile_ids`,
  `account_route` first). So the code PR is safe before or after 341; MIG-4 order is still the plan.
- **D8:** `BUILTIN_ROUTES` keeps `tp_compose` and `tp_search_embed` only; tests that used the dead stage names as
  "an offline stage" / "a stage outside the manifest" use invented names. CIS 336 (not edited, not applied: its guard
  waits for 30 days without ledger rows of the three stages) switches the rows off.

## Changed
- `backend/shared/llm_gateway.py`: `Deployment`, `Model.deployments`, `Route.allowed_scopes`, `deployment_plan`,
  `model_deployments`, `as_deployment`, `CircuitOpen`, `_quote` (core pricing), `_not_called`; `generate_text` /
  `embed_texts` loop over plans; task-role embed clients cached per region (`_task_embed`); view read with the 341
  columns (`GATEWAY_ROUTING_SQL`) and its 42703 fallback; `SUPPORTED_ACCOUNTS`, `_accounts`, `_no_account`, `_cost`
  removed; `Call.pricing_mode`, `Call.auth_mode`, `Call.provider` None for a row with no connection.
- `backend/shared/llm_breaker.py` (new): the state machine and the container breaker.
- `backend/browse/search.py`: `CircuitOpen` → reason `circuit_open`, before the throttle check.
- Tests: `tests/test_llm_gateway_deployments.py` (new), a narration degrade test in `tests/test_assembly.py`, a
  search test in `tests/test_search_embed_cache.py`; existing gateway tests follow the new names and messages;
  `tests/conftest.py` clears the container breaker per test.
- CONTEXT.md (AI section), README (stage list), `backend/config.py` comment.

## Tradeoffs
- Skipping a half-open connection circuit differs from CIS (which lets one call probe). The cost: TripPlanner
  recovers only once a CIS call has answered on the connection (CIS has a daily probe and the admin Test connection).
  The gain: no doomed acc3 attempt per container per open period while the account is blocked, which is the E3
  Done-when.
- The container breaker duplicates the CIS rules in ~150 lines instead of a shared-core module: `aa_llm` is a byte
  copy of CIS and changing it is a CIS PR plus a sync; worth doing when a third app needs a breaker.
- Missing catalog price: the core's estimate (high, flagged) replaces the old NULL cost. The CIS CHECK
  `llm_model_catalog_enabled_needs_price` makes this a corner case; NULL hid spend from the cap.
- The appended view column is one JSON array (`deployment_routing`) aligned with `deployments`, not new keys inside
  `deployments`: a v1 view only appends columns, and the old consumers keep reading exactly what they read.

## Should know
- Merge order: CIS 341 migration-only PR → Infra copy → Migration Apply (contract) → this TripPlanner PR. The code
  also runs before 341 (legacy facts); after 341 the view's circuit state takes effect within the 60 s route cache.
- Ledger rows: `account` and `connection_key` are now the deployment's connection key (the same `acc2` / `acc3` on
  Dev today); `provider` follows the connection's auth mode.
- `test_no_gateway_bypass.py` still passes: the new module makes no provider call.
- E4 (stage x model params) is not in this slice; no params table exists on Dev yet.
