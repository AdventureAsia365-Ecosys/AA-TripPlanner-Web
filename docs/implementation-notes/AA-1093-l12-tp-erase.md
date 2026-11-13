# AA-1093 lane L4 (S279 lane l12-tp-erase): TripPlanner tenant erasure, backup and restore drill

Plan: root `docs/audits/2026-11-13-S279-session/aa1093-plan.md` (Part 2 offboarding, Part 3 restore, lane L4;
decisions D4 delete every tenant row and merge the spend rows, D8 refuse the pilot and on Dev apply only synthetic
tenants, D11 per-tenant export kept 7 days). Gaps closed: S278 system audit M31 (no TripPlanner tenant erasure) and the
TripPlanner half of M27 (only whole-instance PITR).

Ships as three PRs: the migration-only PR (tp-030, contract), this code PR (after tp-030 is applied on Dev) and an
AA-CIS-Infra PR (the jobs Lambda's S3 grant and two environment variables), plus a root retention row.

## Decisions

- **tp-030 is a contract file, not expand.** The plan table said "all expand-only", but this repo's
  `test_replay_twice_pg.py` re-runs every file, and a re-runnable policy / function / view needs a DO block or
  CREATE OR REPLACE, both refused by the expand lint (and the file REVOKEs default grants on its new objects). So: no
  directive line, MIG-4 (migration-only PR first), applied on Dev by Infra Migration Apply in contract mode.
- **`erase_tenant` is SECURITY INVOKER, not DEFINER.** The lane brief asked for SECURITY DEFINER owned by tp_owner.
  Two rules refuse that: this repo's migration lint (MIG-6, no SECURITY DEFINER) and the Infra expand post-check (a new
  definer function is a reviewed exception). It is also not needed: the runtime role already holds DELETE on the five
  tables, and FORCE row-level security means only the system scope reaches other tenants' rows. The function raises
  42501 unless `app.tenant_scope = 'system'` and no tenant is bound, so the assembly Lambda (which never opens the
  scope, `test_aa1038_force_rls.py`) cannot erase. EXECUTE: tripplanner only (PUBLIC revoked).
- **The function owns the race-free part; Python owns the policy.** Plan hash, lock, compare, deletes and the
  count check run in one function call (one transaction), so a plan cannot move between the hash and the deletes.
  Who may run what (demo, pilot, flag, markers, synthetic, approved plan) is pure Python (`tenant_erase.refusal`), so it
  is unit-tested and logged with a reason code.
- **Signature `erase_tenant(tenant, dry_run, expect_plan DEFAULT NULL)`.** The brief named two parameters; the third
  (default NULL) carries the approved hash into the locked transaction. An apply without it raises 22023.
- **Fingerprint = sha256 of the comma-joined md5 of every row's text, in key order** (`md5(t::text)`): it changes on any
  column change, not only `updated_at` (sessions, events and outbox have no `updated_at`). Plan hash = sha256 over
  `tp-tenant-erase/1|<tenant>|` and every `key|action|rows|fingerprint` line (keys sorted), so a plan of one tenant can
  never match another's. Only hashes and counts leave the database.
- **Spend merge.** `charge()` already adds every turn to the `app` row and the `tenant:<id>` row, so the app sum
  includes the tenant. The merge inserts an app row only where none exists for that stage and month (ON CONFLICT DO
  NOTHING; no double count) and deletes the tenant rows.
- **Customers only this tenant's sessions link are deleted; traveller accounts are `keep` (count only)** (review fix
  pass). The schema reference shows exactly one FK to `tripplanner.customers`: `sessions.customer_id`. So a customer
  linked by the erased tenant's sessions and by no session of another tenant is planned `delete` (count + fingerprint
  in the plan hash) and deleted right after the sessions, inside the same locked call (customers joins the EXCLUSIVE
  lock list); a customer another tenant's session links is not touched. Keeping them would leave personal data (name,
  phone, e-mail) with no session at all. Traveller accounts are global sign-in accounts: `keep`, count only (a sign-in
  elsewhere would otherwise move every plan); a traveller's own erasure is the DSAR runbook.
- **Log `tripplanner.tenant_erasure_log`**: PK (request_id, mode); status per mode (CHECK); a refused row has a reason;
  FORCE RLS with `<log>_system` and `<log>_owner_read` (the report view is an owner view read by cis_app); the runtime
  role has no DELETE (`RUNTIME_DML_EXCEPTIONS` in the replay test). An `applied` row is never overwritten (upsert WHERE).
- **Feed of CIS requests, feature-detected.** `catalog.v_tenant_erasure_v1` is not built yet (CIS lane of AA-1093). The
  daily tick reads it only when it exists with `request_id`, `tenant_id`, `mode`; optional `app` (rows of another app
  skipped), `tripplanner_plan_hash` (the approved hash), `is_pilot`, `is_synthetic`. Missing view = `no_feed` (quiet);
  a view without the required columns = `feed_shape_unknown` (attention). The CIS lane should expose these names, or
  this reader changes with it (one place: `tenant_erase._feed`).
- **Markers.** TripPlanner cannot read `is_pilot` / `is_synthetic` today (`catalog.v_tenant_account_v1` is granted to
  booking only). `tenant_target.markers` reads the feed view, then the account view when granted. Unknown markers:
  DRY RUN and backup go on; an apply is refused (`tenant_markers_unknown`).
- **Refusal order:** `demo_tenant` (the AA-hosted tenant holds the public planner's rows), `pilot_tenant` (every mode),
  then for an apply `apply_disabled`, `tenant_markers_unknown`, `not_synthetic` (outside prod; no ENVIRONMENT counts as
  not prod), `no_approved_plan`, and `plan_changed` from the function. The feed does not retry final refusals
  (`demo_tenant`, `pilot_tenant`, `plan_changed`: CIS needs a new request or approval); the others retry daily.
- **Attention:** a refused apply, a verify residue, `feed_shape_unknown`, a failed drill: the daily tick raises, so the
  existing errors alarm mails.
- **Backup format** mirrors the CIS lane (`cis-tenant-backup/1`) without gzip (TripPlanner tables are small): JSONL of
  `to_jsonb(row)`, manifest, SHA256SUMS written last, its digest in the job result. Backup id = job id. One
  repeatable-read read-only snapshot. Bucket from `TENANT_EXPORT_BUCKET` (public repo: no account id in code).
- **Restore drill = a job kind + an op alias.** `restore_drill` enqueues `tp_tenant_restore_drill` and runs it, so the
  evidence persists in `tripplanner.job` (ids, counts, digests). Temp tables `LIKE` the live ones, loaded with
  `jsonb_populate_recordset`, digest written back the backup's way must equal the file's sha256, live counts reported
  not judged; the transaction always rolls back.
- **No S3 ListBucket; 403 is never "missing"** (review fix pass). Every object name is fixed and the backup job names
  its prefix. NoSuchKey / 404 is `ObjectMissing`; 403 / AccessDenied is `AccessDenied`, which the backup and the drill
  turn into a failed job with the error `s3_access_denied: ...` (a wrong grant must fail loudly, not pass as an expired
  backup). Cost: without ListBucket, S3 answers 403 for an expired key too, so a drill of a backup older than 7 days
  also fails `s3_access_denied`; the backup job row's date tells them apart.
- **Fingerprint settings pinned on the function** (review fix pass): `md5(t::text)` depends on the session's TimeZone,
  DateStyle, IntervalStyle, extra_float_digits and bytea_output (sessions has a bytea column). `erase_tenant` carries
  `SET TimeZone = 'UTC'`, `DateStyle = 'ISO, YMD'`, `IntervalStyle = 'postgres'`, `extra_float_digits = 3`,
  `bytea_output = 'hex'`, so an operator's DRY RUN from any client and the Lambda's apply hash the same rows the same
  way. A pg test proves the hash is equal under three different session settings (and fails without the pin).
- **An apply needs the logged DRY RUN** (review fix pass): besides a 64-hex `expect_plan`, `tenant_erase.erase_one`
  requires a `tenant_erasure_log` row with the same request_id, mode `dry_run`, status `planned`, the same tenant and
  `plan_hash = expect_plan`; else `no_approved_plan` (retried daily like the other non-final refusals). The function
  still re-checks the hash under the lock (`plan_changed`).
- **AA-hosted = `config.is_aa_hosted`** (review fix pass): the `demo_tenant` refusal covers `DEMO_TENANT_ID` and the
  bare path's `DEFAULT_TENANT_ID` (`TP_DEFAULT_TENANT_ID`).
- **Report view for `cis_app` only** (review fix pass): the runtime role has no privilege on
  `v_tenant_erasure_report_v1` (an owner view bypasses the log's RLS; the jobs code reads the log under its system
  policy). The migration's guard block refuses a SELECT grant to `tripplanner`.
- **Daily pilot backup** (review fix pass, D11): `tp_tenant_backup` joins the daily tick with `{"trigger":
  "schedule"}`. It lists the tenants marked `is_pilot` in every readable marker view (`catalog.v_tenant_erasure_v1`,
  `catalog.v_tenant_account_v1`; columns `tenant_id` + `is_pilot`, feature-detected) and backs each one up under the
  job id (one prefix per tenant; result `backups`, which the restore drill reads like a single backup's result). No
  readable marker view: `no_feed`, nothing written, the bucket is not even needed. Retention: the gold bucket's
  `exports-expire` rule already covers `exports/` (AA-CIS-Infra `modules/s3`, 7 days + noncurrent 1 day, merged with
  #803), so no Infra lifecycle follow-up is needed.

## Changed

- New: `backend/jobs/tenant_erase.py`, `tenant_backup.py`, `tenant_restore_drill.py`, `tenant_store.py`,
  `tenant_target.py`; tests `backend/tests/test_aa1093_tenant_erase.py` (offline) and `test_aa1093_tenant_erase_pg.py`.
- `backend/jobs/handler.py`: three kinds, `tp_tenant_erase` in the daily tick, op `restore_drill`.
- `backend/tests/test_aa1038_force_rls.py`: the backup and drill modules join the list of job modules that read tenant
  tables (both open the system scope on every acquire). `test_jobs_pg.py`: four daily kinds, `no_feed` in the status.
- `.github/workflows/ci.yml`: the new PG module in the backend PG step.
- `CONTEXT.md` (jobs section, contracts table), `docs/jobs-runbook.md` (new section).
- Review fix pass (S279): `tenant_erase.refusal` uses `config.is_aa_hosted`; `erase_one` checks the logged DRY RUN
  (`APPROVED_PLAN_SQL`); `tenant_store` splits `ObjectMissing` / `AccessDenied` (put and get) and `denied()`;
  `tenant_backup` takes `{"trigger": "schedule"}` (pilot tenants, `backup_one`), `tenant_target.pilot_tenants`;
  `tenant_restore_drill.entry_of` reads a daily result; `handler.DAILY` has five kinds; tests for each finding
  (offline + PG; `test_jobs_pg.py` expects five daily kinds and two `no_feed`). Migration PR: SET clauses on
  `erase_tenant`, customers `delete` + lock + delete, REVOKE ALL on the report view from `tripplanner` (+ guard), the
  header names the real FKs; `test_tp030_pg.py` seeds three invented customers (target only / shared / other only) and
  tests the settings-independent hash, the customer delete and the view refusal.
- Migration PR: `migrations/030_tenant_erasure.sql`, `migrations/tests/test_tp030_pg.py`, the log in
  `RUNTIME_DML_EXCEPTIONS` of `test_replay_twice_pg.py`.

## Tradeoffs

- **Daily feed vs a faster trigger.** The jobs Lambda only wakes daily; the saga's 30-day window makes a day of latency
  acceptable, and the orchestrator can `enqueue` a schedule-shaped run by invoking `tick`/`enqueue` for a drill.
- **Locks.** An apply takes EXCLUSIVE locks on the four tenant tables and `ai_spend_month` for the length of the call
  (milliseconds on Dev; lock_timeout 5 s). A planner request waits behind it.
- **Plain JSONL in the bucket, not encrypted per tenant** (as the CIS lane): bucket SSE, private access block, TLS-only
  policy, 7-day expiry. The rows hold trip plans and outbox payloads (personal data until the 90-day redaction).

## Should know

- **Thresholds are settings (S279):** this lane adds no business threshold. Operational constants that stay literals
  because TripPlanner has no admin-editable settings mechanism: `MAX_ATTEMPTS` per kind (1 / 2 / 1, like the other
  kinds), the erase's `LOCK_TIMEOUT` 5 s and `STATEMENT_TIMEOUT` 60 s. Follow-up: a TripPlanner settings table (or a
  CIS-owned settings view) before these become tunable.
- **Order on Dev:** tp-030 applied (Migration Apply, contract) → this PR merged and deployed → Infra apply (bucket
  variable + S3 grant). The backup and drill fail with `TENANT_EXPORT_BUCKET is not set` until the Infra apply; the
  erase DRY RUN works without it.
- **Temp tables:** the drill needs TEMP on the database for role tripplanner (PUBLIC default). If Dev revoked it, the
  drill job fails with `InsufficientPrivilegeError`; grant TEMP in a roles file.
- **Rule g of the RLS catalogue guard** (`tripplanner.v_tenant_erasure_report_v1:cis_app`) needs an allowlist line in
  the CIS canonical `tests/integration/rls_catalogue_rules.py` and its Infra byte copy (same for Booking's report view):
  with the CIS saga lane. Until then the nightly schema reference lists it as a finding (not fatal).
- **Customers are not in the tenant backup** (unchanged format `tp-tenant-backup/1`): an erasure now deletes the
  customers only that tenant links, so a real restore (none exists; the drill loads into `pg_temp` without
  constraints) would need those customers back before `sessions.customer_id` can point at them. In practice agency
  tenants have 0 linked customers (customers date from R0, the AA-hosted tenant). Follow-up only if a real restore is
  ever built.
- **Optional Infra follow-up:** grant the jobs role `s3:ListBucket` on `exports/tenants/*/tripplanner/*` if a drill
  should tell "expired" (404) from "denied" (403). Not required: the job row's date already tells them apart.
- The apply on Dev is not part of this lane's live steps: it is the end-to-end offboarding drill after the tech lead
  approves the combined fingerprint.
