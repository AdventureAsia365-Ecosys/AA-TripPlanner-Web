-- ============================================================
-- 002_shared_destinations.sql
-- shared.destinations — a table in AA-CIS-App's EXISTING `shared` schema.
--
-- ⚠️  HUMAN-APPLIED ONLY. Do NOT let an agent apply this automatically.
--     The `shared` schema is owned by the wider AA program (already holds
--     tenants, membership_plans, audit_log, ...). Before applying:
--       1. Confirm this migration's number/name does not collide with any
--          concurrent AA-CIS-App migration in the shared schema.
--       2. Confirm the `shared` schema exists (it does today) — this file
--          does not create it, to avoid masking a wrong-DB mistake.
--     Rationale: destinations/country is a candidate cross-program golden
--     record (MDM gap G-D), so it deliberately lives in `shared`, not in
--     tripplanner.*. This is the one intentional exception to the hard
--     schema boundary.
--
-- Idempotent: uses IF NOT EXISTS throughout so re-running is safe and it
-- will not clobber an existing shared.destinations if AA-CIS-App already
-- created one. If a shared.destinations already exists with a different
-- shape, STOP and reconcile manually rather than forcing this.
-- ============================================================

-- pgvector is not required by this table, but destinations feeds the
-- component embeddings; extension creation lives in 001.

CREATE TABLE IF NOT EXISTS shared.destinations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL,
    country         TEXT NOT NULL,     -- normalized (country_normalize.py)
    lat             DOUBLE PRECISION NOT NULL,
    lng             DOUBLE PRECISION NOT NULL,
    cover_image_url TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Case-insensitive unique name: guarantees each place is geocoded at most
-- once (geocode.py looks up lower(name) before calling Mapbox).
CREATE UNIQUE INDEX IF NOT EXISTS destinations_lower_name_uidx
    ON shared.destinations (lower(name));
