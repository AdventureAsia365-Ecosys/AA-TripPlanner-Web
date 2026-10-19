# AA-858 — TripPlanner part: tp-015 ops overview contract view

Session S243 (19/10/2026), builder lane AA-858 step S2. The CIS admin overview (`/admin/overview`) gets a
"TripPlanner customers" card. CIS must not read `tripplanner.*` tables (X1 target, isolation probe), so TripPlanner
publishes one aggregate view in its own schema and grants it to the CIS runtime role (contract rule 5, root ADR
0008 §5; same pattern as tp-009 → `booking`).

## Decisions

- **One view, long format**: `tripplanner.v_ops_traffic_daily (day date, metric text, n bigint)`, UTC days, last 400
  days. A new metric is a new row value (contract `ops.overview.v1`, append-only metric names).
- **Metrics**:
  - `planner_session_started` — `sessions` by `created_at` (one guest planner session);
  - `trip_started` — `trip_drafts` by `created_at`;
  - `trip_edited` — `count(DISTINCT trip_id)` of `trip_events` other than `sent` that day (a trip edited 20 times
    in a day counts once);
  - `inquiry_sent` — `outbox` rows of event `inquiry.submitted` by `created_at` ("send to advisor").
- **Searches are not in the view**: they are not stored in `tripplanner.*`; CIS counts `shared.llm_call_log` rows of
  stage `tp_search_embed` (cache misses only — the label says so).
- **Visits** are not in the view either (Vercel Web Analytics; the overview links out, design §6 forbids a Vercel token
  in the console).
- **Grants**: `GRANT USAGE ON SCHEMA tripplanner TO cis_app` + `GRANT SELECT ON tripplanner.v_ops_traffic_daily TO
  cis_app`. Nothing to `aa_cis_admin`: the CIS runtime logs in as `aa_cis_admin` until X1 S18 and is a member of
  `tp_owner` (rl-001), so it already reads the view; after S18 it is `cis_app`. The view reads its base tables with
  `tp_owner`'s rights: `cis_app` → `tripplanner.sessions` stays 42501 (probe cell `cross.cis_app.tripplanner.sessions`).
- **Runtime `tripplanner`** keeps SELECT only on the view (default privileges FOR ROLE `tp_owner` gave it DML; revoked
  for hygiene, the view is not updatable anyway). Probe cell `runtime_grants.tripplanner` (SELECT on every relation)
  stays green.
- **Guard** raises (never skips) when the file is not run as `tp_owner` or `cis_app` is missing: tp-009's rule that a
  guarded no-op must not register a key with a missing grant.

## Changed (vs. the plan)

- Plan file name `015_ops_overview_view.sql` kept; key tp-015 confirmed by the main session.
- CONTEXT.md: the slice 6c "next free key" moves to tp-016 or later (tp-015 is taken here).
- The replay test `migrations/tests/test_tp015_pg.py` reuses the tp-013 stand-in from `test_tp013_pg.py` (imports
  its helpers and the `pg_dsn` fixture) instead of copying it; it adds `cis_app` NOLOGIN to the stand-in.

## Tradeoffs

- **View vs. an HTTP ops endpoint on the Lambdas**: TripPlanner has no health/ops endpoint, and adding one means a
  Lambda deploy plus auth for a CIS caller. A view costs nothing to run and ships with no Lambda deploy (the PR
  touches only `migrations/`, `CONTEXT.md` and this note).
- **Plain view vs. a stored daily table**: Dev has tens of rows; CIS caches the section 300 s. All four branches are
  bounded by the 400-day filter.

## Should know

- **Nghiệp merges this PR** (agents never merge TripPlanner). Then the AA-CIS-Infra copy PR
  (`chore/aa858-copy-bk017-tp015`) carries the byte-identical `db/migrations/tripplanner/015_ops_overview_view.sql`, and
  DB Migrate applies it as `tp_owner`.
- Until it is applied the CIS card shows "unavailable" (CIS handles a missing view).
- Local checks (S243): `pytest migrations/tests --pg-dsn <local PG 16> --infra-runner <AA-CIS-Infra db/run_migration.py>`
  → 38 passed (6 new in `test_tp015_pg.py`, the DB Migrate `--inspect` lint passes on 015). CI replays on PostgreSQL 15
  + pgvector.
