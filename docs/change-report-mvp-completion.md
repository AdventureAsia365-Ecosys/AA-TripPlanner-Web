# Change Report — MVP completion cycle (2026-09-13)

What was added/changed **beyond the original spec build**, why, and how it
was verified. Scope agreed with the product owner: UI redesign + fill the
gaps that don't need external resources. Deferred: real advisor email
(SES), CloudFront, Mapbox token URL-restriction, auto-refresh of new tours.

The starting point was: full backend live end-to-end on prod
(Browser → Vercel BFF → API Gateway → Lambda → RDS), UAT green, but with
three known gaps — empty semantic search, no reachable narration route,
NONE auth at the edge — plus an old placeholder UI.

---

## 1. UI redesign (AA-branded, TripAdvisor-style)

**What:** Replaced the placeholder UI with an AA-branded light theme.
- Brand tokens (gold `#DB9628`, ink `#1F2933`, off-white, Inter) in
  `globals.css` + `tailwind.config.ts`; Inter via `next/font` in
  `layout.tsx`.
- New `Header.tsx` (wordmark bar). `page.tsx` shell: header + map +
  25rem trip rail.
- Restyled `FilterChips` (floating Discover card), `MapView` markers
  (gold clusters / ink pins), `DestinationPopup` (activity pills, gold
  Add), `TripPanel` (gold day badges, drag-reorder, gold CTA).

**Contracts:** `useTrip()` hook contract preserved; no backend change.

**Verified:** `next lint` + `next build` (typecheck) clean; deployed to
Vercel prod; live HTML shows the new wordmark.

## 2. Embedding backfill (semantic search data gap)

**Why:** The deterministic seeder inserted 1081 `itinerary_components`
with `embedding = NULL`. `browse/search.py` filters on
`embedding IS NOT NULL`, so search always returned `[]`. Data gap, not a
code defect.

**What:** Added `backend/extraction/backfill_embeddings.py` — idempotent
one-off that embeds each component's `text_extract` with Cohere Embed v4
(`us.cohere.embed-v4:0`, us-west-1, 1536-dim — same model the query path
uses) and writes the vector via a `$2::vector` text literal. Only touches
`embedding IS NULL` rows and commits per row, so it is safe to re-run if
the DB tunnel drops.

**Verified:** ran over the cis-tunnel to fill all 1081; live search
(`/browse/search?q=sunrise mountain trek`) returns Himalayan trekking
spots ranked first (Deorali, Poonhill, Annapurna Base Camp…).

## 3. Narration route (compose / renarrate reachable)

**Why:** `agent.compose/renarrate` existed but no HTTP route reached them.

**What:**
- New route `POST /trip/{trip_id}/narrate` in `assembly/handler.py`
  (`mode` = compose | renarrate; empty trip → 400, bad mode → 400, no
  session → 400, Bedrock failure → 502). Narrator is injectable for tests.
- `events.current_itinerary()` — read-only projection that includes
  `text_extract` (the narration prompt needs it).
- BFF `op=narrate`, `api.narrate()`, `useTrip.narrate()`, and a
  Compose/Regenerate button + narration display in `TripPanel`.
- **Switched narration to buffered `invoke()`** (not streaming): the route
  buffers the full text to JSON anyway, and this avoids depending on
  `InvokeModelWithResponseStream`.

**Verified:** live BFF `op=narrate` → 200 with real Claude narration.

## 4. Edge shared-secret auth

**Why:** API Gateway was `authorization = NONE`; anyone with the URL could
call the Lambdas.

**What:**
- `backend/shared/auth.py` — both Lambdas verify `X-TripPlanner-Key`
  (constant-time compare). Disabled when the key env is empty (local/tests
  unaffected).
- BFF sends the header from a server-side env var (`browse` + `trip`
  routes).
- Terraform (`tripplanner.tf`): `random_password` → secret
  `tripplanner/dev/api-key` → `TRIPPLANNER_API_KEY` env on both Lambdas.
  Vercel env set to the same value.

**Verified:** live direct API GW call without header → **401**; with the
header → **200**; full BFF chain → 200.

## 5. Semantic-search UI wiring

**Why:** The search box was bound to the `country` filter (client-side),
never calling the semantic `/browse/search` endpoint.

**What:** Search box now debounces (400ms) into `runSearch()` →
`api.search()`; `MapView` shows the ranked result set + fits bounds when
search is active, and returns to tiles-by-bounds when cleared. Country
moved to its own filter input.

**Verified:** build clean; live BFF search returns semantically-ranked
results (e.g. "street food night market" → Night Market, Seoul, local
markets).

---

## 6. Bug/config fixes made along the way

- **Compose model id**: `us.anthropic.claude-sonnet-4-6` →
  `global.anthropic.claude-sonnet-4-6`. The satellite invoker roles'
  `InvokeApprovedClaudeModelsOnly` policy only allows the `global.`
  profile — the `us.` id returned AccessDenied. Fixed in both app config
  and the Lambda env (Terraform).

---

## 7. Infra applied (AA-CIS-Infra, dev)

PR #58 (merged, applied to dev; plan: 3 add / 4 change / 0 destroy):
- `random_password.tripplanner_api_key` + secret `tripplanner/dev/api-key`
  + version.
- `TRIPPLANNER_API_KEY` env on both Lambdas.
- `BEDROCK_MODEL_COMPOSE` → `global.anthropic.claude-sonnet-4-6`.
- `hashicorp/random` provider added.

No AA-CIS resource modified; only `accounts/aa365/tripplanner.tf` +
`versions.tf` + lock.

## 8. Tests + final UAT

Backend: 59 pytest pass (was 49) — added 5 narration route tests + 5 auth
tests. Frontend: lint + build/typecheck clean.

Final live UAT (via `https://aa-tripplanner.vercel.app`, full chain
Browser → BFF → API GW w/ shared-secret → Lambda → RDS/Bedrock):
- Browse tiles 28_83 → 200, 34 destinations.
- Semantic search "temple sunrise" → 60 ranked results (temple, Temple of
  the Tooth Relic, Poonhill, Kataragama Temple…); "sunrise mountain trek"
  → Himalayan spots first.
- Trip: add ×2 → 200; narrate → compose, 2 days, real Claude narration;
  send without registration → 422 (gate enforced).
- Auth boundary: direct API GW call without `X-TripPlanner-Key` → 401.
- Embeddings: 1081/1081 populated, 0 NULL.

## 9. App repo state

Branch `fix/assembly-uuid-json`, PR #27 open (app repo is **human-merge**
per program rule; code was deployed for testing via `workflow_dispatch` on
the branch, not by merging). Commits: UI redesign, narration route,
edge shared-secret, buffered invoke, global compose model, semantic-search
wiring.

## 10. Deferred (tracked, not done this cycle)

- Auto-refresh of new tours into TripPlanner (scheduled extraction +
  idempotent `run.py`).
- Real advisor email (SES + verified domain).
- CloudFront in front of Browse read routes.
- Mapbox token URL-restriction.
- Fix the minority of mis-geocoded destinations.
- Rotate the exposed dev RDS admin password.
