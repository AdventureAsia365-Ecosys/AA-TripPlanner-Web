# Jobs Lambda runbook — tripplanner.job and the outbox PII redaction

The jobs Lambda `aa-tripplanner-dev-jobs` (AA-861) is the worker of `tripplanner.job` (migration tp-021). An
EventBridge Scheduler rule wakes it once a day at 19:30 UTC with `{"op": "tick"}`, a second one every 5 minutes with
`{"op": "erasure_feed"}` (the CIS erasure requests only, AA-1093; see "Tenant erasure" below); an operator invokes it
directly (IAM, no HTTP route). Its kinds:

- `tp_retention_outbox_redact`: the personal data in `tripplanner.outbox.payload` (customer name, email, phone,
  signed-in identity, custom day notes) is removed 90 days after the row was written (data standards DM-6.6 / DM-7.2).
  The row, its `idempotency_key` and `schema_version` stay.
- `tp_retention_ai_rate_counter` (AA-1015, tp-023): the windows of `tripplanner.ai_rate_counter` (the AI route
  limiter's counters: an HMAC key, a window start and a count, no personal data) that started before UTC midnight
  2 days ago are deleted. Same mode rules: the daily run is a DRY RUN until a manual live run succeeded, then live,
  HELD above 2x the last live run (floor 10,000 rows). No override. First live run:
  `aws lambda invoke --function-name aa-tripplanner-dev-jobs --cli-binary-format raw-in-base64-out --payload '{"op":"enqueue","kind":"tp_retention_ai_rate_counter","payload":{"dry_run":true}}' --profile aa365-admin --region us-west-1 /tmp/tp-jobs.json && cat /tmp/tp-jobs.json`,
  then the same with `"payload":{"dry_run":false,"max_rows":N}` (N = the approved `candidates_total`). Log lines:
  `rate_counter_purge_held`, `rate_counter_purge_failed`.

Commands are single-line (WSL2 rule). They need the acc2 admin profile; the function name is the same in every
command. Answers carry ids, statuses and counts only.

## First deploy (live order, once)

Three repos ship this together, and only one order works. Do steps 3 to 6 within one day, between two ticks: the
daily tick fires at 19:30 UTC (02:30 ICT) inside a 30-minute window, so start after 20:00 UTC and finish before
19:30 UTC (in practice: one Vietnam working day).

1. Merge the TripPlanner tp-021 PR (migration only).
2. Merge the AA-CIS-Infra PR (byte copy of tp-021, `tripplanner.job` in the registry, the jobs Lambda). Its Migration
   Copy Guard compares the copy with tp-021 on TripPlanner `main`, so it goes after step 1.
3. DB Migrate on AA-CIS-Infra: `check`, then `apply` tp-021. It has no `-- migrate: expand` header, so it is a contract
   file: dispatch the apply by hand. Confirm `tp-021` is the latest TripPlanner version.
4. Terraform apply of AA-CIS-Infra through its CI workflow. It creates `aa-tripplanner-dev-jobs` with a placeholder zip,
   the deploy grant, the schedule and two alarms. Between this step and step 6, a tick would invoke the placeholder,
   fail with an import error (it writes nothing) and fire the errors alarm.
   `aa-tripplanner-dev-jobs-not-run` (missing data = breaching) is in ALARM as soon as it is created and stays there
   until the first invocation (step 6).
5. Rebase this code PR on `main` (it was stacked on the tp-021 commit, now squash-merged), re-run CI, merge it through
   the merge queue.
6. The merge deploys the three functions (`deploy-lambdas.yml`; its preflight fails before any upload when step 4 is
   missing). After the post-deploy verify is green, invoke `{"op":"status"}` once (command below): the answer lists
   no jobs and the invocation clears the not-run alarm. Then run the DRY RUN (step 1 of the next section) and check
   that `aa-tripplanner-dev-jobs-errors` stays OK.

Wrong orders and what they break: code deployed before step 4 → the preflight stops the deploy (nothing updated);
step 4 without step 3 or step 6 → the next tick fails (`UndefinedTable` or import error) and the errors alarm fires.

## Read the latest jobs

```bash
aws lambda invoke --function-name aa-tripplanner-dev-jobs --cli-binary-format raw-in-base64-out --payload '{"op":"status"}' --profile aa365-admin --region us-west-1 /tmp/tp-jobs.json && cat /tmp/tp-jobs.json
```

Each job shows `status` (`succeeded` / `failed` / `queued` / `running` / `cancelled`) and, for a retention run,
`result.mode` (`dry_run`, `live`, `held`, `failed`), `candidates_total`, `candidates_with_contact`, `affected_total`,
`remaining_with_contact` and `summary`.

## First real run (destructive-ops procedure)

1. **DRY RUN** (writes nothing):

   ```bash
   aws lambda invoke --function-name aa-tripplanner-dev-jobs --cli-binary-format raw-in-base64-out --payload '{"op":"enqueue","kind":"tp_retention_outbox_redact","payload":{"dry_run":true}}' --profile aa365-admin --region us-west-1 /tmp/tp-jobs.json && cat /tmp/tp-jobs.json
   ```

   Before ~01/2027 no Dev row is 90 days old, so this counts 0. To prove the counts now, use an explicit age
   (still a DRY RUN, no gate needed):

   ```bash
   aws lambda invoke --function-name aa-tripplanner-dev-jobs --cli-binary-format raw-in-base64-out --payload '{"op":"enqueue","kind":"tp_retention_outbox_redact","payload":{"trigger":"manual_override","older_than_days":3}}' --profile aa365-admin --region us-west-1 /tmp/tp-jobs.json && cat /tmp/tp-jobs.json
   ```

2. **Approval:** the tech lead reads `candidates_total` / `candidates_with_contact` and approves a `max_rows`.
3. **Live run** with the approved count (90-day window):

   ```bash
   aws lambda invoke --function-name aa-tripplanner-dev-jobs --cli-binary-format raw-in-base64-out --payload '{"op":"enqueue","kind":"tp_retention_outbox_redact","payload":{"dry_run":false,"max_rows":N}}' --profile aa365-admin --region us-west-1 /tmp/tp-jobs.json && cat /tmp/tp-jobs.json
   ```

   `result.remaining_with_contact` must be 0 when `max_rows` covered every candidate.
4. **Independent verification** (a new connection; DB Migrate `mode=run-script`, since the `tripplanner` schema is
   not readable from the CIS container): rows older than the cutoff that still hold contact data must be 0.

   ```sql
   SELECT count(*) FROM tripplanner.outbox o WHERE o.created_at < date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC' - interval '90 days' AND (o.payload -> 'customer' ->> 'name' IS NOT NULL OR o.payload -> 'customer' ->> 'email' IS NOT NULL OR o.payload -> 'customer' ->> 'phone' IS NOT NULL OR coalesce(o.payload -> 'customer' -> 'identity', 'null'::jsonb) <> 'null'::jsonb);
   ```

After the first approved live run the daily tick runs live by itself, and stops (HELD) when a run would touch more
than 2x the previous live run's rows (floor 1,000). A HELD run writes nothing; repeat steps 1-4.

## Live proof run before rows are 90 days old (Dev only)

A live `manual_override` redacts rows younger than 90 days. It is refused unless the jobs Lambda has
`RETENTION_OVERRIDE_ENABLED=true` (AA-CIS-Infra `var.tripplanner_jobs_retention_override_enabled`, default false) and
ENVIRONMENT is dev, `older_than_days >= 7` and `1 <= max_rows <= 1000`. Open the flag only for an approved run, then
close it. An override run never counts as the last live run: the daily tick stays a DRY RUN until a normal live run.

## Alarms

- `aa-tripplanner-dev-jobs-errors`: a tick failed, a job failed, or a retention run was HELD. Read the `status` and
  the log group `/aws/lambda/aa-tripplanner-dev-jobs` (`job_finished`, `jobs_run`, `outbox_redact_held`,
  `outbox_redact_failed`, `rate_counter_purge_held`, `rate_counter_purge_failed` lines). A tick before tp-023 is
  applied fails the rate-counter kind (`UndefinedTableError`): apply tp-023, the next tick is clean. A failed live run keeps the batches it committed; the job result says how many.
- `aa-tripplanner-dev-jobs-not-run`: no daily tick for 2 days (the daily schedule disabled or broken). It counts the
  tick's `jobs_run` log line with `"op": "tick"` (metric `AA/TripPlanner` `JobsTickRuns`), not invocations: the
  5-minute erasure feed invokes the function all day and would hide a stopped tick. Missing data is breaching, so it
  is also in ALARM from its creation until the first tick (see "First deploy", step 6; invoke `{"op":"tick"}` once
  to clear it at once).
- An erasure feed run that fails (the database unreachable, a failed or refused-apply erase job) raises too and
  fires `aa-tripplanner-dev-jobs-errors`; read the `jobs_run` line with `"op": "erasure_feed"`.

## What stays working after a redaction

- The keys `id` and `idempotency_key` are unchanged, and the columns of `tripplanner.v_inquiry_outbox` did not change.
  (AA-Booking read that view by cursor until the inquiry.submitted contract was retired; tp-027 revoked its grant.)
- A second hand-off with the same key is still refused by `outbox_idempotency_key_key` (`ON CONFLICT DO NOTHING`).

## Tenant erasure, backup and restore drill (AA-1093)

Three kinds of the tenant offboarding saga (root plan `docs/audits/2026-11-13-S279-session/aa1093-plan.md`, Parts 2
and 3; contract `tenant.erasure.v1`). Migrations tp-030 and tp-031 (the append-only report log,
`tripplanner.erase_tenant(tenant, round, dry_run, expect_plan)`, the CIS report view) must be applied first; before
tp-031 the erase fails with `UndefinedFunctionError` (nothing is deleted).

- `tp_tenant_erase`: deletes every row of one tenant in `sessions`, `trip_drafts`, `trip_events`, `outbox`, the
  `customers` only its sessions link, and merges its `ai_spend_month` rows into the app scope. Modes `dry_run` (the
  plan: contract entries `{table, action, rows, digest}` and the `plan_hash` of the round; nothing deleted), `apply`
  (needs the approved `plan_hash` of the round AND an ok DRY RUN report of the same `request_id`, round, tenant and
  source (CIS or operator) with that hash, else `dry_run_missing`; a moved plan is refused `plan_changed`; an ok apply
  of the same request, round and source is not run again, a new round applies its own plan), `verify` (every delete / merge count 0
  and a census of the `pii:` columns finding 0, else `census_not_zero`). Each run appends one report to
  `tripplanner.tenant_erasure_log` (status `ok` / `refused` / `failed` with a contract error code), which CIS reads
  through `tripplanner.v_tenant_erasure_report_v1`. The op `erasure_feed` (every 5 minutes) reads the CIS request
  view `catalog.v_tenant_erasure_v1` once it exists (`no_feed` until then) and queues one job per new request, round
  and phase (idempotency key `tp_tenant_erase:<request>:<round>:<phase>`), then runs due erase jobs only; so a request
  CIS opens (or its "Run now") gets TripPlanner's report within about 5 minutes. The daily tick reads the same view
  and runs any request still without a report (the safety net, e.g. after a feed job's Lambda was killed).
  Refused always: the AA-hosted tenant (`aa_hosted_refused`) and the pilot (`pilot_refused`). An apply also needs
  `TENANT_ERASE_APPLY_ENABLED=true` on the function (Terraform, default off), CIS markers for the tenant
  (`tenant_markers_unknown`), outside prod a tenant CIS marks synthetic (`not_synthetic`), and for a CIS request an
  unexpired approval (`approval_expired`). A failed report or a refused apply fails the tick (errors alarm).
- `tp_tenant_backup`: one tenant's four tables as JSONL + `manifest.json` + `SHA256SUMS` to the gold bucket,
  `exports/tenants/<tenant>/tripplanner/<job id>/` (the bucket rule deletes `exports/` after 7 days). The function
  needs `TENANT_EXPORT_BUCKET` (Terraform) and its role may write and read that prefix only. The daily tick backs up
  every tenant CIS marks `is_pilot` (`no_feed` while no marker view is readable); a job that fails with
  `s3_access_denied` means S3 refused the call (the role grant, or the bucket policy), never "file missing".
- `tp_tenant_restore_drill` (op `restore_drill`): checks the files against `SHA256SUMS` and the backup job's digest,
  loads every table into `pg_temp`, compares counts and digests with the manifest and with live rows, and rolls back.
  It never writes a live table. `passed: false` fails the tick.

Commands (a tenant by `tenant_id` or `tenant_slug`; every answer is ids, counts and hashes):

```bash
aws lambda invoke --function-name aa-tripplanner-dev-jobs --cli-binary-format raw-in-base64-out --payload '{"op":"enqueue","kind":"tp_tenant_backup","payload":{"tenant_slug":"<slug>"}}' --profile aa365-admin --region us-west-1 /tmp/tp-jobs.json && cat /tmp/tp-jobs.json
aws lambda invoke --function-name aa-tripplanner-dev-jobs --cli-binary-format raw-in-base64-out --payload '{"op":"restore_drill","payload":{"tenant_slug":"<slug>"}}' --profile aa365-admin --region us-west-1 /tmp/tp-jobs.json && cat /tmp/tp-jobs.json
aws lambda invoke --function-name aa-tripplanner-dev-jobs --cli-binary-format raw-in-base64-out --payload '{"op":"enqueue","kind":"tp_tenant_erase","payload":{"tenant_slug":"<slug>","mode":"dry_run"}}' --profile aa365-admin --region us-west-1 /tmp/tp-jobs.json && cat /tmp/tp-jobs.json
```

Read the CIS request feed now (what the 5-minute schedule does; a drill need not wait for it):

```bash
aws lambda invoke --function-name aa-tripplanner-dev-jobs --cli-binary-format raw-in-base64-out --payload '{"op":"erasure_feed"}' --profile aa365-admin --region us-west-1 /tmp/tp-jobs.json && cat /tmp/tp-jobs.json
```

The answer: `feed` (`no_feed` before CIS ships the view, `feed_shape_unknown` when it lacks a contract column, else
`feed`), `due` (requests without a report in their round and phase), `queued` (the job ids this run queued; empty
when an earlier run already queued them) and `jobs` (each erase job run, `result.outcome` `ran` / `superseded` /
`not_due` / `already_reported`, `result.requests` the report). A `TooManyRequestsException` means a scheduled run holds
the one slot (reserved concurrency 1): retry a few seconds later. An `enqueue` with `"trigger":"feed"` or
`"trigger":"schedule"` is refused: those payloads belong to the schedules.

An operator run uses one request id per tenant (a UUIDv5 of the tenant, the `request_id` of the DRY RUN's report) and
round 1 unless the payload names `request_id` / `round`. A `request_id` of a CIS request (a row of
`catalog.v_tenant_erasure_v1`) fails the job before anything runs: an operator report never lands in a CIS request,
so use the default id or a fresh one. An operator apply (a drill, after the tech lead approved the
DRY RUN's `plan_hash`) adds `"mode":"apply","expect_plan":"<plan_hash>"`; then `"mode":"verify"`. Read the reports
as `cis_app`: `SELECT mode, status, error_code, plan_hash FROM tripplanner.v_tenant_erasure_report_v1 WHERE tenant_id
= '<tenant>' ORDER BY reported_at`. On Dev it runs only for a tenant CIS marks synthetic, and only while `TENANT_ERASE_APPLY_ENABLED` is
`true`; close the flag again after the drill.
