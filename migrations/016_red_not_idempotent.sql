-- Migration tp-016: AA-849 RED EVIDENCE ONLY — never merge, never copy to AA-CIS-Infra.
--
-- Deliberately not idempotent: a plain CREATE TABLE (no IF NOT EXISTS). It passes the migration lint and the
-- first replay pass, and must turn the CI job "Migrations (lint, PG 15 replay)" red in the second pass
-- (test_replay_twice_pg.py::test_pass_2_runs_every_file_again_without_an_error).

BEGIN;

CREATE TABLE tripplanner.aa849_red_probe (id bigserial PRIMARY KEY, note text NOT NULL);

INSERT INTO shared.schema_versions (version, applied_at, description)
VALUES ('tp-016', now(), 'AA-849 red evidence: not idempotent (never merge)')
ON CONFLICT (version) DO NOTHING;

COMMIT;
