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
      │               X-TripPlanner-Key; guest token on trip mutations, AA-827)
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
  └─ read-only: acp_contract.v_active_tour_atoms, gold_aa_internal.published_tours (CIS-owned)
      │
      ▼
Amazon Bedrock  — Cohere Embed v4: acc2 first, then acc3/acc1 via the satellite role (AA-862);
                  Claude via cross-account role (acc3 primary → acc1 fallback)
```

The journey ends with a **draft trip + customer** sent to an advisor ("Send to advisor" +
sign-up), not a completed booking — booking belongs to the future AA-Booking (AAA) product
(see `docs/tripplanner-to-aaa-handoff.md` in the root docs repo).
TripPlanner sends no e-mail: the staff "new inquiry" e-mail is sent by AA-Booking when it processes the
`inquiry.submitted` outbox row (job `bk_notify_email`, AA-811); `backend/assembly/notify.py` only logs the hand-off.

### Frontend

- **Next.js 14.2.15** (App Router), deployed on **Vercel**.
- **Mapbox GL** for the interactive map (destination pins, trip routing, day-by-day view).
- Talks to the backend Lambdas over HTTP; no direct DB access from the browser.
- Deploy is CI-driven (S241): `.github/workflows/vercel-deploy.yml` runs `vercel deploy` when a PR
  or a push to `main` touches `frontend/**` — PR → preview (URL in one PR comment), `main` →
  production. The Vercel Git integration is off (`frontend/vercel.json`, `git.deploymentEnabled:
  false`) because the Hobby team cap (~100 deployments / 24 h, all projects) was being used up.
  Needs the repo secret `VERCEL_TOKEN`. Check the "Vercel deploy" job on the PR for deploy success.

### Pages (AA-812)

- `/` search-first home (destination, days, travellers → `/plan?country=&days=&travellers=`),
  featured tours, destinations grid, how it works, trust strip.
- `/plan` the map planner (moved from `/`), with a Map | List-of-tours toggle; `?tour=<tour_id>`
  pins that tour's whole route through the existing apply-route flow (`&ask=1` opens the trip).
- `/tours/[slug]` and `/destinations/[country]`: Server Components with ISR (30 min, the browse
  CDN TTL), JSON-LD (`TouristTrip`, `TouristDestination`), `sitemap.xml`, `robots.txt`.
- Kit pages use `frontend/ui/` (tokens copied from the CIS kit with the same values, light +
  dark via `<html data-theme>`, localStorage `tp_theme`); the planner keeps its light `aa-*` palette.

### Backend — two Lambdas, split by statefulness on purpose

- **browse Lambda** — read-only, **stateless and cacheable**. Serves destination browsing and
  suggestion queries. Because it never mutates trip state, its responses can be cached at the
  edge/CDN layer without correctness risk. Keep new read-only endpoints here. Since AA-812 it
  also serves the public catalog under `/browse/catalog/*` (tours, tour by slug, countries,
  country page, sitemap), reading **only** the CIS catalog views (`catalog.v_*`, ADR 0001), and
  `/browse/tour-route` (one tour's whole route for "Customise this trip": the Tour Graph under the
  current or the linked tour_id, else built from the tour's catalog places onto planner experiences
  at those places, `match` = `tour_graph` | `places`; a 404 carries the tour's title and day list).
  Map tiles (`/browse/tiles/{lat}_{lng}[_{size}]`) are fixed-grid cells of 1–90 degrees; the map
  (`frontend/lib/tileLoader.ts`) picks the finest grid that covers the view in ≤ 16 cells, caches
  them, runs ≤ 4 requests at a time and has a request budget (AA-812 hotfix). Its one model
  call is the embedding for free-text search (`/browse/search`; the response is no-store, the query
  embedding is cached in the container and a failed embed falls back to keyword ranking, AA-856).
- **assembly Lambda** — **stateful**, owns all trip mutations, and is the only side that calls
  **Claude**. Building/AI-narrating a trip is a write that also incurs model cost, so it is
  deliberately isolated from the cacheable read path. Keep anything that writes trip state or
  calls a text model here.
- Runtime: **Python 3.12**. The two functions are separate deploy units so the cacheable read
  path and the expensive stateful path scale and cache independently.

### Guest identity (AA-827)

- **Rule: `session_id` is an identifier, never a credential; every trip mutation goes through
  `_guard`** (`backend/assembly/handler.py`). The credential is a random guest token (32 bytes,
  base64url, 43 chars) that the BFF keeps in an HttpOnly cookie (`__Host-tp_guest` in production,
  `tp_guest` under local http; Secure, SameSite=Lax, Path=/). **Lifetime rule:** Max-Age 400 days
  (the browser cap), sliding — every matched request re-sends it with a fresh Max-Age; nothing else
  expires a guest (`sessions.expires_at` is informational, not enforced). `frontend/middleware.ts`
  mints it on `/plan` and on `/api/trip` only (the home, destination and tour pages are CDN-cached, so a
  per-visitor Set-Cookie must never ride on them), as a
  raw `Set-Cookie` header — never `res.cookies.set`, which also echoes the value into an
  `x-middleware-set-cookie` response header that client JS can read — and refuses a cross-site POST
  (403) before any mint, so another site can never overwrite the cookie. Client JS never sees it. The
  browser keeps only `{sessionId, tripId}` (v4 UUIDs) in localStorage.
- The `/api/trip` BFF forwards the token on POST only, in `x-tripplanner-guest`; it refuses a
  cross-origin POST (403) and never logs. `tripplanner.sessions.guest_token_hash` stores sha256(token)
  only (`bytea`, 32 bytes, migration tp-011); the plaintext `guest_token` (= id::text before AA-827) is
  no longer written, tp-012 nulls the legacy values and `sessions_guest_token_retired_chk`
  (`CHECK (guest_token IS NULL)`) keeps it empty, so a pre-AA-827 Lambda artifact can no longer insert
  a session (rollback = revert PR). DROP COLUMN is a later contract file. One cookie can bind several sessions, so the hash has no unique index.
- `_guard` runs on all 8 mutations (add, remove, reorder, send-to-advisor, narrate, apply-route, add /
  remove day): 401 `guest_session_invalid` when the token does not match the session
  (`assembly/guest_session.py`), 403 `forbidden` when the session does not own the trip
  (`trip_drafts.session_id`; a trip with no draft yet has no owner). It also creates the sessions row
  on first use (`events.append_event` no longer does). The frontend (`lib/api.ts` `postTrip`) forgets
  its ids (only if localStorage still holds the failing ones) and reloads with a new trip on either
  answer; a second reset in the same tab within 60 s does not reload.
- **No cookie is not "another browser".** When a POST came without a cookie, the BFF uses the token
  the middleware minted for it; if the Lambda refuses that token, the BFF answers 401
  `cookies_required` instead of `guest_session_invalid`. The client retries once (a cookie lost once is
  now replaced) and, on a second `cookies_required`, keeps its ids and says cookies are needed. A
  browser that blocks the cookie can make the first edit of a trip only.
- Known gap: two planner tabs opened at the same moment by a first-time visitor (no cookie yet) can
  each get a different token; the later Set-Cookie wins, and a session already bound to the earlier
  one is reset on its next edit. Minting on the `/plan` document makes this rare, not impossible.
- Legacy grace window `config.GUEST_LEGACY_GRACE_UNTIL` is **closed** (code constant 16/10/2026 00:00
  UTC, already past; S239: Dev has no real guests, so the planned 19/10 cutoff was not waited for). A
  session id alone, a tokenless request and a pre-AA-827 session (no hash) all get 401. The grace code
  path (bind a legacy session to its first token, accept tokenless for an unhashed session) is kept in
  `assembly/guest_session.py` but dormant; reopening it means moving the constant into the future in
  a reviewed deploy.
- `trip_id` / `session_id` must be canonical UUIDs (400 otherwise, value not echoed); an unexpected
  error answers 500 and logs only its type, place and route name. Each guarded request prints one
  `{"evt":"guest_auth","outcome":…,"route":…}` line (no token, hash or ids).
- GET reads (trip, suggestions, coverage, activities) are not gated yet (follow-up).

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
- Schema ownership: everything under `tripplanner.*` is owned by this repo's migrations
  (ecosystem rules: `AA-Ecosys/docs/database/schema-ownership.md`). Every migration registers itself
  in `shared.schema_versions` with the `tp-NNN` key (from tp-007, which also backfills tp-001..006
  for objects that exist) and is applied through AA-CIS-Infra **DB Migrate** from an identical copy
  in `db/migrations/tripplanner/`.
  `shared.destinations` is **read-shared** reference data owned by the CIS side — treat it as
  read-only from here; do not migrate or mutate it from this repo.
- **Only CIS writes content** (root ADR 0002, 01/10/2026). This repo writes only `tripplanner.*`
  user state (sessions, drafts, customers, trip events) and `shared.llm_call_log`. The DB role
  `tripplanner` has no INSERT/UPDATE on `shared.destinations` any more (CIS migration 201).
  **The place pipeline (itinerary places, geocoding, tour graph) is CIS's** (root ADR 0007,
  AA-712: CIS job kinds `place_extract` / `place_geocode` / `place_graph`, schema `geo`). Since
  slice 6a this repo has **no place writers**: the extraction modules and scripts were removed,
  and the guard test `backend/tests/test_extraction_ops.py` fails if any module under `backend/`
  writes `shared.destinations` or the tripplanner place tables (`itinerary_components`, `tour_day`,
  `tour_stop`, `tour_graph_*`, `tour_leg`, `tour_junction`). Those tables are **frozen** (no longer
  refreshed). Since slice 6b the **browse** readers (tiles, countries, by-country, destination detail,
  search, routes, tour-route) read either them (`PLACES_SOURCE=legacy`, the default) or the CIS catalog
  views of CIS migration 228 (`PLACES_SOURCE=cis`: `catalog.v_tour_component`, `v_tour_stop`,
  `v_tour_leg`, `v_tour_junction`, `v_destination`, contract `catalog.graph.v1`; keyed by catalog
  `source_id`, live tours only, no AA tenant needed; `backend/shared/places_source.py`). Every browse
  response carries `x-places-source`. The **assembly** readers (`events._current_components`,
  suggestions, coverage, activities, apply-route, handoff `leg_chain`) stay on the frozen tables until
  slice 6c, which ships with the tp-013 component id-map; the views' component ids are new stable
  uuid5 ids, so `cis` is set on both Lambdas together, in that window, not before.
- Photos (AA-708, 01/10/2026): AA marketing photos are synced and matched by CIS (`photo_sync`,
  `shared.place_photo`). This repo reads them only through `shared.v_destination_photos` /
  `shared.v_tour_photos` and `shared.destinations.cover_image_url` (set by CIS from the photo
  matched to a place on the tour's own itinerary). Image URL = CIS public API base + view `path`
  + `?size=large|small`. Cover matching for a country runs after this repo re-extracts its tours'
  places (`itinerary_components` / `tour_stop`) — see `docs/ecosystem-architecture.md` §4.5.

### AI (Bedrock)

- **Every model call goes through the ecosystem Model Gateway** (AA-685,
  `backend/shared/llm_gateway.py`). The model, fallback order and price for each stage come
  from the gateway tables that AA-CIS-App owns in the shared RDS (`shared.llm_role_config`,
  `shared.llm_model_catalog`). Every call attempt writes one `shared.llm_call_log` row (failed
  attempts too, with `stop_reason = 'error'`; a model with no catalog price logs `cost_usd` NULL),
  tagged `quality_signal.app = "tripplanner"`, so it shows on the CIS External Spend page. The
  `tripplanner` DB role has SELECT on the two config tables and INSERT on the log (CIS
  migration 173). If the tables cannot be read, built-in routes keep the previous models.
  Stages:
  - `tp_compose`: assembly narration (Claude Sonnet 4.6 via the satellite role);
  - `tp_search_embed`: browse free-text search, one Cohere Embed v4 call per distinct query (acc2
    first; on an acc2 throttle the route falls through to the satellite rows `cohere-embed-v4-acc3`,
    then `cohere-embed-v4-acc1` once enabled — same model and 1536-d, AA-862).
    **Search under load (AA-856)**: Cohere Embed V4 on acc2 allows 20 requests per minute (Service
    Quotas), so `llm_gateway.query_embed_cache` keeps query vectors per Lambda container (bounded LRU,
    1024 entries, 6 h TTL; key = stage + base Bedrock model id(s) of the route (geo prefix stripped, so account rows of one
    model share entries) + input type + normalised text; a hit makes no
    call, no log row, no spend, and is served even over the daily cap). When the embed fails (every
    account of the route failed: throttle or any Bedrock error; failed attempts still logged) search answers 200 with a keyword ranking
    (`ILIKE` per query word over destination/component name, country and text, same filters and
    active-tour rule; no pg_trgm on the RDS) and `"ranking": "keyword"` instead of a 500; when any
    attempt of the failed chain was a throttle the container skips the model for 10 s. A query with no letter or digit (empty,
    punctuation, zero-width characters) answers `{"destinations": []}` with no model call. The acc2
    and satellite embed clients make one attempt each (no boto retry); the satellite AssumeRole uses
    STS timeouts of 3 s connect / 5 s read, at most 2 attempts. Each search prints one `{"evt":"search_embed","outcome":hit|miss|fallback,...}` line
    with the container's cache counters (never the query text);
  - `tp_extract` / `tp_component_embed`: the former offline extraction pipeline; no caller since
    AA-712 slice 6a (built-in routes kept until the frozen tables are dropped).

  An admin can change a stage's model on the CIS admin Settings page (TripPlanner group) without
  a TripPlanner deploy; the Lambdas pick the change up within 60 s. Shadow config on a tp_* stage
  is not run here (a warning is printed): a consumer app does not pay for shadows.
- **AI guards (AA-805, migration tp-010)**: a daily hard stop on **customer-facing** model spend
  only — stages `tp_compose` + `tp_search_embed` (`llm_gateway.ONLINE_STAGES`,
  `TP_DAILY_SPEND_CAP_USD`, counted in `tripplanner.llm_spend_daily` by the gateway — the role
  cannot read `shared.llm_call_log`). Offline stages (`tp_extract`, `tp_component_embed`) are
  logged but neither counted nor capped here, so a backfill never takes search/narration down.
  Over the cap narration falls back to AA text + neutral day lines and search answers 503.
  `/narrate` is limited to the session that created the trip and reuses
  `tripplanner.narration_cache` for an identical itinerary (the key includes the AA master day
  text, so CIS edits miss the cache). Search embeds the query as `search_query` and ranks only
  components of active, not soft-deleted tours.
- **AI labels (AA-819, AUP 2026-11)**: every AI-written text block a traveller sees carries a label
  (`components/AiLabel.tsx`, a button with an aria-expanded explanation, not a `title` tooltip):
  the site pill, the tour page summary / highlights / day by day, the map popup and stop activity
  texts ("Adventure Asia AI", "AA AI" below the sm breakpoint — brand name chosen by Nghiệp S239: CIS writer-model output, published when it passes CIS's automated checks;
  only flagged tours reach a human, so no label claims a team review of every text), and the
  narration (`master` and `master_basic` = "AA itinerary · Adventure Asia AI", the same CIS text;
  `mixed` = "AA itinerary + Adventure Asia AI"; `llm` = "Adventure Asia AI narration"; `basic` = neutral place lines only, no label). The
  narrate API's `ai_generated` is true for every source but `basic`. Place search and "You might
  also like" suggestions both rank by Cohere embeddings (suggestions: route-constrained, ranked by
  the pinned components' taste vector), and the site pill names both.
  Narration day choice (`master_content.resolve_days`), because a component row's tour day can be
  wrong: per trip day, the own tour day text of any entry whose text names its stop (component or
  place name, accent-insensitive, generic suffixes such as "Temple" dropped); else the nearest other
  day of the same tour that names it and that no other entry of the itinerary owns or already took;
  else the own day text as it is; only a day with no AA text goes to the LLM.
- Cross-account Claude access follows the ecosystem Bedrock routing: **acc3 primary → acc1
  fallback** (the same accounts the CIS side uses; see `docs/ecosystem-architecture.md`).
  Cohere embeddings run on acc2 first; the route's satellite rows (`cohere-embed-v4-acc3`, later
  `-acc1`, CIS migration 240) take over on an acc2 throttle (acc2 has 20 rpm, acc3 2,000 rpm). Each
  container caches one no-retry satellite embed client per account (one AssumeRole until 5 minutes
  before the creds expire), AA-862.
- **`tour_day` (AA-675, migration 003)**: one row per (published tour, itinerary day): start,
  end and **overnight place** (a `shared.destinations` row), plus other places. Built from
  `published_tours.aa_itineraries` by the former TripPlanner extraction (rules + one `tp_extract`
  call per tour), which CIS has ported (`services/places/`); the table is **frozen** since AA-712
  slice 6a. It is the base of the Tour Graph (AA-673).
- **Extraction checks (assembly Lambda, direct `lambda invoke` only, not reachable over HTTP)**:
  `{"extraction": {"op": "list" | "orphans" | "neighbours"}}` (`backend/extraction/lambda_entry.py`,
  `checks.py`) are read-only: `list` = active tours and whether tour_day is stale, `orphans` = rows
  of tours no longer active, `neighbours` = the Tour Graph sample query. The former writer ops
  `tour_days`, `components`, `relink`, `tour_graph`, `prune` answer `status` 410
  "unknown extraction op" (moved to CIS) before opening a DB connection.
- **Tour Graph (AA-673, migration 004)**: read model, frozen since AA-712 slice 6a (it was rebuilt
  in full from `tour_day` by the former op `"tour_graph"`). `tour_stop` = where each (tour, day) is (overnight destination, else the
  day's main component destination); `tour_graph_node` = destinations with tours, activities,
  intensity, seasons; `tour_graph_edge` = A→B steps of real tours, weighted by tour count;
  `tour_leg` = every multi-day span of one tour (the sellable unit, with countries and
  activities); `tour_junction` = destination pairs ≤ 150 km (tunable, both directions,
  `cross_border` flagged) where legs of different tours chain; `tour_graph_build` = params + stats
  per rebuild. Op `"neighbours"` is the sample "where next, still coverable by tours" query.
  Route-constrained suggestions and multi-tour composition read it (AA-674).
- `itinerary_components` was built by the former offline extraction pipeline from CIS published
  tours and atoms. It is frozen since AA-712 slice 6a; CIS builds the replacement
  (`catalog.v_tour_component` over `geo.*`) and the readers switch to it in slices 6b/7.
- The trust and role wiring that make this call possible live in **AA-CIS-Infra**, not here.

## Deploy & CI

- **Frontend**: Vercel, deployed by `.github/workflows/vercel-deploy.yml` (preview per PR that
  touches `frontend/**`, production on such a merge to `main`); Git integration off via
  `frontend/vercel.json`; needs the repo secret `VERCEL_TOKEN`.
- **Backend Lambdas**: deployed via GitHub Actions assuming the OIDC role
  `aa-tripplanner-dev-app-deploy` (an Infra-owned IAM role). The role's trust is scoped to this
  repo through the org OIDC subject
  `repo:AdventureAsia365-Ecosys*/AA-TripPlanner-Web*:*` — see AA-CIS-Infra's `tripplanner.tf`.
- Because the org has "include repo ID in the OIDC subject" enabled, the trust uses the
  `AdventureAsia365-Ecosys*/...` wildcard form; a plain `repo:Ecosys/...` subject fails
  AccessDenied. This is the same OIDC fact that AA-588 corrected in Infra.

## Contracts with other apps

| Other side | This repo reads | This repo writes | Granted by |
|---|---|---|---|
| AA-CIS-App (catalog, AA-812) | `catalog.v_tour`, `v_tour_day`, `v_tour_place`, `v_tour_photo`, `v_destination`, `v_destination_photo` (contract `catalog.tour.v1`); photo URLs = `CIS_PUBLIC_API_BASE` + path | — | CIS migration 225 |
| AA-CIS-App (places / tour graph, AA-712) | with `PLACES_SOURCE=cis`: `catalog.v_tour_component`, `v_tour_stop`, `v_tour_leg`, `v_tour_junction`, `v_destination` (+ `located_by`, `precision`) — contract `catalog.graph.v1` / `catalog.tour.v1.1` | — | CIS migration 228 |
| AA-CIS-App (content) | `gold_aa_internal.published_tours` (tenant RLS, AA tenant set per transaction), `acp_contract.v_active_tour_atoms` (place/activity columns), `shared.destinations`, `shared.v_destination_photos` / `v_tour_photos` | — | CIS migrations 180 / 216, 201 |
| AA-CIS-App (Model Gateway) | `shared.llm_role_config`, `shared.llm_model_catalog` | `shared.llm_call_log` (`quality_signal.app = "tripplanner"`) | CIS 173 |
| AA-Booking | — | `tripplanner.outbox` row `inquiry.submitted` v1 / v1.1 / v1.2 (customer + consent, days, leg chain, custom days), written in the same transaction as the `sent` event (AA-676, migration tp-008). AA-Booking reads `tripplanner.v_inquiry_outbox` and keeps its own inbox by `idempotency_key`; it never writes here. Contract: `AA-Ecosys/docs/contracts/inquiry.submitted.v1.2.json` (payload `version` 1.2 since AA-830: each leg also carries the catalog tour key `source_system` + `source_id`, resolved against `catalog.v_tour` at send time, both null when unresolvable; outbox column `schema_version` = major 1) | ecosystem ADR 0001 decision 6 (outbox); migration tp-009 grants role `booking` USAGE on schema `tripplanner` + SELECT on the view only (AA-681) |
| AA-CIS-Infra | — | — | owns the Lambdas, API Gateway, OIDC deploy role, secrets and the DB Migrate workflow that applies `migrations/` |

Schema owners and version keys: `AA-Ecosys/docs/database/schema-ownership.md`.

## Boundaries — what this repo does NOT do

- It does not run the content pipeline (Atom/Segment/Route/Slate/Piece/publish) — that is
  AA-CIS-App's domain entirely.
- It does not own the `shared` schema (CIS does). It created `shared.destinations` (migrations
  002 / 005), but since ADR 0002 / CIS migration 201 the table is CIS-owned and read-only from here;
  the only `shared` table it writes is `shared.llm_call_log`.
- It does not provision its own AWS resources or OIDC roles — those live in AA-CIS-Infra.
- **Agents must not merge to the production branch directly.** Ship via PR and let the human
  merge (matches this repo's stated policy and the ecosystem program rules). This repo has no
  branch protection like AA-CIS-App; a squash merge works as soon as CI is green.

## Known gaps (deferred)

Place tables frozen until the readers switch to the CIS views (AA-712 slices 6b/7; new tours do
not reach the map until then); CloudFront in front of
browse; Mapbox token URL restriction; more countries in the gateway table.

## Related documents

- `README.md` (this repo) — setup/run instructions.
- `docs/ecosystem-architecture.md` (root docs repo) — the ecosystem-wide account map, Bedrock
  routing, OIDC design, and how TripPlanner sits next to AA-CIS-App / AA-CIS-Infra.
- `AA-CIS-Infra/tripplanner.tf` — the OIDC deploy role and any TripPlanner-specific AWS resources.
