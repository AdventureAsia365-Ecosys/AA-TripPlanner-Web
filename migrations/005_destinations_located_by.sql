-- 005: AA-674 — record how a shared.destinations row was located.
--
-- Rows from the early pipelines were geocoded by Mapbox name search alone, which misplaces many
-- Asian names (AA-675: "Chiang Mai" -> a village in Roi Et, Korean hotels in the Philippines).
-- backend/extraction/locate.py now locates places with the model and confirms with Mapbox. Linking
-- reuses a row by name only when it was located this way; older rows are located again.
--   located_by  'mapbox' (model point confirmed by Mapbox within 25 km) | 'llm' (model point kept)
--               | NULL (legacy row, not trusted for reuse)
--
-- Additive, nullable. The `tripplanner` role already has SELECT/INSERT/UPDATE on the table.

BEGIN;

ALTER TABLE shared.destinations
    ADD COLUMN IF NOT EXISTS located_by TEXT CHECK (located_by IN ('mapbox', 'llm')),
    ADD COLUMN IF NOT EXISTS located_at TIMESTAMPTZ;

COMMIT;
