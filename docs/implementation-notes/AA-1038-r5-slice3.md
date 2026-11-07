# AA-1038 R5 slice 3 — leads, brand and the per-tenant AI cap (AA-TripPlanner-Web part)

Session S272 (lane l10). Root ADR 0019 d10, d11, d2 / b19 (+ the S272 d11 amendment). Contract `lead.submitted.v1`
(root, new) and `tenant.entitlements.v1` 1.5 (CIS 390). Other parts: AA-CIS-App (CIS 390, kit core `brandTheme.ts`),
AA-Partner (`crm_tp_lead_pull`), root (contracts, ADR, DM-7.1, ERD design page).

## Decisions

- **Store in the outbox, deliver by the tenant's channel.** tp-026 lets `tripplanner.outbox` carry `lead.submitted`
  rows with `delivery_channel` / `delivered_at` / `delivery_attempts` / `delivery_error`: the outbox already commits
  with the trip's `sent` event, has a tenant (tp-025) and a redaction marker (tp-021). `v_inquiry_outbox` filters its
  event, so Booking never sees a lead.
- **Refuse before storing** when the lead could not be delivered (no route, the route view not there yet, a webhook /
  e-mail channel this deployment cannot use): a lead that can never leave is personal data with no purpose.
- **CRM channel = pull + ack over HTTP** (the CRM has no DB grant here, ADR 0008a). New route class `/trip/m2m/v1/*` in
  the assembly Lambda, reached by the existing `ANY /trip/{proxy+}` route: an aa-m2m token (CRM client, scope
  `tripplanner.leads/deliver`) replaces the edge key on these paths only, verified before any SQL with the shared
  verifier byte copy (`groups_claim="scope"`, as CIS / Booking). The path's tenant is bound (RLS) and the payload's
  tenant is checked again on the way out.
- **Webhook / e-mail inline after the commit**, retried by the daily `tp_lead_deliver`; the 7-day hold ends every
  undelivered lead (any channel) with an ids-only payload. The expiry runs live on the schedule (the contract's promise,
  not a clean-up of history); a manual run with `"dry_run": true` counts only.
- **Per-tenant AI cap in `budget_status`**, after the app caps, from CIS 390's `catalog.v_tenant_ai_budget_v1`
  (fail closed for an agency tenant; the demo tenant / bare path keep today's behaviour). Search learns its tenant from
  the same signed assertion in the browse Lambda (no DB binding there: `tenant.using`). The ledger row takes the tenant
  from the request context, so nothing in the request body can name it.
- **Review fix pass (S272).** (1) `tp_lead_deliver` runs the expiry FIRST in its own transaction, then the retry
  with a 120 s wall-clock budget (`RETRY_BUDGET_S`; the rest is `deferred`), a breaker per tenant (one failed attempt
  = that tenant is not tried again this run) and per channel (`webhook_key_unavailable` / `email_sender_unavailable`
  stop the channel). An expired undelivered lead adds `attention: ["lead_expired_undelivered"]` to the result, which
  `jobs/handler.attention` reports, so the scheduled tick fails loudly (the jobs Lambda error alarm) before anyone
  wonders where a lead went; expiries are counted per channel (`expired_crm`, ... in `lead_deliver_run`).
  (2) The crm channel is gated like the other two: `TP_LEAD_CRM_PULL_ENABLED` (default off) on the assembly Lambda;
  off = 503 `lead_channel_unavailable`, nothing stored. (3) `Traveller.phone` is pinned to E.164 with its country
  code (`^\+[1-9][0-9 ()-]{5,39}$`) in the contract, `leads.validate` and the form (hint + `PHONE_RE`), because the
  CRM normalises without a region; the CRM also keeps a phone it cannot normalise as text in the inquiry message.
- **Review fix pass 2 (S272).** Each item has its reproduction as a test (`test_aa1038_lead_review.py`,
  `test_aa1038_lead_deliver.py`, `lib/leadForm.test.ts`, `lib/botCheck.test.ts`, `migrations/tests/test_tp026_pg.py`).
  (1) **True 7-day maximum.** `hold_cutoff` = now - (`LEAD_HOLD_DAYS` - one tick interval - 1 h margin) for the daily
  EventBridge tick (`EXPIRY_TICK_INTERVAL` mirrors Infra `cron(30 19 * * ? *)`; a test replays that schedule with a
  lead written 1 minute after a tick and a tick up to 59 minutes late). Chosen over an hourly schedule or an
  `expires_at` column: no Infra or migration change. `TP_LEAD_HOLD_DAYS` is clamped to 2..7 (`config.lead_hold_days`;
  a non-number = 7). `MAX_ATTEMPTS` 3 (the expiry is idempotent) and a re-queued run is attention at once.
  (2) **Whole backlog each run.** The expiry loops batches of `EXPIRE_MAX` (each its own transaction) until none is
  left or `EXPIRE_BUDGET_S` (90 s) is used, before the retry part; a batch that raises is tried again at once
  (`EXPIRE_TRIES`); rows a concurrent ack holds (`SKIP LOCKED`) are looked at again; rows left ask for attention
  (`lead_expiry_incomplete`).
  (3) **Send limits.** BotID Basic on `POST /api/trip/send` in the BFF (own pathname, like narrate), then in the
  Lambda `ratelimit.hit` scopes `send` (per guest session, 5/h) and `send_ip` (per IP, 20/h), counted only when a lead
  would be stored, then a per-tenant 24-hour cap (`TP_LEAD_TENANT_DAILY_CAP`, 200) counted inside the send
  transaction; every refusal stores nothing. tp-026 adds `outbox_lead_tenant_created_idx` for that count.
  (4) **Reject path.** `_text` applies NFC before the length check (the CRM's `clean_text` rule); the form does the same
  with code points; one rule pinned by shared vectors (root `docs/contracts/rules/`, byte copy
  `backend/tests/contracts/lead.submitted.v1.traveller-rules.json`) run by the Lambda, the form and the CRM tests. A
  rejected ack logs an ERROR line and the daily run reports `lead_rejected_by_crm` (counts per reason, no PII); the
  CRM raises its ops alert `tp_lead_rejected`.
  (5) **Per-tenant crm readiness = a pull beacon.** Each M2M list call records a beacon for that tenant (the
  `ai_rate_counter` table, keyed hash, scope `crm_pull`, 5-minute windows, purged with it); the crm channel stores a
  lead only when the CRM listed the tenant within `TP_LEAD_CRM_PULL_FRESH_S` (600 s), else 503
  `lead_channel_unavailable`. The CRM enters the agency before listing, so a locked / offboarding / unprovisioned
  agency never refreshes it. Chosen over a CIS readiness column: CIS does not hold the CRM's lock state, and this
  needs no migration or contract change between CIS and the CRM. A beacon read error fails closed (refused).
  (6) **Hardening.** `serve_m2m` serves only a tenant whose route resolves to crm now (404 `tenant_not_on_crm`);
  an ack never changes a final state; the webhook refuses a non-public address (`webhook_urlnotpublic`); the inline
  SES client has 2 s / 3 s timeouts and no SDK retry; tp-026 guards the `v_inquiry_outbox` filter (whitespace-
  normalised view definition must end with the one predicate), tested on a real PostgreSQL with widened views.
- **Review fix pass 3 (S272).** Two majors, each reproduced as a test.
  (1) **The 7 days survive a failed tick.** The pass-2 cutoff kept a lead inside 7 days only when every tick ran: if
  the tick that picks a lead fails (the database unreachable past `_tried`'s ~3 s, an RDS reboot / failover / stop, a
  cold-start pool error, or `run_due` stopping below `MIN_TIME_LEFT_S` before the job), the re-queued job waits for
  the next daily tick (the scheduler has no other wake-up) and the lead was redacted at 7 d 22 h 59 m. `hold_cutoff`
  now also absorbs one failed tick (`EXPIRY_TICKS_ABSORBED = 1`: hold - 2 intervals - 1 h, 4 d 23 h at 7 days), and
  the floor of `TP_LEAD_HOLD_DAYS` is 3. Chosen over waking the jobs Lambda hourly: no Infra change, and an hourly
  wake-up costs a Lambda start + DB connection 24 times a day for one job. Cost: an undelivered lead is held about
  5 to 6 days instead of 6 to 7 (delivery retries get one day less); the CRM and a working webhook / e-mail deliver
  within minutes, so this only shortens the life of leads that were failing anyway. Two failed ticks in a row are
  the residual risk (each fails loudly). Test: `test_aa1038_lead_deliver.py` replays the real cron with the expiring
  tick's database down, including the reported lead (written 2026-11-02 20:31 UTC, now redacted at 6 d 22 h 59 m), and
  shows that the pass-2 cutoff gave 7 d 22 h 59 m.
  (2) **A webhook route on a non-public address refuses before storing.** The CIS 390 CHECK accepts
  `https://127.0.0.1/hook`; `channel_ready` checked only that a URL and the key exist, so the lead was stored and
  could never leave. `channel_ready` now runs `webhook_target_problem` (https + `public_address_check`; an IP literal
  is not looked up, a host name resolves within `TP_LEAD_WEBHOOK_RESOLVE_TIMEOUT_S`, 2 s, on a small thread pool as
  `getaddrinfo` has no timeout) before the transaction: 503 `lead_channel_unavailable`, nothing stored, nothing
  posted, log event `lead_webhook_target_refused` (error code only, never the URL). A host that does not resolve is
  now `UrlUnresolvable` (`webhook_urlunresolvable`, transient) and stays apart from `webhook_urlnotpublic`
  (permanent). `tp_lead_deliver` treats `webhook_urlnotpublic` / `webhook_url_not_https` as a route defect: that
  tenant's rows are counted `route_defect`, not tried again this run, and the run asks for attention
  (`lead_route_defect`). The rows stay inside their hold (not redacted at once): AA staff may fix a typo in Tenant 360
  within the hold and the lead then still reaches the agency; the hold bounds the data either way. Only rows stored
  before the URL changed can get there now. Not done here (CIS, follow-up): refusing private / loopback literals in
  the CIS 390 CHECK or the Tenant 360 form.
- **Brand** via the kit core layer only (`ui/core/brandTheme.ts`): no per-page colours, the planner just renders the
  block. BO-8 badge only with `tp.branding`.

## Changed

- `migrations/026_lead_outbox_delivery.sql` (+ `migrations/tests/test_tp026_pg.py`) — the migration-only PR.
- Backend: `assembly/leads.py`, `assembly/handler.py` (`_send_lead`, `serve_m2m`, the M2M branch of `_handle`),
  `shared/m2m_auth.py`, `shared/tenant_budget.py`, `shared/tenant.py` (`current()`, `using()`, source `m2m`),
  `shared/llm_gateway.py` (tenant cap, ledger tenant), `browse/handler.py` + `browse/search.py` (search tenant, pause
  reasons), `jobs/lead_deliver.py` + registry, `config.py`.
- Frontend: `components/LeadForm.tsx`, `lib/leadForm.ts`, `TripPanel`, `PlannerApp` (agency), `lib/plannerTenant.ts`,
  `lib/useTrip.tsx`, `lib/api.ts` (lead, tenant on search), `/api/trip` (`lead`), `/api/browse` (signed search),
  `app/[tenant]/plan/page.tsx` (brand `<style>`).
- Tests: `test_aa1038_leads.py`, `test_aa1038_tenant_budget.py`, `test_aa1038_leads_pg.py` (CI PG list),
  `lib/leadForm.test.ts`; guard tests adjusted: `test_extraction_ops.py` (a `name/` is a scope, not a table),
  `test_read_guard.py` (`m2m_*` routes are not trip reads), `test_jobs_pg.py` (third daily kind).

## Tradeoffs

- The CRM pull is per agency per minute (CRM side); fine at pilot scale.
- The webhook key is derived per tenant from one TripPlanner master secret: AA hands the agency its key (a small ops
  script, follow-up); rotating the master rotates every tenant's key.
- `narration_cache` and `ai_rate_counter` stay tenant-less: the cached text is AA master text + AI for the same
  itinerary, no tenant data; the limiter keys are per session / IP.

- The per-tenant daily cap protects the agency and AA's SES from a send loop, but a determined sender with many IPs
  could fill one tenant's cap for a day (the tenant then gets 429 for real travellers). 200 a day is far above pilot
  traffic; raise it per deployment with `TP_LEAD_TENANT_DAILY_CAP`.
- The send path now resolves the webhook host before storing (up to 2 s added to a send on a slow resolver; a
  resolver failure refuses the lead, the traveller can send again). The check and the POST resolve separately (DNS
  rebinding between them is the residual risk, bounded by https-only and the second check at POST time).
- The webhook is still retried only by the daily run (an endpoint down for a minute delays a lead up to a day), and
  `channel_ready` treats the webhook channel as ready once the master secret exists, before AA has handed any agency
  its key; the key-handover script is the follow-up that must ship before a tenant is routed to a webhook.
- The beacon makes a crm tenant refuse leads for up to 10 minutes after a CRM outage starts (and after the CRM is
  first switched on): intended, a lead is stored only when it can leave.

## Review fix pass 3 (S272)

- **Name token rule (finding: a name like `-` was stored, delivered and lost at the CRM).** The CRM's intake indexes
  the traveller's name with `blind_index.name_tokens`, which needs a letter or digit token; `validate` accepted any
  non-empty cleaned name, so `-`, `...`, an em dash or an emoji-only name was stored here, pulled, acknowledged
  `stored` (final `delivered`, redacted) and then failed in the CRM's intake with no alert. Now the contract's rule
  (root `docs/contracts/rules/lead.submitted.v1.traveller-rules.json`, keys `name_token_normalisation` `NFKD` and
  `name_token_pattern` `[\p{L}\p{N}]`, new name vectors) is applied by `leads.has_name_token` (Python `str.isalnum`
  after NFKD; a test checks it equals Unicode category L or N on every code point, which is also what the CRM's `\w`
  split keeps) and by the form (`hasNameToken`, the same regex as the rules file): 422 `invalid_lead`, fields `[name]`,
  nothing stored. A CJK-only or accented name is valid. The CRM rejects such a lead at the pull as well (ack
  `rejected`, so `lead_rejected_by_crm` here and `tp_lead_rejected` there), for a producer that skips this check.
- **Exact daily cap.** The cap was count-then-store under the per-trip lock only, so two sends of one tenant on two
  trips could both count `cap - 1` and both store. `tenant_leads_24h` now takes
  `pg_advisory_xact_lock(hashtext('tp_lead_cap:' || tenant_id))` before the count (always after the per-trip lock, so
  no crosswise wait), held to the commit of the lead row. Tradeoff: sends of one tenant are serialised for the rest of
  their transaction (the trip summary read and the inserts, milliseconds); the cap is a safety bound, not a hot path.
  Tests: `test_review_repro_the_cap_lock_is_taken_before_the_count` (offline, lock statement before the count) and
  `test_two_concurrent_sends_never_pass_the_tenant_cap` (local Postgres, two connections, a slowed trip summary: one
  200 and one 429 `lead_cap_reached`; it fails with the lock statement removed).

## Review fix pass 4 (S272)

- **Finding.** `validate()` checked the e-mail's length and pattern, then returned `v.lower()`. `str.lower()` turns
  U+0130 (capital I with dot above) into `i` + U+0307, so `'İ' * 33 + '@example.com'` (local part 33) was sent with a
  local part of 66 code points, and `'a' * 60 + 'İ' * 4 + '@example.com'` with 68; a long domain also pushed the
  total past 254. The lead was stored and answered 2xx, then the CRM rejected it (loud, but the request never reached
  the agency, which breaks the contract's "a lead the producer accepts is never rejected by the consumer").
- **Decision.** Lower-case first, then the length (`MAX_EMAIL`) and `_EMAIL_RE` on that value, which is the value sent.
  The form's `travellerFieldOk` does the same (`toLowerCase()` before the code-point length and `EMAIL_RE`); it still
  sends the e-mail as typed (trimmed) and the Lambda lower-cases it. U+0130 is the only code point whose lower case is
  longer than one code point, and `\s` agrees with `str.isspace()` everywhere, so this is the only e-mail gap.
- **Tests.** Shared vectors `dotted_capital_i_fits`, `dotted_capital_i_lengthens_local`,
  `dotted_capital_i_lengthens_total` (rules file byte copy); `test_review_repro_an_email_lower_casing_lengthens_is_refused_before_storing`
  (the review's two addresses plus the total case: 422 `invalid_lead`, fields `[email]`),
  `test_the_email_sent_is_the_lower_case_value_that_was_checked`, and the matching form test in `leadForm.test.ts`.
  The vendored `lead.submitted.v1.json` takes the new `Traveller.email` description.

## Should know

- Merge order: tp-026 migration PR (applied by Migration Apply), CIS 390 (applied), the CIS kit PR and its Kit Sync PR
  here, then this code PR (it imports `ui/core/brandTheme.ts`; the branch carries the tp-026 commit until rebase).
- AA-CIS-Infra follow-ups before the channels work live: the aa-m2m resource server `tripplanner.leads` (scope
  `deliver`) for the CRM client and `TRIPPLANNER_API_URL` on the CRM task; Secrets Manager
  `tripplanner/<env>/lead-webhook-key` readable by the assembly and jobs roles; `TP_LEAD_EMAIL_FROM` + SES send for both.
- These Infra changes are deploy PREREQUISITES of each channel, not notes: switch `TP_LEAD_CRM_PULL_ENABLED=true` on
  the assembly Lambda only together with (or after) the CRM's M2M scope and `TRIPPLANNER_API_URL`; give the jobs role
  the webhook key and SES send in the same apply as the assembly role (else every retry stops at the channel breaker
  and the expiry ends the lead, loudly). Until then every channel answers 503 `lead_channel_unavailable` and stores
  nothing; the slice is not live-verified before the crm pull is on.
