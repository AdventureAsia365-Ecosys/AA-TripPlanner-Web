-- 004: AA-673 — Tour Graph read model, rebuilt from tripplanner.tour_day (AA-675).
--
-- Suggestions must follow routes of tours we actually sell, and a trip may chain several tours.
--   tour_stop        one row per (active tour, day) that has a stop: the overnight destination,
--                    else the day's main itinerary_components destination
--   tour_graph_node  a destination on at least one stop: tours, activities, intensity, seasons
--   tour_graph_edge  A -> B when a tour goes from stop A to a different stop B next
--   tour_leg         every multi-day span of one tour whose first and last days have a stop:
--                    the unit an advisor can sell
--   tour_junction    destination pairs close enough to chain two legs (default <= 150 km),
--                    stored both directions; the same destination is an implicit junction
--   tour_graph_build one row per rebuild: params and stats
--
-- Rebuilt in full by the assembly Lambda (extraction op "tour_graph", backend/extraction/tour_graph.py).
-- Created by aa_cis_admin: the default privileges on schema tripplanner grant the `tripplanner`
-- role SELECT/INSERT/UPDATE/DELETE on new tables. Additive.

BEGIN;

CREATE TABLE IF NOT EXISTS tripplanner.tour_stop (
    source_tour_id  TEXT NOT NULL,
    day_index       INT  NOT NULL,
    destination_id  UUID NOT NULL REFERENCES shared.destinations(id),
    country         TEXT NOT NULL,
    source          TEXT NOT NULL CHECK (source IN ('overnight', 'component')),
    PRIMARY KEY (source_tour_id, day_index)
);
CREATE INDEX IF NOT EXISTS tour_stop_destination_idx ON tripplanner.tour_stop (destination_id);

CREATE TABLE IF NOT EXISTS tripplanner.tour_graph_node (
    destination_id  UUID PRIMARY KEY REFERENCES shared.destinations(id),
    name            TEXT NOT NULL,
    country         TEXT NOT NULL,
    lat             DOUBLE PRECISION NOT NULL,
    lng             DOUBLE PRECISION NOT NULL,
    tour_ids        TEXT[] NOT NULL,
    stop_count      INT  NOT NULL,       -- nights/days spent there across all tours
    activities      TEXT[] NOT NULL DEFAULT '{}',
    intensity_min   TEXT,
    intensity_max   TEXT,
    season_months   INT[] NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS tour_graph_node_country_idx ON tripplanner.tour_graph_node (country);

CREATE TABLE IF NOT EXISTS tripplanner.tour_graph_edge (
    from_destination_id UUID NOT NULL REFERENCES shared.destinations(id),
    to_destination_id   UUID NOT NULL REFERENCES shared.destinations(id),
    tour_count          INT  NOT NULL,
    tour_ids            TEXT[] NOT NULL,
    km                  DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (from_destination_id, to_destination_id)
);

CREATE TABLE IF NOT EXISTS tripplanner.tour_leg (
    source_tour_id           TEXT NOT NULL,
    day_from                 INT  NOT NULL,
    day_to                   INT  NOT NULL CHECK (day_to > day_from),
    days                     INT  NOT NULL,          -- day_to - day_from + 1
    start_destination_id     UUID NOT NULL REFERENCES shared.destinations(id),
    end_destination_id       UUID NOT NULL REFERENCES shared.destinations(id),
    destination_ids          UUID[] NOT NULL,        -- ordered, consecutive duplicates removed
    countries                TEXT[] NOT NULL,
    activities               TEXT[] NOT NULL DEFAULT '{}',
    PRIMARY KEY (source_tour_id, day_from, day_to)
);
CREATE INDEX IF NOT EXISTS tour_leg_start_idx ON tripplanner.tour_leg (start_destination_id, days);
CREATE INDEX IF NOT EXISTS tour_leg_end_idx ON tripplanner.tour_leg (end_destination_id);
CREATE INDEX IF NOT EXISTS tour_leg_countries_idx ON tripplanner.tour_leg USING gin (countries);

CREATE TABLE IF NOT EXISTS tripplanner.tour_junction (
    destination_a  UUID NOT NULL REFERENCES shared.destinations(id),
    destination_b  UUID NOT NULL REFERENCES shared.destinations(id),
    km             DOUBLE PRECISION NOT NULL,
    cross_border   BOOLEAN NOT NULL,
    PRIMARY KEY (destination_a, destination_b),
    CHECK (destination_a <> destination_b)
);

CREATE TABLE IF NOT EXISTS tripplanner.tour_graph_build (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),  -- not a sequence: no USAGE grant needed
    built_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    params      JSONB NOT NULL,
    stats       JSONB NOT NULL
);

COMMIT;
