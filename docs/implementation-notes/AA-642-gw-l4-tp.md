# AA-642 — lane L4 TripPlanner (S263): gateway gap plan 1.E, slices E1 and E2

Plan: root `docs/audits/2026-11-01-S263-gateway-gap-audit/plan.md` §1.E (E1, E2) and §2 (lane L4).
Both slices edit `backend/shared/llm_gateway.py`, so they ship as two stacked PRs (E1 first).

## E1 — truncation is a failure, route deadline

### Decisions
- A reply whose stop reason is in `aa_llm.ledger.TRUNCATED_STOP_REASONS` (`max_tokens`, `length`,
  `model_context_window_exceeded`) is a **failed, billed** attempt: the `Call` keeps its tokens and catalog cost,
  `failed=True`, `status='truncated'`, `error_class='truncated'`, `stop_reason` the provider's. The route moves on to
  the next account / model with the same `max_tokens` (no automatic raise of the limit); when every attempt is cut,
  `generate_text` raises `BedrockError` and the narration degrades. Truncated text is never returned.
- The truncated row gets `pricing_mode='catalog'` (it is billed) and its cost counts against the spend cap
  (`_add_spend` adds `cost_usd` as for a success). `ledger_row` uses `_billed(call)` for that.
- Route deadline = `route.deadline_s` (from `route_deadline_s` of the config read, 1..3600 like the migration 320
  CHECK) else `config.LLM_ROUTE_DEADLINE_DEFAULT_S` = 20 s, always capped at `LLM_ROUTE_DEADLINE_MAX_S` = 25 s. Why
  25: the API Gateway HTTP API stops at 30 s, and the handler still needs time to build the basic narration and write
  the ledger rows. The assembly Lambda's own timeout (60 s) is not the binding limit.
- Enforcement mirrors CIS AA-1018 part 2: before every attempt, if less than `LLM_ROUTE_DEADLINE_MIN_ATTEMPT_S` (1 s)
  is left, append one 0 USD row (`status='error'`, `error_class='deadline'`, model = the attempt not started) and
  raise `RouteDeadlineExceeded` (a `BedrockError`, `reason='route_deadline'`). The first attempt always starts.
- Each attempt's production client is built with the time left as its timeout
  (`bedrock_satellite.default_client_factory(timeout_s=...)`: connect `min(t, 5)`, read `t`, one attempt, no boto
  retry); for acc3 the AssumeRole STS client gets the same bound. Injected factories (tests, scripts) are unchanged.
- The handler passes the exception's `reason` to the degrade line (`narration_degraded.reason = route_deadline`);
  any other route failure keeps `provider_unavailable`.

### Changed
- The config view `catalog.v_llm_gateway_config_v1` does not carry `route_deadline_s` today (CIS 323), and the
  vendored `aa_llm.contract.GATEWAY_CONFIG_SQL` selects fixed columns, so on Dev every TP route uses the default
  20 s until the view appends the column (plan E3, CIS 329) and the core SQL selects it. The table fallback read
  (`shared.llm_role_config`, only while the view is missing) now selects `route_deadline_s` too.
- Embeddings (`embed_texts`) are unchanged: they already use no-retry clients with 5 s connect / 10 s read.

### Tradeoffs
- `read_timeout` bounds the wait for each socket read, not the whole call; for a buffered `InvokeModel` /
  `Converse` the reply arrives in one piece after generation, so in practice it bounds the call. A thread-based hard
  timeout was rejected: the abandoned thread keeps running in the frozen Lambda container.
- Failing over after a truncation can bill a second cut reply. Accepted: the plan asks for failover, the
  narration prompt asks for 1-2 sentences per day with `max_tokens` 1200, and the spend cap still applies.

### Should know
- Live today acc3 refuses every call (Error 002), so a compose request writes one `refused` / `access_denied` row
  per attempt and answers basic narration; truncation and deadline paths are proven by unit tests only until an LLM
  is back.
- `RouteDeadlineExceeded.reason` is a class attribute; `AiPaused` keeps its own `reason`.

## E2 — prompt_version, prompt_hash, request_id on the ledger + CI gate

### Decisions
- Mirror the CIS AA-900 register, self-contained in TripPlanner: `backend/shared/prompt_registry.py` (the same
  `prompt_hash_of` algorithm, byte for byte: sha256 of the symbols' source text joined by a NUL line, read with `ast`),
  manifest `backend/shared/prompt_versions.json` (same JSON shape), gate script `backend/scripts/prompt_registry_check.py`
  (check / `--bump`, same rules; no `--sql`: the TP read guard forbids SQL naming CIS tables in backend code, and the
  manifest fields are already the register's columns) and the CI test `backend/tests/test_prompt_registry_gate.py` (runs in the
  required "Backend (pytest)" job).
- One manifest entry: `tp_compose` `v1`, source `backend/assembly/agent.py`, symbols `_COMPOSE_SYSTEM`,
  `_RENARRATE_SYSTEM`, `_build_body` (both narration prompts and the user-prompt template share one stage).
- `prompt_hash` = sha256 of the system prompt sent (the CIS gateway's definition since AA-1018 part 2), so every call
  of one prompt version shares a hash whatever the trip.
- `prompt_version` = the newest manifest version while the deployed source still matches it (checked once per
  container); a drift prints one `llm_gateway_note` and writes NULL. Stamped on every attempt of the request (success,
  refused / error, truncated, deadline).
- `request_id` = Bedrock `ResponseMetadata.RequestId` of the reply, or of a botocore `ClientError` (`e.response`);
  None for an error with no provider answer (STS failure, timeout) and for the deadline row. Embedding rows get it too.

### Changed
- CIS writes `prompt_version` only when `shared.prompt_version` also holds the (stage, version, hash) row. TripPlanner
  cannot read that table (no grant), so it writes the manifest version as soon as the source matches. The CIS rows for
  the `tp_*` stages come later (plan lane L1), built from `backend/shared/prompt_versions.json` (stage, version,
  prompt_sha256, source_path; source paths are TripPlanner repository paths).
- No migration in this lane.

### Tradeoffs
- Reading `agent.py` at run time (ast, once per container) instead of hashing a constant at build time: it proves
  the deployed bytes match the manifest, the same guarantee as CIS, at the cost of one file read per cold start.

### Should know
- A prompt edit in `agent.py` now fails CI until the manifest is bumped. Bump, then ask AA-CIS-App to register the
  new row.
- Live check needs no LLM: an acc3-refused compose attempt writes a `refused` row with `prompt_hash` and
  `prompt_version='v1'` (request_id is set when Bedrock answered the refusal).
