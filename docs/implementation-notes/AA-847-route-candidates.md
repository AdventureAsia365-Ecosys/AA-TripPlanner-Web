# AA-847 — assembly route candidates on PLACES_SOURCE=cis: the next stop as an equality join

Own notes file (parallel AA-847 lanes, lesson S242). Trigger: the first probe after CIS 258 was applied on Dev had one
parity FAIL, `assembly_route_candidates Laos cis 886 ms` (runs 886.0 / 890.7 / 886.5) against legacy 26 ms and a 200 ms
target. Before 258 the same reader measured 191.7 ms (PASS by 4%; the decision memo had kept it as a watch item).
Same pairwise pattern as the routes chain (`AA-847-routes.md`).

## Why 258 changed the plan
- The old `ROUTE_CANDIDATES_SQL_CIS` found each tour's next stop with a correlated subquery in `nxt`:
  `(SELECT min(s2.day_index) FROM stops s2 WHERE s2.source_id = h.source_id AND s2.day_index > h.day_index AND
  s2.destination_id <> h.destination_id) AS to_day`. `nxt` is referenced once and is not MATERIALIZED, so PG inlines
  it, and the subquery is copied into every place `to_day` is used: the `WHERE nx.to_day IS NOT NULL` (probe node
  `CTE Scan:s2`) and the join qual `c.day_index <= nx.to_day` against `catalog.v_tour_component` (`CTE Scan:s2_1`).
  Each copy is a SubPlan that scans the whole `stops` CTE per call (a CTE scan has no index).
- The `s2` copy runs once per `here` row: 1,797 loops, 88 ms, the same before and after 258.
- Where the `s2_1` copy runs depends on the join order. Before 258, `_live_tour.source_id` was the expression
  `COALESCE(rt.source_group_id, rt.tour_id)::text`. The planner has no statistics for it, so the row estimates of
  `v_tour_component` and `v_tour_stop` (both join `_live_tour` on it) were defaults. 258 made it a stored column
  with an index and `ANALYZE` stats, so those estimates became right.
- With the right estimates the planner hash-joins `v_tour_component` to `nxt` on `source_id`. The day range, subquery
  included, becomes that join's filter and runs once per same-tour pair: 16,544 loops, 761 ms of the 886. Before
  258 the plan built the candidate rows another way (`Nested Loop` + `Materialize` over 413 rows) and ran `s2_1` only
  376 times. The probe prints the top nodes only, so this is the reading of `s2_1 loops 376 → 16,544` plus `s2`
  unchanged, not a full plan diff.
- Reproduced locally. On stand-in tables with real stats (the after-258 situation) at Dev-like size, the old text
  rescans `stops` 12,039 times for 1,119 here rows and 13,887 pairs. That is Dev's shape: 16,544 pairs for 1,797 here
  rows.

## Decisions
- **`nxt` is an equality join + GROUP BY** (`MATERIALIZED`): `here h JOIN stops s2 ON s2.source_id = h.source_id
  AND s2.day_index > h.day_index AND s2.destination_id <> h.destination_id`, `min(s2.day_index) AS to_day`, grouped
  by the here row's columns. No SubPlan is left, so no CTE is scanned more than once. The join is bounded per tour
  (at most the tour's days per here row) and hashes on the tour.
- **Same answer, by construction.** A here row with no later stop elsewhere has no join partner, so it drops out of
  the inner join. That is exactly what the old `to_day IS NOT NULL` did, so that predicate is gone. The GROUP BY also
  folds duplicate here rows (duplicate stop rows, or one stop matched to several near rows with the same
  `transfer_km`). No output column can see multiplicity: every aggregate is `MIN` or `COUNT(DISTINCT)`.
- **Tie-break on `d.destination_id`** after `best_distance` in the ORDER BY. The old form left rows tied on
  `best_distance` at the `LIMIT` to the plan. Legacy is unchanged.
- **No forced custom plan** (unlike routes). The rewrite has no parameter-sensitive join order to lose. Locally a
  `force_generic_plan` EXECUTE takes the same time as the custom plan: Dev-like 3–68 ms vs 3–65 ms, 3x 23–136 ms vs
  23–126 ms. So `suggest()` gets no extra transaction or `SET LOCAL`.

## Changed
- `backend/assembly/suggestions.py`: `ROUTE_CANDIDATES_SQL_CIS` only (same parameters $1–$8, same columns).
  `ROUTE_CANDIDATES_SQL` (legacy) is byte for byte unchanged.
- `backend/tests/test_places_source_pg.py`:
  - `ROUTE_CANDIDATES_SQL_CIS_V1`, the slice 6c text frozen as the reference;
  - the seeded generator `suggest_graph()` and the loader `_load_suggest_graph()`;
  - `suggest_args()` and `route_candidates_equivalence()`: EXCEPT ALL both ways on the full answers (LIMIT
    1,000,000), plus the LIMIT 24 answers compared by their `best_distance` keys and by the rows strictly ahead of
    the last key;
  - 3 tests: equivalence on 12 argument sets, a quirk-heavy graph, and a plan check (old has SubPlans and CTE
    loops > 1, new has none).
- `backend/tests/test_places_source_assembly.py`: an offline shape test (no `(SELECT min(`, the equality join, the
  tie-break, the same placeholders, legacy unchanged).

## Tradeoffs
- The `nxt` join builds the same-tour later-stop pairs once (Dev-like: a few per here row) and then aggregates them,
  where the old form ran one CTE scan per row. At 3x it is still one hash join plus an aggregate.
- Materializing `nxt` fixes its row count for the planner, but the estimate of a materialized CTE is rough. The join
  to `v_tour_component` stays a hash join locally at both sizes. The views on Dev are security_barrier views over
  `_live_tour`, which stand-in tables cannot reproduce, so the Dev probe has the final word (Infra
  `feat/aa-847-probe-route-candidates`, READERS regenerated from this branch).

## Should know
- Local evidence: a throwaway PG 16.15 without pgvector. The stand-in tables use the same SQL text, and `<=>` is a
  SQL cosine function, which makes every row costlier than pgvector. Scratch script:
  `/tmp/claude-1000/-home-nghiep-projects-AA-Ecosys/2894b372-a5cb-41dc-8635-5df10ac7d5aa/scratchpad/rc/scale.py`.
  - Dev-like (2 seeds, about 250 tours, 2.6k stops, 6k components, 31–1,119 here rows): 12 of 12 argument sets
    return identical rows, with EXCEPT ALL = 0 both ways and identical top-24 keys. Old takes 255–1,388 ms
    (median of 3); new takes 2.3–59 ms.
  - 3x (750 tours, 8k stops, 18k components, up to 3,026 here rows and 40k pairs): 6 of 6 identical. Old takes
    2.8–6.9 s; new takes 20–120 ms.
- The plan check asserts the old text has SubPlans. If PG ever stops inlining the old CTE, that assertion is the one
  to revisit, not the rewrite.
- `test_vector_topk_pg.py` still needs real pgvector (it errors on a PG without the extension) and is untouched here,
  as in the routes notes.

## Round 2 — each catalog view read once (S246)

Trigger: the Dev probe after round 1 (TP #121 `ee5b0ba`, Infra READERS regenerated from it) measured
`assembly_route_candidates Laos cis 213 ms` (runs 213.8 / 213.0 / 212.4) against legacy 23.8 ms and a 200 ms
target: FAIL by 13 ms. Top nodes: `Seq Scan:published_tours own_ms=137 rows=892 loops=154`, two `Hash Join`
`loops=154` (rows 967 and 776, ~39 ms each), `CTE Scan:nx rows=154 loops=1`, `Nested Loop rows=413 loops=1`.

### Why the 154 loops
- 154 is the row count of `nxt`. Every node inside `catalog.v_tour_component` ran once per `nxt` row: 967 is the
  whole of `geo.tour_place` joined to the live tours, 892 the live rows of `published_tours`.
- `v_tour_component` (and `v_destination`, `v_tour_stop`, `v_graph_node`) are `security_barrier` views. The planner
  never flattens them: each stays a subquery that the join to `nxt` cannot be pushed into (no parameterized path).
  `_live_tour` is not a barrier, so it is flattened *inside* the view, and the live-tour join
  (`published_tours` ⋈ `raw_tours` on `source_key`) is part of that subquery's plan.
- `nxt` is a MATERIALIZED CTE (a GROUP BY over a join of two CTE scans, under a haversine filter the planner gives a
  default selectivity), so its estimate is rough. Locally it is estimated at **1 row** for 129 real ones. With a
  1-row outer side, a nested loop with the view as its inner side and no Materialize costs one inner pass, about
  the same as a hash join. On Dev the planner picked the nested loop. The view's plan then re-ran per real `nxt`
  row: the hash tables were reused, but their probe side (the `published_tours` seq scan) was scanned 154 times.
  This is read from the probe's top nodes (loops = nxt rows, no Materialize). The probe prints no full plan, so
  the 1-row estimate on Dev is inferred, not printed.
- Locally, for the same 1-row estimate, the planner hashes `nxt` instead (a cost tie). `SET enable_hashjoin = off`
  is enough to reproduce Dev's order: `CTE Scan:nxt` outer, the `v_tour_component` subquery inner with
  `loops = 129`, and `v_destination` re-read per joined row (`Seq Scan:destinations rows=1446 loops=501`).

### Decisions
- **Every catalog view in its own MATERIALIZED CTE**, so each runs exactly once whatever the join order:
  `near` (v_graph_node, now MATERIALIZED too), `stops` (v_tour_stop, as before), `comp` (v_tour_component) and
  `dest` (v_destination). The main query joins CTEs only, and a rescan of a CTE reads its tuplestore, not the
  view. The other option, keeping the view joins and hoping for a hash join, depends on an estimate we cannot fix
  from TripPlanner.
- **`comp` reads only the candidate tours**: `c.source_id = ANY(ARRAY(SELECT DISTINCT x.source_id FROM nxt x))`,
  an InitPlan evaluated once. `texteq` is leakproof, so the `= ANY` is allowed through the barrier. Locally it lands
  on `raw_tours_source_key_idx` and the tour_place source index (19 of 41 live tours for one last stop), not a
  full component scan. This is the `COUNTRY_CLAUSE_CIS` pattern. The activity / intensity / embedding filters move
  in with it.
- **`comp` keeps the distance, not the vector** (`c.embedding <=> $1::vector AS distance`). The Dev vectors are
  1536-d, about 6 kB a row, and a CTE stores what it selects. `MIN(c.distance)` is the same value as
  `MIN(c.embedding <=> $1)`: the same function on the same inputs.
- **`dest` reads only the destinations `comp` reached** (`= ANY(ARRAY(SELECT DISTINCT ... FROM comp))`, `uuid_eq`
  leakproof), with the pinned-place exclusion `<> ALL($5)` moved in. `v_destination`'s per-destination photo
  LATERAL then runs for those rows only.
- **Same rows by construction.** It is the same inner joins and the same filters, grouped by the same columns in
  another order. Each `= ANY` keeps only keys that can join anyway (a component of a tour outside `nxt`, or a
  destination no kept component reaches, has no join partner). No DISTINCT is applied to view rows, so the
  multiplicities are the views'. The output, ORDER BY and tie-break are unchanged.

### Changed
- `backend/assembly/suggestions.py`: `ROUTE_CANDIDATES_SQL_CIS` only (same $1–$8, same columns). Legacy is
  unchanged.
- `backend/tests/test_places_source_pg.py`:
  - `ROUTE_CANDIDATES_SQL_CIS_V2`, the round-1 text, frozen byte for byte (checked equal to `ee5b0ba`'s constant
    before the edit);
  - `route_candidates_equivalence(..., old=, new=)`;
  - `CATALOG_VIEWS_SCHEMA`, stand-in **views**. The base tables use CIS's columns, keys and indexes
    (`raw_tours.source_key` stored + indexed, `published_tours.tour_id` unique, `graph_build_one_current_uq`, the
    tour_place / tour_stop / tour_segment keys). `published_tours` / `raw_tours` have FORCE RLS with the view
    owner's AA-master policy plus `tenant_isolation`. The views are the 253 / 256 / 258 texts on Dev
    (`_live_tour`, `v_tour_component`, `_live_stop`, `v_tour_stop`, `v_graph_node`, `v_destination`,
    `shared.v_destination_photos`), owned by a throwaway non-superuser role and read as a second role;
  - `catalog_rows()` / `load_catalog_views()`: a suggest_graph() catalog mapped onto the base tables, with what the
    views must hide. That is tours of another tenant, master not active, deleted, source inactive or unpublished,
    superseded group versions, suspect / merged / line destinations, an old build's stops and nodes, and stale
    components. Filler tours and destinations bring the scans to Dev's sizes;
  - fixture `views_pool` (a database plus two roles, all dropped after the test) and 2 tests:
    `test_route_candidates_round2_same_rows_over_the_views` (V3 against V1 and V2, 8 argument sets, EXCEPT ALL
    both ways) and `test_route_candidates_views_read_once`. The second checks that, with hash joins off, V2 reads
    view tables outside any CTE more than once. In both the natural and the forced plan, V3 reads every table inside
    one of its five CTEs, each CTE runs once (`loops = 1`), and no seq scan of published_tours / raw_tours runs
    more than once.
- `backend/tests/test_places_source_assembly.py`: an offline shape test (the five CTEs and which are MATERIALIZED,
  the four views each named once, a main query with no `catalog.`, distance not vector, the two `= ANY` InitPlans,
  the tie-break).

### Tradeoffs
- Two more CTE materializations (comp: the candidate tours' components, a few hundred rows of 5 columns; dest:
  the reached destinations). Where the planner already hashes (the local natural plan), V3 is up to 2.4 ms slower
  than V2 at Dev size and up to 5.4 ms at 3x on some argument sets, and faster on others. That is the price of the
  guarantee. Where V2 takes Dev's order, V3 removes the per-row view runs.
- `near` is materialized even though the Dev plan never re-ran `v_graph_node`. It is the same risk (a barrier
  aggregate view over `_live_stop` joined to a CTE), and one evaluation costs the same as the inlined one.
- Inside each view the planner is still free. On the stand-ins `_live_stop` probes `published_tours` by its
  `tour_id` key once per live raw tour (`Index Scan loops=41`, 130 at 3x) and `comp` once per candidate tour
  (19). These are per-tour index probes inside one evaluation, the same in V2 and V3, and they are CIS's view
  plans. No *seq* scan of published_tours / raw_tours runs more than once, and nothing re-runs per `nxt` row.

### Should know (local evidence)
Throwaway PG **15.17 with pgvector 0.8.1** (Dev's major version; an initdb'd cluster on 127.0.0.1:54392, scratch
`/tmp/claude-1000/-home-nghiep-projects-AA-Ecosys/2894b372-a5cb-41dc-8635-5df10ac7d5aa/scratchpad/rc2/run.py`).
The vectors are 1536-d as on Dev. The reads run as the reader role through the barrier views, with RLS applied as
the view owner.
- **Dev size, 2 seeds × 6 argument sets** (seed 847: 845 raw tours, 842 published, 1,450 destinations, 473 stops,
  1,132 tour_place. Views: v_tour_component 786, v_tour_stop 309, v_graph_node 85. Seed 1847: 955 / 371 / 105):
  12 of 12 identical against **V1 and V2**, EXCEPT ALL 0 both ways, and the same top-24 keys and rows ahead of
  the cut. Medians of 5: V1 10.6–114 ms, V2 2.4–20.5 ms, V3 2.0–12.1 ms. With `enable_hashjoin = off` (Dev's
  order), EXPLAIN ANALYZE gives V2 17.7–156 ms (the view re-run 129×, `v_destination` 501×) against V3
  3.6–23 ms, whose CTEs all run once.
- **3x, seed 2847, 6 argument sets** (2,541 raw tours, 4,350 destinations, 1,286 stops, 3,060 tour_place. Views:
  2,473 / 964 / 272): 6 of 6 identical against V1 and V2. V1 85–1,209 ms, V2 5.2–13.7 ms, V3 5.4–19.1 ms. Forced
  order: V2 EXPLAIN 21–232 ms, and V3 8.6–58 ms with no table read outside its CTEs.
- Local timings cannot reproduce Dev's 213 ms. Locally the natural plan is already a hash join, and the live-tour
  join under a Sort is reused on rescan. The Dev probe has the final word (Infra `feat/aa-847-probe-rc2`, READERS
  regenerated from this commit).
- `test_vector_topk_pg.py` passes on this PG 15 + pgvector cluster. The full backend suite with `--pg-dsn` gives
  426 passed.
