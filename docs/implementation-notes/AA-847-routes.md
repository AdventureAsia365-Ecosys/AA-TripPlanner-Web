# AA-847 — routes on PLACES_SOURCE=cis: equality-join chain query, custom plans

Own notes file (parallel AA-847 lanes, lesson S242). Decision memo: the S246 step-18 gate diagnosis (section 2.2),
which kept `routes Laos cis 488 ms vs 200` as a real TripPlanner query defect. CIS 258 (leakproof live-tour key) is a
separate CIS PR and changes no TripPlanner SQL.

## Decisions
- **`CHAIN_SQL_CIS` joins on an equality.** The slice 6b form joined every leg pair on the days range alone, then
  kept pairs that met at the first leg's end or at a junction (`LEFT JOIN j … WHERE b.start = a.end OR j IS NOT NULL`).
  On Dev Laos that built 136,636 pairs and a disk-spilling sort for 48,517 kept rows (260 ms per call). The rewrite
  adds a `hop` CTE: per first leg `a`, every place a second leg may start at — `a`'s own end (`km` NULL → 0) and each
  junction from `a`'s end — and joins `legs b ON b.start_destination_id = h.next_start`. `span` / `legs` / `j` stay
  as they were; `a` is `MATERIALIZED` (read twice by `hop`).
- **Self-pair junctions are skipped in the junction branch** (`AND j.destination_b <> a.end_destination_id`). The CIS
  junction table (`geo.tour_junction`) has no CHECK against `destination_a = destination_b` (only the graph builder
  avoids it; `tripplanner.tour_junction` had the CHECK). Without the filter a self-pair would add a second row next
  to the 0 km one.
- **Deterministic tie-break**: the ORDER BY now ends on `h.source_id, h.day_from, h.day_to, b.source_id, b.day_from,
  b.day_to`. The old form left equal-rank rows to the plan; `rank()` keeps the first of a tie (stable sort, deduped
  by tour set), so a different plan changed which proposal survived. Found by the existing PG test
  `test_routes_same_proposals_keyed_by_source_id` (days=6: two chains of the same two tours, equal on every key),
  which failed on the rewrite until the tie-break was added and passes with it (same answer as legacy there).
- **Custom plans for the cis single + chain reads only**: `proposals()` wraps them in `conn.transaction()` with
  `SET LOCAL plan_cache_mode = force_custom_plan` (`FORCE_CUSTOM_PLAN_SQL`). The memo's diagnosis (plausible, not yet
  proven on Dev): asyncpg caches the prepared statement, after five runs PG may pick a generic plan with 1-row
  estimates (EXPLAIN 11.7 ms vs 841 ms timed). Legacy is unchanged (no transaction, no SET); nothing is set
  pool-wide (that would add planning to tiles and the other readers). The probe mirrors this with
  `set_config(..., true)` around its own calls (Infra probe PR).

## Changed
- `backend/browse/routes.py`: `CHAIN_SQL_CIS` (hop form + tie-break), `FORCE_CUSTOM_PLAN_SQL`, `proposals()`.
- `backend/tests/test_places_source_pg.py`: `CHAIN_SQL_CIS_V1` (the merged slice 6b text, frozen as the reference),
  seeded `chain_graph()` generator + `chain_equivalence()` (EXCEPT ALL both ways without the LIMIT, plus the LIMIT 40
  answers' sort keys); 3 new tests (equivalence on 2 countries × 5 durations, the self-pair behaviour, plan_cache_mode
  scoped to the transaction on cis and never set on legacy).
- Same file: the stand-in `catalog.v_destination` / `catalog.v_tour_component` tables gained the CIS 256 (slice 6d)
  columns (shape, bbox, segment). HEAD already failed 5 of the file's 7 tests (`column d.shape_kind does not exist`,
  `column c.segment does not exist`) since the slice 6d merge: CI does not run this file (`--pg-dsn` only).
- `backend/tests/test_places_source.py`: offline test that the SET LOCAL precedes exactly the cis single + chain reads.

## Tradeoffs
- `hop` duplicates each first leg once per junction from its end (Dev: ~2.8k junctions over ~1.6k places, a few per
  place), against the old cross product of every leg pair. Locally the old plan's `Materialize` loops and external
  merge sort (17–133 MB) are gone; the new plan is one hash join (7–93 ms vs 23–918 ms on the same data).
- A transaction per cis routes call costs one BEGIN/COMMIT round trip each way; a custom plan costs ~1–3 ms of planning
  per read. Both are small next to the generic-plan loss the memo measured.
- Equivalence holds when no self-pair junction exists. With one, old returned the junction's row (its km) INSTEAD of
  the 0 km row, one per self-pair; new returns the single 0 km row. Accepted: a self-pair is a graph bug, and 0 km is
  the right answer for "starts where the first leg ends".

## Should know
- Local evidence (throwaway PG 16 without pgvector; stand-in tables, same SQL text): seeded Dev-shaped graphs
  (122 tours / 5,853 legs / 2,094 junctions, and 66 / 3,060 / 5,360), Laos, Vietnam, Thailand, Cambodia × 7/14/21
  days: 15 of 15 runs with identical row counts (11,027 – 178,952 rows), `EXCEPT ALL` = 0 both ways, and identical
  top-40 sort keys.
- The stand-in tables are not the views: planner behaviour over the real `catalog.v_tour_leg` (joins `_live_tour`)
  is for the probe to measure on Dev (`routes_chain` Laos cis; gate wording in the memo section 2.4).
- `test_vector_topk_pg.py` needs real pgvector (errors on a PG without the extension); untouched here.
- The probe READERS in AA-CIS-Infra are regenerated from this branch's HEAD (`gen_probe_sql.py`), so the probe runs
  the same `CHAIN_SQL_CIS` text.
- Review notes (independent review, S246), not fixed here:
  - `test_routes_same_proposals_keyed_by_source_id` compares cis with legacy. Legacy `CHAIN_SQL` still leaves equal
    rank rows to the plan, so at days=6 the match rests on the legacy plan's current order. If that test flakes, look
    at the legacy side's tie first, not at the cis SQL.
  - Pre-existing in both paths: the chain SQL orders km before places, `rank()` orders places before km, so the
    LIMIT 40 can drop a candidate `rank()` would have preferred. Old and new cis behave the same; a fix changes the
    proposals and belongs in its own PR.
