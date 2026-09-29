# AA-TripPlanner-Web — Repo Context

B2C map-based trip planner in the Adventure Asia (AA) ecosystem. An end traveler explores
destinations on an interactive map, assembles a personal multi-day trip, and gets AI-assisted
suggestions and narrative. This repo is a self-contained product: its own frontend, its own
serverless backend, and its own database schema — it does **not** share application code with
AA-CIS-App. What it shares with the rest of the ecosystem is a small set of AWS-level resources
(Bedrock access, OIDC deploy roles, the destinations reference data) provisioned by AA-CIS-Infra.

> Scope of this file: describe the whole repo accurately — what it is, how it is laid out, how it
> runs, and where its boundary with the rest of the AA ecosystem sits. Deep per-subsystem design
> notes belong in their own doc next to the code, not here.

## What it is (role in the AA ecosystem)

- **Product surface**: consumer-facing (B2C). The visitor is a traveler planning a trip, not an
  AA admin or a marketplace tenant. This is the opposite audience from AA-CIS-App (admin +
  tenant content pipeline).
- **Position**: a leaf product. It reads shared reference data (`shared.destinations`) that the
  CIS side owns, and it calls Bedrock for AI assembly. Nothing in the CIS content pipeline
  depends on TripPlanner — the arrow points one way (TripPlanner → shared/Bedrock), never back.
- **Ownership split**: this repo owns its application code and its own schema (`tripplanner.*`).
  AWS resources it needs (Lambda plumbing, OIDC deploy role, cross-account Bedrock trust) are
  owned by **AA-CIS-Infra**, consistent with the ecosystem rule "App owns code, Infra owns
  resources."

## Architecture at a glance

```text
Traveler (browser)
      │
      ▼
Next.js 14 frontend  ──(Mapbox GL)──►  interactive map UI
      │  same-origin /api/* (BFF, server-side; holds the TripPlanner API key)
      ▼
API Gateway HTTP v2  (ANY /browse/{proxy+}, ANY /trip/{proxy+}; edge shared secret
      │               X-TripPlanner-Key, no per-user auth yet)
      ▼
Two Python 3.12 Lambda functions
  ├─ browse      → stateless, cacheable reads (destinations, suggestions); embeds search text
  └─ assembly    → stateful writes (trip mutations) + Bedrock (AI narrative/plan)
      │
      ▼
Postgres (the shared RDS in acc2)
  ├─ tripplanner.*        (owned here — itinerary_components, customers, sessions,
  │                        trip_events, trip_drafts; pgvector)
  ├─ shared.destinations  (shared reference data, geocoded lat/lng)
  └─ read-only: acp_contract.tour_atoms, gold_aa_internal.published_tours (CIS-owned)
      │
      ▼
Amazon Bedrock  — Cohere Embed v4 called directly in acc2;
                  Claude via cross-account role (acc3 primary → acc1 fallback)
```

The journey ends with a **draft trip + customer** sent to an advisor ("Send to advisor" +
sign-up), not a completed booking — booking belongs to the future AA-Booking (AAA) product
(see `docs/tripplanner-to-aaa-handoff.md` in the root docs repo).

### Frontend

- **Next.js 14.2.15** (App Router), deployed on **Vercel**.
- **Mapbox GL** for the interactive map (destination pins, trip routing, day-by-day view).
- Talks to the backend Lambdas over HTTP; no direct DB access from the browser.
- Deploy is Vercel-driven: a PR shows a Vercel preview and CI status; merge to the production
  branch triggers the production deploy. Check the Vercel CI check on the PR for deploy success.

### Backend — two Lambdas, split by statefulness on purpose

- **browse Lambda** — read-only, **stateless and cacheable**. Serves destination browsing and
  suggestion queries. Because it never mutates trip state, its responses can be cached at the
  edge/CDN layer without correctness risk. Keep new read-only endpoints here. Its one model
  call is the embedding for free-text search (`/browse/search`, not cached).
- **assembly Lambda** — **stateful**, owns all trip mutations, and is the only side that calls
  **Claude**. Building/AI-narrating a trip is a write that also incurs model cost, so it is
  deliberately isolated from the cacheable read path. Keep anything that writes trip state or
  calls a text model here.
- Runtime: **Python 3.12**. The two functions are separate deploy units so the cacheable read
  path and the expensive stateful path scale and cache independently.

### Data model — event-sourced trips

- A trip is stored as an **append-only event stream** rather than a single mutable row. Each user
  action (add destination, reorder day, remove stop) is an event; the current trip state is the
  fold over its events. This gives a natural history/undo and makes concurrent edits reconcilable.
- **Taste vector**: a per-traveler preference signal built from their interactions, combined with
  **geographic** proximity to drive suggestions ("you liked X, and Y is nearby and similar").
  Since AA-590 (PR #46) "suggest next place" re-ranks by 0.6 taste (pgvector) + 0.4 distance to
  the trip's last stop (absolute km, ramp 150-1500 km) in `backend/assembly/suggestions.py`;
  with no anchor stop it falls back to taste order only. Since AA-674 (step 2) the candidates are
  **route-constrained**: what real tours do next from the trip's last stop. That means the
  components of the days after any `tour_stop` within 150 km of it (same place, or a transfer to
  another tour), up to that tour's next stop elsewhere. The response carries `mode`
  (`route` | `taste`) and each suggestion a `tour_count`. When no tour passes near the last stop,
  it falls back to taste within the trip's countries.
- **Planner on AA routes (AA-674, Jira PR-11)**:
  - The trip panel starts with a route planner: "Heading to" + days → proposals from real AA
    tours (`/browse/routes`), previewed on the map. "Start with this route" pins them
    (`POST /trip/{id}/apply-route`).
  - A coverage block shows the AA tours the trip is built from (`/trip/{id}/coverage`: legs,
    transfers, gaps with a reason).
  - Each day card lists "Activities here" (`/trip/{id}/stops/{dest}/activities`); checking one
    pins its component.
  - Days can be added (extra night, free day, extra day at the end; events `add_day` /
    `remove_day`, migration 006) and are shown as the traveller's additions, not part of a tour.
- **Map extras (AA-589, PR #46)**: arrival/departure gateways are drawn on the map (airport
  marker + dashed arc to the first/last stop; `ALL_GATEWAYS` / `nearestGateway` in
  `frontend/lib/mapbox.ts`), and hovering a country in the country picker previews its highlight
  (custom listbox — a native `<option>` does not fire hover events). The gateway table covers
  only 6 countries so far (Laos, Sri Lanka, South Korea, Nepal, Japan, India); other countries
  snap to the nearest listed gateway.
- Schema ownership: everything under `tripplanner.*` is owned by this repo's migrations.
  `shared.destinations` is **read-shared** reference data owned by the CIS side — treat it as
  read-only from here; do not migrate or mutate it from this repo.

### AI (Bedrock)

- **Every model call goes through the ecosystem Model Gateway** (AA-685,
  `backend/shared/llm_gateway.py`). The model, fallback order and price for each stage come
  from the gateway tables that AA-CIS-App owns in the shared RDS (`shared.llm_role_config`,
  `shared.llm_model_catalog`). Every successful call writes one `shared.llm_call_log` row,
  tagged `quality_signal.app = "tripplanner"`, so it shows on the CIS External Spend page. The
  `tripplanner` DB role has SELECT on the two config tables and INSERT on the log (CIS
  migration 173). If the tables cannot be read, built-in routes keep the previous models.
  Stages:
  - `tp_compose`: assembly narration (Claude Sonnet 4.6 via the satellite role);
  - `tp_search_embed`: browse free-text search, one Cohere Embed v4 call per query (acc2);
  - `tp_extract` / `tp_component_embed`: the offline extraction pipeline.

  An admin can change a stage's model on the CIS admin Settings page (TripPlanner group) without
  a TripPlanner deploy; the Lambdas pick the change up within 60 s.
- Cross-account Claude access follows the ecosystem Bedrock routing: **acc3 primary → acc1
  fallback** (the same accounts the CIS side uses; see `docs/ecosystem-architecture.md`).
  Cohere embeddings run directly on acc2 (no satellite).
- **`tour_day` (AA-675, migration 003)**: one row per (published tour, itinerary day): start,
  end and **overnight place** (geocoded into `shared.destinations`), plus other places. Built from
  `published_tours.aa_itineraries` by rules + one `tp_extract` call per tour, with every model
  place grounded in that day's text (`backend/extraction/tour_days.py`). It runs inside the
  assembly Lambda through a direct `lambda invoke` (`{"extraction": {"op": "list" | "tour_days" |
  "prune"}}`, not reachable over HTTP), driven by `backend/extraction/invoke_tour_days.py`.
  Unchanged tours are skipped (itinerary hash). It is the base of the Tour Graph (AA-673).
  A night is linked through its **anchor** (the overnight place, or the day's end place when the
  overnight is a hotel/lodge/camp; none in transit), located by `backend/extraction/locate.py`:
  the model proposes coordinates, then Mapbox confirms within 25 km (Mapbox name search alone
  misplaces Asian names). Op `"relink"` re-locates every anchor.
- **Tour Graph (AA-673, migration 004)**: read model rebuilt in full from `tour_day` by extraction
  op `"tour_graph"` (`backend/extraction/tour_graph.py`, no model call; `invoke_tour_days.py` runs
  it after extracting). `tour_stop` = where each (tour, day) is (overnight destination, else the
  day's main component destination); `tour_graph_node` = destinations with tours, activities,
  intensity, seasons; `tour_graph_edge` = A→B steps of real tours, weighted by tour count;
  `tour_leg` = every multi-day span of one tour (the sellable unit, with countries and
  activities); `tour_junction` = destination pairs ≤ 150 km (tunable, both directions,
  `cross_border` flagged) where legs of different tours chain; `tour_graph_build` = params + stats
  per rebuild. Op `"neighbours"` is the sample "where next, still coverable by tours" query.
  Route-constrained suggestions and multi-tour composition read it (AA-674).
- `itinerary_components` is built by an offline extraction pipeline from CIS published tours and
  atoms. It is not refreshed automatically when new tours are published; the CIS data reset of
  16/09/2026 means it must be rebuilt after the CIS rerun (tracked in CIS Linear AA-600).
- The trust and role wiring that make this call possible live in **AA-CIS-Infra**, not here.

## Deploy & CI

- **Frontend**: Vercel (preview per PR, production on merge to the production branch).
- **Backend Lambdas**: deployed via GitHub Actions assuming the OIDC role
  `aa-tripplanner-dev-app-deploy` (an Infra-owned IAM role). The role's trust is scoped to this
  repo through the org OIDC subject
  `repo:AdventureAsia365-Ecosys*/AA-TripPlanner-Web*:*` — see AA-CIS-Infra's `tripplanner.tf`.
- Because the org has "include repo ID in the OIDC subject" enabled, the trust uses the
  `AdventureAsia365-Ecosys*/...` wildcard form; a plain `repo:Ecosys/...` subject fails
  AccessDenied. This is the same OIDC fact that AA-588 corrected in Infra.

## Boundaries — what this repo does NOT do

- It does not run the content pipeline (Atom/Segment/Route/Slate/Piece/publish) — that is
  AA-CIS-App's domain entirely.
- It does not own the `shared` schema (CIS does). It does write into `shared.destinations`, which
  it created (migration 002): the extraction inserts newly located places and corrects their
  coordinates, and migration 005 added `located_by` / `located_at`. It must not change other
  `shared` tables.
- It does not provision its own AWS resources or OIDC roles — those live in AA-CIS-Infra.
- **Agents must not merge to the production branch directly.** Ship via PR and let the human
  merge (matches this repo's stated policy and the ecosystem program rules). This repo has no
  branch protection like AA-CIS-App; a squash merge works as soon as CI is green.

## Known gaps (deferred)

Automatic re-extraction when new tours are published; real advisor email (currently a stub);
CloudFront in front of browse; Mapbox token URL restriction; some wrong geocode points; more
countries in the gateway table.

## Related documents

- `README.md` (this repo) — setup/run instructions.
- `docs/ecosystem-architecture.md` (root docs repo) — the ecosystem-wide account map, Bedrock
  routing, OIDC design, and how TripPlanner sits next to AA-CIS-App / AA-CIS-Infra.
- `AA-CIS-Infra/tripplanner.tf` — the OIDC deploy role and any TripPlanner-specific AWS resources.
