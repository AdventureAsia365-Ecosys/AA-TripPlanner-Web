# Design: AA_TripPlanner_AI (map-first, v0.3)

> **Historical (AA-998, 28/10/2026):** this spec predates the retirement of acc1. Claude now runs on the
> acc3 satellite only; read `CONTEXT.md` for the current Bedrock routing.

## Architecture
Browser -> Frontend (Next.js, Vercel) -> BFF (Next.js API routes) fans out
to two independent Lambdas:
- **Lambda A (browse)** — stateless, behind CloudFront, tile + destination-
  detail + search queries. Never calls Bedrock.
- **Lambda B (assembly)** — stateful, per-visitor, owns the trip event log,
  sequencing, and the two LLM steps (`compose`, `renarrate`) via the
  Bedrock satellite (acc3 -> acc1).

Both Lambdas read/write the same Postgres instance (shared with
AA-CIS-App), under two schemas: `tripplanner.*` (owned by this service)
and `shared.destinations` (shared table, see steering/tech.md).

The extraction pipeline (`backend/extraction/`) is offline/batch, not a
deployed service — it writes into the same schemas but nothing at
runtime calls it directly.

## Data model

```sql
-- ============================================================
-- shared schema (EXISTING — coordinate migration numbering with
-- AA-CIS-App, do not just append blindly)
-- ============================================================
CREATE TABLE IF NOT EXISTS shared.destinations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    country TEXT NOT NULL,           -- normalized value, see
                                      --   extraction/country_normalize.py
    lat DOUBLE PRECISION NOT NULL,
    lng DOUBLE PRECISION NOT NULL,
    cover_image_url TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE UNIQUE INDEX ON shared.destinations (lower(name));  -- avoid
                                                            -- duplicate
                                                            -- geocoding

-- ============================================================
-- tripplanner schema (NEW, owned entirely by this service)
-- ============================================================
CREATE SCHEMA IF NOT EXISTS tripplanner;

CREATE TABLE tripplanner.itinerary_components (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_tour_id TEXT NOT NULL,       -- = published_tours.tour_id,
                                         --   NOT published_tours.id —
                                         --   tour_atoms joins via tour_id,
                                         --   reuse that key directly
    source_day_index INT NOT NULL,
    destination_id UUID NOT NULL REFERENCES shared.destinations(id),
    name TEXT NOT NULL,
    activity TEXT NOT NULL,             -- enum, see taxonomy below
    intensity_level TEXT NOT NULL,      -- enum
    season_months INT[] NOT NULL,       -- subset of 1..12
    duration_hint TEXT NOT NULL,        -- enum
    text_extract TEXT NOT NULL,         -- verified, cleaned atom text
    embedding VECTOR(1536),
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX ON tripplanner.itinerary_components
    USING ivfflat (embedding vector_cosine_ops);
CREATE INDEX ON tripplanner.itinerary_components (destination_id);
CREATE INDEX ON tripplanner.itinerary_components (activity);
CREATE INDEX ON tripplanner.itinerary_components USING GIN (season_months);

CREATE TABLE tripplanner.customers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    phone TEXT UNIQUE,
    email TEXT UNIQUE,
    created_at TIMESTAMPTZ DEFAULT now()
);
-- Registration flow MUST check phone/email against this table before
-- inserting — see backend/assembly/registration.py

CREATE TABLE tripplanner.sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    guest_token TEXT NOT NULL UNIQUE,
    customer_id UUID REFERENCES tripplanner.customers(id),  -- null = guest
    created_at TIMESTAMPTZ DEFAULT now(),
    expires_at TIMESTAMPTZ  -- set for guest sessions only (90 days);
                            --   null (no expiry) once customer_id is set
);

CREATE TABLE tripplanner.trip_events (
    id BIGSERIAL PRIMARY KEY,
    trip_id UUID NOT NULL,
    session_id UUID NOT NULL REFERENCES tripplanner.sessions(id),
    event_type TEXT NOT NULL,   -- add_component | remove_component |
                                 --   reorder | sent
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX ON tripplanner.trip_events (trip_id, created_at);
-- This is the canonical source of truth / history. Never delete rows.

CREATE TABLE tripplanner.trip_drafts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),  -- = trip_id referenced
                                                     --   in trip_events
    session_id UUID NOT NULL REFERENCES tripplanner.sessions(id),
    status TEXT NOT NULL DEFAULT 'draft',   -- draft | sent
    itinerary JSONB NOT NULL DEFAULT '[]',  -- projection:
                                             --   [{day, component_id,
                                             --     name, rationale}]
    date_start DATE,
    date_end DATE,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);
-- This is a PROJECTION, rebuilt/updated transactionally alongside each
-- trip_events insert. It exists for fast reads only — trip_events is
-- the real source of truth. Never write to trip_drafts without also
-- appending the corresponding event.
```

### Taxonomy (enum values — confirmed for this build)
- `activity`: `trekking`, `cultural_heritage`, `wildlife_nature`,
  `water_activities`, `culinary`, `wellness_relaxation`,
  `adventure_sport`, `local_immersion`
- `intensity_level`: `leisurely`, `moderate`, `active`, `strenuous`
- `duration_hint`: `half_day`, `full_day`, `overnight`, `multi_night`
- `season_months`: integer array, subset of `{1..12}`

### Country normalization (hard-coded, applied during extraction)
```python
COUNTRY_MAP = {
    "SRI-LANDKA": "Sri Lanka",
    "OKINAWA": "Japan",
}
```
Source-of-truth fix tracked separately (Linear AA-571) — this map is a
local workaround only, applied when populating `shared.destinations`.

## Extraction pipeline (`backend/extraction/run.py`)

1. `SELECT tour_id FROM gold_aa_internal.published_tours WHERE
   master_status = 'active'` — 31 tours as of the last check.
2. `SELECT tour_id, itinerary_day, text FROM acp_contract.tour_atoms
   WHERE deleted = false AND tour_id = ANY(...)` for those tours.
3. Group rows by `(tour_id, itinerary_day)`.
4. For each group, call the LLM (Sonnet-tier) with the group's atom
   texts. Prompt it to:
   - decide whether the atoms describe one place or several genuinely
     distinct ones
   - for each resulting place: extract a clean `name`, `activity`,
     `intensity_level`, `duration_hint`, `season_months`,
     `text_extract`
   - if no atom in the group has a clear place name, use the tour's
     already-known primary destination (pass it in as context)
5. Deterministic verify (`verify.py`): for each proposed component,
   confirm the assigned `activity` (or a close synonym) appears in the
   group's raw atom text. On failure, retry step 4 once for that group;
   on second failure, discard.
6. Geocode (`geocode.py`): look up `name` in `shared.destinations`
   (case-insensitive). If not found, call Mapbox Geocoding API once,
   insert a new row (with normalized `country`, via `country_normalize.py`
   applied to the source tour's `raw_tours.country` value).
7. Insert into `tripplanner.itinerary_components`, embedding
   `text_extract` for semantic search.

Run manually via CLI for this build (`python -m extraction.run`) — no
schedule/trigger infrastructure needed yet.

## API contracts

### Lambda A (browse) — behind CloudFront except /search

`GET /browse/tiles/{tile_id}?activity=&intensity_level=&country=&season=`
Response:
```json
{
  "destinations": [
    {"id": "uuid", "name": "Sapa", "lat": 22.34, "lng": 103.84,
     "component_count": 2}
  ]
}
```

`GET /browse/destinations/{destination_id}`
Response:
```json
{
  "id": "uuid", "name": "Sapa", "country": "Vietnam",
  "components": [
    {"id": "uuid", "name": "...", "activity": "trekking",
     "intensity_level": "active", "duration_hint": "multi_night",
     "text_extract": "...", "thumbnail_url": "...",
     "in_trip_day": null}
  ]
}
```

`GET /browse/search?q=...&activity=&country=&season=` (NOT cached)
Same response shape as tiles, but ranked by pgvector cosine similarity
within the pre-filtered set.

### Lambda B (assembly) — never cached

`POST /trip/{trip_id}/components`
Body: `{"session_id": "uuid", "component_id": "uuid"}`
Effect: append `add_component` event, update `trip_drafts` projection,
debounce-triggered `compose`/`renarrate` call.

`DELETE /trip/{trip_id}/components/{component_id}`
Body: `{"session_id": "uuid"}`

`PATCH /trip/{trip_id}/reorder`
Body: `{"session_id": "uuid", "ordered_component_ids": ["uuid", ...]}`
Effect: append `reorder` event, deterministic re-sequencing, debounced
`renarrate` call (never `compose`).

`POST /trip/{trip_id}/send-to-advisor`
Body: `{"session_id": "uuid", "customer": {"name": "", "phone": "",
"email": ""} }` (customer object required only if not already
registered)
Effect: dedupe/create `Customer`, attach to session, set
`trip_drafts.status = 'sent'`, append `sent` event, notify advisor
(fixed address for this build: `pqnghiep1354@gmail.com`, stored as a
single config value, not hardcoded inline in multiple places — swap
before real customer use).

## Sequencing algorithm (deterministic, `backend/assembly/sequencing.py`)
MVP default: greedy nearest-neighbor ordering by destination lat/lng
(start from an arbitrary component, repeatedly pick the nearest
unplaced one). This is a straight-line-distance heuristic, not real
routing — acceptable per scope (§7 of requirements.md). Re-run on every
`add_component`, `remove_component`, or when NOT triggered by a manual
`reorder` (manual reorder replaces the algorithm's output for that
trip until further Add/Remove changes the component set again).

## Agent design (2 LLM steps only — no "parse brief" step in this build)

1. **`compose`** — triggered after sequencing on trip changes. Input:
   ordered list of components (with `text_extract` for each). Output:
   per-day connective narration, streamed via SSE.
2. **`renarrate`** — triggered after a manual reorder. Input: the
   customer's fixed order (never re-derived). Output: narration only,
   same streaming behavior. Must never change which components are
   present or their order.

Both call the Bedrock satellite (Sonnet-tier, acc3 -> acc1) via
`backend/shared/bedrock_satellite.py`.

## Non-functional requirements
| Parameter | Value | Applies to |
|---|---|---|
| Hover debounce (destination popup) | ~200ms | Lambda A |
| Search text debounce | 300-500ms | Lambda A |
| Compose/renarrate debounce | 2-3s of inactivity | Lambda B |
| CDN TTL (tiles + destination detail) | 30 min fixed, no invalidation | Lambda A |
| Tile grid size | 1° latitude/longitude per cell | Lambda A |
| Long-trip warning threshold | ~25 days (warn, never block) | Lambda B |
| Guest session retention | Lost on tab close | Sessions |
| Registered session retention | Indefinite, saved history | Sessions |
