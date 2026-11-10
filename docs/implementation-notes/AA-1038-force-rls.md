# AA-1038 — R5, the FORCE slice: enforcing tenant row-level security (tp-028)

Lane l1-tp-rls, S276. Slice 1 (tp-025, `AA-1038.md`) added `tenant_id` and shadow policies to `tripplanner.sessions`,
`trip_drafts`, `trip_events` and `outbox`; this slice makes them enforcing. Pieces: migration
`migrations/028_force_tenant_rls.sql`, the jobs Lambda's system scope (`backend/shared/tenant.py` `system_scope`,
opened by `backend/jobs/outbox_redact.py` and `backend/jobs/lead_deliver.py`), tests, CONTEXT.md "Tenancy". The
AA-CIS-Infra side (a TripPlanner cell in the nightly DB Isolation Probe) is its own PR.

## Decisions

- **Three policies per table instead of one.** `<table>_tenant` (TO `tripplanner`, the bound tenant only, no unset
  branch), `<table>_system` (TO `tripplanner`, `tp_owner`, GUC `app.tenant_scope = 'system'`) and
  `<table>_owner_read` (SELECT TO `tp_owner`: every row without a bound tenant, the bound tenant's rows with one).
  One policy per path keeps each readable and lets the probe and the guards name them.
- **System path = a GUC scope, not a new role.** The jobs Lambda logs in as `tripplanner` like the other two
  Lambdas. A separate NOLOGIN role (the CRM pattern) needs an Infra `roles/` file, an owners.yaml entry, a grant and a
  `SET LOCAL ROLE` allowlist; a per-tenant loop cannot list the tenants without reading across them (and would skip a
  suspended tenant's rows, which retention must still reach). The GUC is set per job run by
  `tenant.system_scope(conn, op=KIND)` and reset in a `finally`, the same lifecycle as `tenant.bound`.
- **Only the jobs Lambda opens the scope, enforced by a test.** `backend/tests/test_aa1038_force_rls.py`: no module
  outside `backend/jobs/` calls `system_scope(`, `app.tenant_scope` is spelled only in `shared/tenant.py`, every job
  module that names a tenant table acquires its connection with the scope, every assembly connection is acquired with
  `tenant.bound` in the same `async with`, and the browse Lambda names no tenant table.
- **Owner read follows the session's tenant.** Not `USING (true)`: rule t of the RLS catalogue (AA-CIS-App
  `tests/integration/rls_catalogue_rules.py`) flags a bare true outside the CIS parity policies, and an owner view read
  by the runtime role under a bound tenant now shows only that tenant's rows. `cis_app` binds no TripPlanner tenant,
  so the CIS ops pages keep every row. Owner writes go through `<table>_system` only.
- **`v_inquiry_outbox` becomes `security_invoker`.** The retired view (no consumer since tp-027) is still granted to
  `tripplanner` with DML. As an owner view under FORCE it would answer through `outbox_owner_read`, i.e. every tenant's
  traveller data to a connection without a tenant. As an invoker view the caller's own policies apply.
- **Default without the demo fallback.** `NULLIF(current_setting('app.tenant_id', true), '')::uuid`: a missing bind
  is an error now, never a demo row.
- **Order: code first, then the migration.** The jobs code works under tp-025 (no policy reads `app.tenant_scope`
  there), and the assembly Lambda has bound a tenant on every request since slice 1. So the code deploys first and
  tp-028 is applied after it; one PR carries both, and the live steps put Deploy Lambdas before the Infra copy and
  Migration Apply.

## Changed

- `migrations/028_force_tenant_rls.sql` (new, contract, no directive line).
- `backend/shared/tenant.py`: `SCOPE_GUC`, `SYSTEM_SCOPE_SQL`, `UNSCOPE_SQL`, `system_scope()`; docstrings describe
  tp-028.
- `backend/jobs/outbox_redact.py`, `backend/jobs/lead_deliver.py`: the run opens the system scope;
  `backend/jobs/handler.py`: comment.
- Tests: `migrations/tests/test_tp028_pg.py` (new; also `forced_database`, the stand-in the backend integration tests
  now use), `test_replay_twice_pg.py` (tp-025 in `SUPERSEDED_BY_DROP`), `backend/tests/test_aa1038_force_rls.py` (new),
  `test_aa1038_tenant.py` (system scope), `test_aa1038_lead_deliver.py` (the fake records the scope; the run opens it
  first and resets it last, also when it raises), `test_tenant_rls_pg.py`, `test_aa1038_tenant_site_pg.py`,
  `test_aa1038_leads_pg.py` (run on the tp-028 replay).
- Against the issue text: the issue says "FORCE RLS"; FORCE alone only changes the owner (the runtime role was
  already under row security with ENABLE). The isolation comes from dropping the unset branch; FORCE puts the owner
  and its views under the policies too.

## Tradeoffs

- A GUC scope is a code-level boundary, not a database one: any code running as `tripplanner` could set it. The same
  is true of `app.tenant_id` itself (any code can bind any tenant); the RLS model here protects against a missing or
  wrong bind, not against SQL injection. The static test keeps the scope in `backend/jobs/`.
- `v_ops_*` stay owner views readable by the runtime role without a tenant (counts and keys, no traveller data).
  Making them invoker views would break `cis_app`, which holds no table grant.
- `test_tp028_pg` runs the policies on PostgreSQL 16 locally (no pgvector) and on 15 + pgvector in CI.

## Should know

- **Live order:** merge, Deploy Lambdas green (the jobs code), then the Infra bot copy of tripplanner/028 and the
  Infra Migration Apply (contract). After the apply: the DB Isolation Probe cell `tenant.tripplanner_isolation`
  (Infra PR of this lane) turns from pending to expected.
- A later tp- file that UPDATEs or DELETEs rows of the four tables as `tp_owner` must open the scope first:
  `SELECT set_config('app.tenant_scope', 'system', true);` inside its transaction (the TripPlanner migration lint
  allows a set_config with a plain literal name). Without it the statement reaches no row and says nothing.
- An ad hoc script that counts these tables as `tripplanner` without a bound tenant now gets 0 (lesson: a count of 0 on
  an RLS table is "not sure", not "empty"). Count as the owner (DB Migrate `mode=run-script`) or bind the tenant.
- Undo: a later contract file (NO FORCE, drop the three policies, recreate the shadow policies, the demo default back,
  reset the view option), only after the jobs code stops depending on it (it does not: the scope is harmless under
  the shadow policies).
