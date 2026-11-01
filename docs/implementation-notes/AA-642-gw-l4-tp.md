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
