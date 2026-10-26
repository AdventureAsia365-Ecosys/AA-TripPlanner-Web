# Data coverage (AA-992) — TripPlanner part: tp-020 contract views

The CIS admin page `/admin/data-coverage` (S252 design `.tmp-session/s252/datacov/design.md` §1.7, §3.1) counts what
TripPlanner holds and how many saved pins lost their component. TripPlanner publishes the numbers it owns as contract
views granted to `cis_app` (same pattern as tp-015 / AA-858).

## Decisions

- **Three views, not two.** The design had `v_ops_pin_components (component_id, mapped_id, trips)` with one id-map hop
  per pin. Since AA-990 TripPlanner resolves a pin by walking the id map AND the CIS redirect hop by hop (map first,
  ≤ 5 hops, `backend/shared/component_ids.id_map`), and the AA-990 backfill rows sit in the middle of such chains
  (map → redirect → map). One map hop per pin cannot reproduce that walk, so the pin view drops `mapped_id` and a third
  view `v_ops_component_id_map (old_id, new_id)` exposes the map edges (catalog ids only, ~750 rows). CIS walks the same
  chain (map edges priority 1, `geo.component_redirect` priority 2) on its side.
- **Pins = pins ever added** (`add_component` events with a `component_id`, distinct per trip), the aa990 rule;
  `remove_component` is not netted (critic item 6, v2).
- **Pins must be UUID-shaped (fix round 2).** `POST /trip/{id}/components` stores the body's `component_id` in
  `trip_events.payload` (`pii:free_text`) with no type or shape check, and a guest token can call it, so without a
  filter any traveller string would reach `cis_app` row by row and count as a lost pin. The pins CTE keeps only
  `component_id ~* '^[0-9a-f]{8}-…-[0-9a-f]{12}$'` (catalog ids are UUIDs: `component_id_map` uuid columns,
  `events.py` `::uuid[]`), lower-cased (the text form of a Postgres uuid). The rest is counted, never shown, as the new
  totals metric `pin_events_invalid_id`. Fixed before tp-020 is applied (append-only after).
- **Totals as one long view** (`metric`, `n`), like tp-015: adding a metric is a view change, never a column change.
- Copied tp-015 exactly for the guard (`current_user = 'tp_owner'`, `cis_app` exists), the REVOKE of DML from
  `tripplanner` (default privileges), `COMMENT ON VIEW` naming contract + reader, and the registry row.

## Changed

- `migrations/020_ops_data_coverage_views.sql` (new).
- `migrations/tests/test_tp020_pg.py` (new): column lists (the contract), owner + comment + registration, totals and
  pins counted from rows written as the runtime role (payload text never leaves), cis_app reads exactly the 4 contract
  views (tp-015 + 3) and every base table is 42501, runtime reads but cannot write, guard refuses the master, re-run
  is a no-op.
- `CONTEXT.md`: Contracts row for the CIS data coverage reader.

## Tradeoffs

- Exposing the whole id map (not only rows reached by pins) is simpler and still carries no personal or commercial
  data: both columns are CIS catalog component ids.
- No index added: every base table is < 1,000 rows on Dev; the views are read at most every 10 minutes (CIS slow tier).

## Should know

- Apply order: Infra copy + owners.yaml/isolation probe/RLS allow-list (CIS first, Infra copy identical) → DB Migrate
  `check` → `apply` (tripplanner). The CIS API ships before it and shows the TripPlanner rows as
  `n/a: contract_view_missing` until then.
- Tests run locally on a throwaway PG 16 cluster (`--pg-dsn`), 80 passed (again after the fix round 2 UUID filter) incl. the replay-twice chain and the Infra
  runner lint (`--infra-runner`); CI runs them on PG 15 + pgvector.
