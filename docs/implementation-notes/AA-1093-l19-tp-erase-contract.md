# AA-1093 lane L4 follow-up (S279 lane l19-tp-erase-contract): TripPlanner erasure on the tenant.erasure.v1 contract

Why: tp-030 (applied on Dev) and the jobs code of lane l12 (`AA-1093-l12-tp-erase.md`) were built before the contract
`tenant.erasure.v1` landed on the root repo (root `docs/contracts/tenant.erasure.v1.openapi.yaml`). The contract wants
an append-only report log (one row per report, CIS keeps the newest per (app, round, mode)), statuses
ok / blocked / refused / failed with error codes, rounds, md5 table digests and a plan hash bound to the round, and the
request view `catalog.v_tenant_erasure_v1` with the ErasureRequestRow columns. Booking's bk-057 and
`app/b2b/tenant_erase.py` (lane l13) already speak it; this lane follows them.

Ships as two PRs (MIG-4): the migration-only PR (tp-031, contract class) and this code PR after tp-031 is applied on
Dev.

## Decisions

- **tp-031 reshapes the log in place, no DROP TABLE, no DELETE.** The AA-CIS-Infra runner classifies `DROP TABLE` as
  destructive (`-- migrate: destructive`, snapshot + DRY RUN, never auto-applied) and stops the contract auto-apply
  on any DELETE statement. An in-place reshape stays contract: drop the old primary key and CHECKs, rename
  `reason` -> `error_code`, `counts` -> `tables`, `created_at` -> `reported_at`, add `report_id` (new primary key),
  `app`, `round`, `blockers`, `census`, `source`, and the contract CHECKs (same rules as bk-057). The reshape runs only
  while the column `counts` exists, so a re-run changes nothing (`test_replay_twice_pg.py`).
- **tp-030 rows are carried over, not deleted; an erasure that ran stops the file.** On Dev the table may hold an
  operator DRY RUN of the l12 live steps. Rows of status planned / refused become reports of round 1, `source = tp030`
  (planned -> ok, the reason becomes the error code, the counts object becomes a tables list with digest NULL, plan
  hash kept on a DRY RUN, NULL on a verification). A row of status applied / verified / residue (an erasure ran) makes
  tp-031 raise before any change: it would have to be carried over by hand, never guessed.
- **`updated_at` becomes `legacy_updated_at` (nullable), not dropped.** Dropping a column is destructive too. It holds
  the upsert time of a tp030 row and stays NULL on every report since tp-031; a later destructive file can drop it.
- **The UPDATE of the tp-030 rows opens the system scope for that one statement.** FORCE row-level security applies
  to tp_owner as well: its only write policy (`tenant_erasure_log_system`) needs `app.tenant_scope = system`. The
  first test run showed the UPDATE touching 0 rows without it (then the NOT NULL on `report_id` failed); the file now
  sets the scope with `set_config(..., true)`, restores the previous value right after, and checks the UPDATE touched
  every row.
- **`source` column (cis / operator / tp030)**, like Booking's `source` (cis / cli). The report view leaves the
  tp030 rows out (`WHERE source <> 'tp030'`): they are not contract reports (old hash formula, no digests).
- **Function signature `erase_tenant(tenant, round, dry_run, expect_plan DEFAULT NULL)`**; the tp-030 signature is
  dropped (`DROP FUNCTION IF EXISTS`, not a destructive statement for the runner). Kept from tp-030: SECURITY INVOKER,
  the pinned session settings, the system-scope check, EXCLUSIVE locks on an apply, exact-count deletes, the customers
  only this tenant links, the spend merge, `plan_changed`.
- **Digest per the contract**: md5 of the sorted (COLLATE "C") `<primary key>|<row version>` lines joined by a newline;
  row version = `updated_at` as `YYYY-MM-DD"T"HH24:MI:SS.US"Z"` in UTC (bk-057's format), empty for sessions,
  trip_events, outbox and customers (no `updated_at`); the key of `ai_spend_month` is `stage,scope,month`. The
  tp-030 fingerprint (md5 of the whole row text) also moved on content edits; the contract digest moves on row set and
  `updated_at` changes only, which is what a delete plan needs.
- **Plan hash** = the contract formula; the jobs code recomputes it in Python (`tenant_erase.plan_hash`, the same code as
  Booking's) and reports `failed` / `internal_error` if the database ever returns another value.
- **Actions**: delete (the four tenant tables, customers), merge (`ai_spend_month`; tp-030 called it
  `merge_into_app`, not a contract action), keep (`traveller_accounts`, digest NULL).
- **Error codes aligned with Booking** (the contract lists examples only): `aa_hosted_refused` (was `demo_tenant`),
  `pilot_refused` (was `pilot_tenant`), `not_due`, `apply_disabled`, `tenant_markers_unknown`, `not_synthetic`,
  `approval_missing` and `dry_run_missing` (together the old `no_approved_plan`), `approval_expired`, `plan_changed`,
  `census_not_zero`, `census_incomplete`, `internal_error`.
- **One report per (request, round, phase), no retry** (Booking's rule): a refusal or a failure is a report; CIS reads
  it and decides the next round. The l12 code retried non-final refusals daily, which with an append-only log would
  write a row a day. An apply already reported ok is never run again.
- **Feed**: all ErasureRequestRow columns are required (feature-detected; else `feed_shape_unknown`, attention);
  `WHERE app = 'tripplanner'`, newest `updated_at` per request, `step_status = 'active'`, phase dry_run / apply /
  verify, `not_before <= now()`; an apply row without an approved hash is skipped (CIS sets both together).
- **Verify census**: the `pii:` columns (DM-6.1 comments, read from `pg_description`) of the planned tables; cleared
  ones (trip_drafts itinerary / dates, trip_events payload, outbox payload, customers name / phone / email) are
  counted for the tenant; `traveller_accounts.iss/sub` are kept by rule; any other tagged column fails the verification
  `census_incomplete` (blocker `pii_column_unlisted`).
- **Operator path kept** (DRY RUN default): request id = UUIDv5 of the tenant (Booking's CLI does the same), round 1,
  payload may name `request_id` / `round`; markers from `tenant_target.markers`; no approval expiry for a drill.
- **Contract test copy**: `backend/tests/contracts/tenant.erasure.v1.json` (ErasureReport, ErasureReportRow,
  ErasureRequestRow and their components, generated from the root YAML like the other vendored contracts). Offline
  tests validate every kind of report and a request row; the PG test validates every row cis_app reads in the view.

## Changed

- Migration PR: `migrations/031_tenant_erasure_contract.sql`, `migrations/tests/test_tp031_pg.py` (its
  `erasure_database` replaces tp-030's for the backend PG tests), `test_replay_twice_pg.py` (tp-030 in
  `SUPERSEDED_BY_DROP`: its view names renamed columns; the log keeps SELECT, INSERT only).
- Code PR: `backend/jobs/tenant_erase.py` rewritten on the contract; `tenant_target.py` docstring;
  `backend/tests/test_aa1093_tenant_erase.py` (erase part rewritten, backup / drill part unchanged),
  `backend/tests/test_aa1093_tenant_erase_pg.py` (contract feed stand-in, contract row validation; backup / drill tests
  call the 4-argument function), `backend/tests/contracts/tenant.erasure.v1.json`, `CONTEXT.md`, `docs/jobs-runbook.md`,
  `backend/tests/test_aa1038_force_rls.py` (`tenant_erase.py` now names the tenant tables in its census SQL: it joins
  the list of job modules that must open the system scope, which it does).
- Unchanged: the daily pilot backup, the restore drill, the S3 AccessDenied handling, `MAX_ATTEMPTS = 1`.

## Tradeoffs

- In-place reshape vs drop and recreate: more statements, but contract class (auto-applied when the job tables are
  idle), no snapshot, and drill rows survive as evidence.
- Keeping `legacy_updated_at` leaves one column only tp030 rows use; dropping it needs a destructive file for one
  timestamp.

## Should know

- **Order on Dev**: tp-031 applied by Migration Apply (contract) -> this PR merged -> jobs Lambda deployed. Between the
  apply and the deploy the old code calls a function that no longer exists; its daily tick answers `no_feed` (no CIS
  request view yet), so only an operator erase drill in that window fails (nothing is touched). The backup and the
  drill are not affected.
- **Thresholds are settings (S279):** no new threshold. `LOCK_TIMEOUT` 5 s and `STATEMENT_TIMEOUT` 60 s stay technical
  constants; TripPlanner has no admin-editable settings mechanism yet (follow-up, as in the l12 notes).
- **ERD design page** (root `docs/architecture/erd/design/tripplanner.md`, enforced): `tenant_erasure_log` needs a
  table there with primary key `report_id` (tp-030 did not add it); the root PR of this lane does it next to the
  migration copy.
- The CIS RLS catalogue allowlist line for `tripplanner.v_tenant_erasure_report_v1:cis_app` (lane l13 CIS / Infra
  join) stays valid: same view name, same grant.
