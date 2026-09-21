-- ============================================================
-- 001_tripplanner_schema.sql
-- AA-TripPlanner-Web — schema owned entirely by this service.
--
-- Safe to apply: creates a NEW schema `tripplanner` and its tables only.
-- Does NOT touch gold_aa_internal, acp_contract, or any AA-CIS-App schema.
-- Depends on: pgvector (already enabled on this RDS instance, v0.8.1),
--             shared.destinations (see 002_shared_destinations.sql — apply
--             002 first, since itinerary_components references it).
-- ============================================================

CREATE SCHEMA IF NOT EXISTS tripplanner;

-- Ensure pgvector is available (no-op if already enabled).
CREATE EXTENSION IF NOT EXISTS vector;

-- ------------------------------------------------------------
-- itinerary_components — the core unit the customer pins.
-- One destination + one activity + one usable duration, carved out of
-- an existing AA tour's day-by-day content (acp_contract.tour_atoms).
-- ------------------------------------------------------------
CREATE TABLE tripplanner.itinerary_components (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_tour_id  TEXT NOT NULL,          -- = published_tours.tour_id
                                            --   (the key tour_atoms joins on)
    source_day_index INT NOT NULL,
    destination_id  UUID NOT NULL REFERENCES shared.destinations(id),
    name            TEXT NOT NULL,
    activity        TEXT NOT NULL,          -- enum (see CHECK below)
    intensity_level TEXT NOT NULL,          -- enum
    season_months   INT[] NOT NULL,         -- subset of 1..12
    duration_hint   TEXT NOT NULL,          -- enum
    text_extract    TEXT NOT NULL,          -- verified, cleaned atom text
    embedding       VECTOR(1536),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT itinerary_components_activity_chk CHECK (activity IN (
        'trekking', 'cultural_heritage', 'wildlife_nature',
        'water_activities', 'culinary', 'wellness_relaxation',
        'adventure_sport', 'local_immersion'
    )),
    CONSTRAINT itinerary_components_intensity_chk CHECK (intensity_level IN (
        'leisurely', 'moderate', 'active', 'strenuous'
    )),
    CONSTRAINT itinerary_components_duration_chk CHECK (duration_hint IN (
        'half_day', 'full_day', 'overnight', 'multi_night'
    ))
);

-- Vector index: HNSW (not ivfflat). At this scale (~hundreds of rows)
-- ivfflat needs training data to build its lists and gives poor recall
-- on a small/empty table; HNSW needs no training step and gives strong
-- recall immediately. Same cosine operator, no query-side changes.
CREATE INDEX itinerary_components_embedding_idx
    ON tripplanner.itinerary_components
    USING hnsw (embedding vector_cosine_ops);

CREATE INDEX itinerary_components_destination_idx
    ON tripplanner.itinerary_components (destination_id);
CREATE INDEX itinerary_components_activity_idx
    ON tripplanner.itinerary_components (activity);
CREATE INDEX itinerary_components_season_idx
    ON tripplanner.itinerary_components USING GIN (season_months);

-- ------------------------------------------------------------
-- customers — created only at registration (send-to-advisor).
-- Registration MUST dedupe by phone/email before inserting.
-- ------------------------------------------------------------
CREATE TABLE tripplanner.customers (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name       TEXT NOT NULL,
    phone      TEXT UNIQUE,
    email      TEXT UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------
-- sessions — one per visitor. Guest sessions expire; once a customer_id
-- is attached (registration), expiry is cleared (indefinite retention).
-- ------------------------------------------------------------
CREATE TABLE tripplanner.sessions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    guest_token TEXT NOT NULL UNIQUE,
    customer_id UUID REFERENCES tripplanner.customers(id),  -- null = guest
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ   -- set for guests (90d); null once registered
);

-- ------------------------------------------------------------
-- trip_events — canonical, append-only source of truth / history.
-- Never delete rows. Every state change is an event here first.
-- ------------------------------------------------------------
CREATE TABLE tripplanner.trip_events (
    id         BIGSERIAL PRIMARY KEY,
    trip_id    UUID NOT NULL,
    session_id UUID NOT NULL REFERENCES tripplanner.sessions(id),
    event_type TEXT NOT NULL,   -- add_component | remove_component |
                                --   reorder | sent
    payload    JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT trip_events_type_chk CHECK (event_type IN (
        'add_component', 'remove_component', 'reorder', 'sent'
    ))
);
CREATE INDEX trip_events_trip_idx
    ON tripplanner.trip_events (trip_id, created_at);

-- ------------------------------------------------------------
-- trip_drafts — read-cache PROJECTION of trip_events. Updated in the
-- SAME transaction as each event insert. trip_events remains the real
-- source of truth; never write here without appending the event too.
-- id == trip_id referenced in trip_events.
-- ------------------------------------------------------------
CREATE TABLE tripplanner.trip_drafts (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES tripplanner.sessions(id),
    status     TEXT NOT NULL DEFAULT 'draft',   -- draft | sent
    itinerary  JSONB NOT NULL DEFAULT '[]',     -- [{day, component_id,
                                                --   name, rationale}]
    date_start DATE,
    date_end   DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT trip_drafts_status_chk CHECK (status IN ('draft', 'sent'))
);
CREATE INDEX trip_drafts_session_idx
    ON tripplanner.trip_drafts (session_id);
