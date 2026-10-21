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
