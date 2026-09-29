-- 003: AA-675 — tripplanner.tour_day, one row per (published tour, itinerary day).
--
-- itinerary_components groups every place an atom mentions under a day (5.1 on average, up to 15),
-- so it cannot say where a day starts, ends or sleeps. The Tour Graph (AA-673) needs that
-- day-by-day skeleton: a leg is a run of consecutive days of one tour, an edge links the overnight
-- stop of day i to day i+1, a junction is where two tours' overnight stops meet.
--
-- Source: gold_aa_internal.published_tours.aa_itineraries ("Day N — title" blocks). Rule-based
-- parsing first; the tp_extract model fills what the rules miss, and every place it returns must
-- appear in that day's own text (grounding). Rebuilt per tour; a tour whose itinerary text is
-- unchanged (same source_hash) is skipped.
--
-- Created by aa_cis_admin: the default privileges on schema tripplanner already grant the
-- `tripplanner` Lambda role SELECT/INSERT/UPDATE/DELETE on new tables (no GRANT needed).
-- Additive; apply before the TripPlanner deploy that writes it.

BEGIN;

CREATE TABLE IF NOT EXISTS tripplanner.tour_day (
    source_tour_id           TEXT NOT NULL,          -- = published_tours.tour_id
    day_index                INT  NOT NULL CHECK (day_index >= 1),
    title                    TEXT NOT NULL,
    start_place              TEXT,                   -- where the day begins (null: same as last night)
    end_place                TEXT,                   -- where the day's travel ends
    overnight_place          TEXT,                   -- where the traveller sleeps (null: departure day)
    overnight_destination_id UUID REFERENCES shared.destinations(id),
    places                   TEXT[] NOT NULL DEFAULT '{}',  -- other named places visited that day
    extracted_by             TEXT NOT NULL CHECK (extracted_by IN ('rule', 'llm', 'rule+llm')),
    source_hash              TEXT NOT NULL,          -- sha256 of the tour's aa_itineraries text
    extracted_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source_tour_id, day_index)
);

CREATE INDEX IF NOT EXISTS tour_day_overnight_dest_idx
    ON tripplanner.tour_day (overnight_destination_id) WHERE overnight_destination_id IS NOT NULL;

COMMIT;
