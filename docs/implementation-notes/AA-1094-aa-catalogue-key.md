# AA-1094: the AA catalogue keys on the AA-hosted tenant, not on kind demo

S278 live verify finding: `backend/browse/tenant_tours.py` gave every tenant with `kind = demo` the AA catalogue
(`source: "aa"`) and no tenant tour page. Every synthetic agency on Dev is kind `demo`, so none of them could show its
own adopted tours, and the check "a tenant path lists only its adopted tours" could not be proven.

## Decisions

- **Key: the tenant id, through one predicate `config.is_aa_hosted(tenant_id)`.** True for `DEMO_TENANT_ID` (the fixed
  id `aa-tripplanner` has; CIS 379 seeds it and refuses any other tenant holding that id or slug) and for the bare
  path's `DEFAULT_TENANT_ID`. The tenant lookup view `catalog.v_tenant_tripplanner_v1` carries no plan code (no
  `content.internal`), so the fixed id is the explicit attribute TripPlanner has; adding the plan to the view would be
  a CIS contract change for no gain. A slug constant was not needed.
- **One rule for "AA's own planner".** `tenant_budget.exempt` (tenant AI budget, and the agency lead route in
  `assembly/handler.py`) already compared the same two ids; it now calls `config.is_aa_hosted`, so the tour list, the
  AI budget and the lead route cannot drift apart.
- **An agency with no adopted tour lists nothing** (`source: "tenant"`, total 0), never the AA catalogue.
- **`kind` is still answered by `tenant_site.shape`** (the frontend type reads it) but no rule reads it.

## Changed

- `backend/config.py`: `is_aa_hosted()` (canonical uuid compare; a non-uuid is not AA-hosted).
- `backend/browse/tenant_tours.py`: `DEMO_KIND` removed; `serves_aa_catalogue(site)` used by `list_tours` and
  `tour_detail`.
- `backend/shared/tenant_budget.py`: `exempt` uses the predicate (same answers; now also tolerant of case / spaces).
- `CONTEXT.md`: the tenant tours paragraph states the rule.
- Tests: `backend/tests/test_aa1094_tenant_tours.py` (AA tenant by id whatever its kind; a synthetic kind demo agency
  lists its own tours and has a page; an agency with no adopted tour lists nothing; the bare-path override; the
  predicate; a source guard that no tour rule reads `kind`), `backend/tests/test_aa1038_tenant_site_pg.py` (two kind
  demo agencies in the stand-ins, as the runtime role).

## Tradeoffs

- Keying on the id means a second AA-hosted site would need its id added to the predicate. Today there is one, and
  CIS 379 pins it; a plan-code column in the view would be the general answer if a second one ever appears.

## Should know

- No frontend change: the frontend already keys on the list's `source` and the lead form on the slug
  `aa-tripplanner` (`lib/leadForm.ts`), both consistent with this rule. Only the browse Lambda changes behaviour; the
  web service is not redeployed by this PR.
- No migration.
