# AA-642 S0 (TripPlanner part): fail closed + degrade mode

Slice B1a of the S262 AI gateway plan. Root ADR 0009 decisions 5 (declared degrade mode for an
`aws_only` writer stage) and 6 (fail closed when spend cannot be read).

## Decisions

- **Spend unreadable = exhausted.** `llm_gateway.budget_status` returns
  `{"exhausted": True, "reason": "spend_unreadable", "spent_usd": None}` when the
  `tripplanner.llm_spend_daily` read fails. Over the cap the reason is `cap_reached`; under it `None`.
- **A failed counter write pauses AI for 60 s per container** (`SPEND_UNREADABLE_HOLD_SECONDS`), because
  a lost write means the counter undercounts and the cap could never be reached. Per container (module
  global), like the throttle cooldown; no DB state, so it cannot itself fail.
- **Route unreadable = `AiPaused("route_unreadable")`**, a subclass of `BedrockError`, raised by
  `load_route`. It is not cached, so the next request reads again. `BUILTIN_ROUTES` stays only as the
  documented list of expected routes (tests, `ONLINE_STAGES`); no call path uses it any more.
- **Provider outage = degrade.** A `BedrockError` out of the narration (every model of the route failed,
  e.g. acc3 refusing since 29/10) answers 200 with `basic` / `master_basic`. Any other exception is a
  defect and still answers 502 `narration_failed` (failed attempts logged in both cases).
- **Response flags.** `ai_paused: true` when AI was paused or failed and the answer used no model;
  `ai_budget_exhausted: true` keeps its AA-805 meaning (cap reached). Never both: the paused reason wins
  (`spend_unreadable` is reported as paused, not as budget).
- **Search never answers 503 for a refused model call.** Over the cap → keyword ranking with
  `ai_budget_exhausted: true`; spend or route unreadable → keyword ranking with `ai_paused: true`.
  `SearchUnavailable` and the browse handler's 503 branch are removed. A cache hit is still served
  semantically over the cap (it spends nothing), but not when the route itself cannot be read (the
  cache key needs the route).
- **ERROR log lines.** `{"evt":"llm_gateway_error","level":"error","what":…}` for `route_unreadable`,
  `spend_unreadable`, `spend_write_failed`, `call_log_write_failed`; `{"evt":"narration_degraded",
  "reason":…}` per degraded narration. The old `llm_gateway_note` stays for non-errors (missing price,
  shadow configured).
- **Frontend notice.** `lib/aiNotices.ts` holds the EN and VI text; English unless `<html lang="vi">`
  (TripPlanner has no language picker yet, pages open in English). The planner shows one `role="status"`
  line under the narration. The `master_basic` label explanation now covers both "paused" and "limit
  reached".

## Changed

- `backend/shared/llm_gateway.py`: `AiPaused`, fail-closed `budget_status`, `load_route` raises instead of
  using built-in models, `_add_spend` hold flag, ERROR lines.
- `backend/assembly/handler.py` narrate: degrade path, `ai_paused`, `narration_degraded` line.
- `backend/browse/search.py`, `backend/browse/handler.py`: keyword fallback for refused calls, 503 removed.
- Frontend: `lib/aiNotices.ts` (+ test), `lib/api.ts` `narrate` returns `notice`, `lib/useTrip.tsx`
  `narrationNotice`, `components/TripPanel.tsx` status line.
- Tests: `test_llm_gateway.py` (fail-open test replaced), `test_assembly.py` (502 test replaced by the
  degrade test, plus spend-unreadable, route-unreadable, master_basic and defect-502 tests),
  `test_browse.py` and `test_search_embed_cache.py` (503 → keyword).
- Docs: `CONTEXT.md` AI guards, `README.md` daily spend paragraph.

## Tradeoffs

- **More basic narrations during a DB blip.** Failing closed means a short DB problem turns AI off for
  that request (and for 60 s after a failed counter write). Accepted: ADR 0009 d6 prefers a paused
  feature to unaccounted spend, and the basic answer is still useful.
- **Last good route not reused.** When the 60 s route cache expires and the read fails, AI pauses even
  though the previous route was fine. Simpler and matches "unreadable config pauses AI"; a DB that cannot
  serve two small SELECTs is unlikely to serve the spend counter either.
- **No breaker yet.** With acc3 blocked every AI-needing narration still tries STS + invoke before it
  degrades; the breaker belongs to the shared core (S8), not this fork.

## Should know

- The CIS app card text "AI paused (spend unreadable)" (lane C, `platform_apps.py`) reads CIS-side data;
  this PR only adds the TripPlanner log lines and response flags.
- Live check after deploy: `POST /trip/{id}/narrate` on a trip that needs AI returns 200 with source
  `basic` / `master_basic` and `ai_paused: true` while acc3 refuses (0 USD: the call is refused).
  `GET /browse/search?q=…` returns 200.
