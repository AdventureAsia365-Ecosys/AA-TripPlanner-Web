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
- **Customers and traveller accounts are `keep` (count only).** They have no tenant column and are frozen global
  traveller records (customers since R0, before tenants existed, so 0 for any agency tenant). A traveller's own
  erasure is the DSAR runbook. Their count is in the plan hash, their fingerprint is not (a sign-in elsewhere would
  otherwise move every plan).
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
- **No S3 ListBucket.** Every object name is fixed and the backup job names its prefix; S3 answers 403 for a missing
  key without ListBucket, which the store treats as missing.

## Changed

- New: `backend/jobs/tenant_erase.py`, `tenant_backup.py`, `tenant_restore_drill.py`, `tenant_store.py`,
  `tenant_target.py`; tests `backend/tests/test_aa1093_tenant_erase.py` (offline) and `test_aa1093_tenant_erase_pg.py`.
- `backend/jobs/handler.py`: three kinds, `tp_tenant_erase` in the daily tick, op `restore_drill`.
- `backend/tests/test_aa1038_force_rls.py`: the backup and drill modules join the list of job modules that read tenant
  tables (both open the system scope on every acquire). `test_jobs_pg.py`: four daily kinds, `no_feed` in the status.
- `.github/workflows/ci.yml`: the new PG module in the backend PG step.
- `CONTEXT.md` (jobs section, contracts table), `docs/jobs-runbook.md` (new section).
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
- The apply on Dev is not part of this lane's live steps: it is the end-to-end offboarding drill after the tech lead
  approves the combined fingerprint.
