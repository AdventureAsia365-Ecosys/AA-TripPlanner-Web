# Tasks: AA_TripPlanner_AI (map-first, v0.3)

- [x] 1. Repo scaffold
  - Create `frontend/`, `backend/browse/`, `backend/assembly/`,
    `backend/shared/`, `backend/extraction/` per structure.md
  - _Requirements: scaffold only_

- [x] 2. Database schema  (SQL written; applying to RDS is human-gated — see docs/smoke-test-runbook.md)
  - Write `migrations/001_tripplanner_schema.sql` (itinerary_components,
    customers, sessions, trip_events, trip_drafts) per design.md
  - Write `migrations/002_shared_destinations.sql` — coordinate table
    name/columns with any existing AA-CIS-App migration conventions for
    the `shared` schema before applying
  - _Requirements: 1_

- [x] 3. Extraction pipeline  (code + offline tests done; the real 31-tour run is human-gated — needs DB + Mapbox + Bedrock)
  - `backend/extraction/country_normalize.py` — hard-coded map
  - `backend/extraction/geocode.py` — Mapbox Geocoding, cached lookup
    against `shared.destinations`
  - `backend/extraction/verify.py` — deterministic keyword/synonym check
  - `backend/extraction/run.py` — full pipeline: read active tours ->
    read+group atoms -> LLM categorize -> verify (retry once) ->
    geocode -> insert components
  - Run against the real 31 active tours (no mock data step)
  - _Requirements: 1_

- [x] 4. Bedrock satellite module  (code + stubbed tests done; live call needs IAM trust — human-gated)
  - `backend/shared/bedrock_satellite.py` — STS assume-role (acc3
    primary, acc1 fallback), Bedrock invoke wrapper, streaming support
  - Unit test against a stubbed STS/Bedrock response — no live AWS calls
    in tests
  - _Requirements: 3_

- [x] 5. Lambda A — Map/Browse
  - `backend/browse/tiles.py` — fixed 1° grid tile query with filter
    support (activity, intensity_level, country, season)
  - `backend/browse/search.py` — pgvector search within pre-filtered set
  - `backend/browse/handler.py` — routes: GET tiles, GET destination
    detail, GET search
  - _Requirements: 2_

- [x] 6. Lambda B — Trip Assembly
  - `backend/assembly/events.py` — append-only trip_events writer +
    trip_drafts projection updater (single transaction per event)
  - `backend/assembly/sequencing.py` — deterministic nearest-neighbor
    day-ordering
  - `backend/assembly/agent.py` — `compose` and `renarrate` LLM steps,
    SSE streaming, debounce handling
  - `backend/assembly/registration.py` — Customer dedupe by phone/email,
    session claim on registration
  - `backend/assembly/notify.py` — advisor notification (fixed email
    config value)
  - `backend/assembly/handler.py` — routes: add/remove component,
    reorder, send-to-advisor
  - _Requirements: 3, 5, 6_

- [x] 7. Frontend
  - `MapView.tsx` — Mapbox, tile-based pins, clustering
  - `FilterChips.tsx` — activity/intensity/season/country, wired to tile
    query params
  - `DestinationPopup.tsx` — hover (200ms debounce) + tap, lists all
    components at a destination with Add/Remove
  - `TripPanel.tsx` — live-updating list, drag-reorder, day badges,
    long-trip warning banner, "Send to advisor" button + registration
    form
  - BFF routes (`app/api/browse`, `app/api/trip`) proxying to Lambda A/B
    — Lambda URLs never exposed client-side
  - _Requirements: 2, 3, 4, 5, 6_

- [x] 8. End-to-end smoke test  (DONE live on prod: Browser -> Vercel BFF -> API GW -> Lambda -> RDS; browse tiles/detail, add/reorder/send-to-advisor, narration all verified. See docs/change-report-mvp-completion.md)
  - Manual run: browse map -> hover a destination with 2+ components ->
    add one -> confirm trip panel updates immediately -> add a second
    component from a different destination -> confirm compose runs once
    after debounce -> drag-reorder -> confirm renarrate runs, components
    unchanged -> register at send-to-advisor -> confirm notification
    fires to the configured address -> edit trip again -> confirm it's
    still editable and re-send works
  - Confirm every component surfaced on the map traces back to a real,
    active tour in `published_tours`

## Post-MVP completion cycle (2026-09-13) — see docs/change-report-mvp-completion.md

- [x] 9. UI redesign — AA-branded TripAdvisor-style light theme (gold/ink/
  offwhite, Inter). Header + restyled FilterChips/MapView/DestinationPopup/
  TripPanel. Hook contracts unchanged. Deployed to Vercel prod.
- [x] 10. Embedding backfill — `backend/extraction/backfill_embeddings.py`
  fills the NULL embeddings the deterministic seeder left, so semantic
  search returns results. Idempotent one-off (Cohere Embed v4). All 1081
  components embedded.
- [x] 11. Narration route — `POST /trip/{trip_id}/narrate` (compose/
  renarrate) wired into assembly handler + BFF (`op=narrate`) + TripPanel
  (Compose/Regenerate). Buffered Bedrock invoke (not streaming). Verified
  live.
- [x] 12. Edge shared-secret auth — both Lambdas verify `X-TripPlanner-Key`
  (`backend/shared/auth.py`); BFF sends it; Terraform secret
  `tripplanner/dev/api-key` + Lambda env + Vercel env. Verified: direct
  call -> 401, BFF -> 200.
- [x] 13. Semantic-search UI wiring — search box debounces into the
  semantic `/browse/search` endpoint; map shows ranked results, clear
  returns to browse.
- [x] 14. Fix compose model id -> `global.anthropic.claude-sonnet-4-6`
  (the `us.` profile is not in the satellite invoker roles' policy).

## Deferred (tracked, NOT done)
- Auto-refresh: new tours in `acp_contract.tour_atoms` do NOT flow into
  `tripplanner.itinerary_components` automatically — extraction is manual.
  Needs a scheduled/triggered re-run + idempotent `run.py`.
- Real advisor email (SES + verified domain) — currently a logging stub.
- CloudFront in front of Browse read routes.
- Mapbox token URL-restriction.
- Fix minority of mis-geocoded destinations (Mapbox limit=1 wrong-place).
- Rotate the exposed dev RDS admin password.

## Explicitly NOT Kiro tasks (human / Terraform gate)
- New Lambda resources + IAM trust policy additions in `AA-CIS-Infra`
  (acc3-bedrock, acc1-bedrock) — Nghiep applies manually via Terraform
- New CloudFront distribution — Terraform, human-applied
- Vercel project creation, domain setup
- Coordinating the `shared.destinations` migration with any concurrent
  AA-CIS-App schema work — flag for Nghiep to check before applying
  migration 002
- Merge to main — human-only, per standing program rule. Open a PR after
  each task instead
