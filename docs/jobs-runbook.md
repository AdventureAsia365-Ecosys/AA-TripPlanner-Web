# Jobs Lambda runbook — tripplanner.job and the outbox PII redaction

The jobs Lambda `aa-tripplanner-dev-jobs` (AA-861) is the worker of `tripplanner.job` (migration tp-021). An
EventBridge Scheduler rule wakes it once a day at 19:30 UTC with `{"op": "tick"}`; an operator invokes it directly
(IAM, no HTTP route). Its only kind today is `tp_retention_outbox_redact`: the personal data in
`tripplanner.outbox.payload` (customer name, email, phone, signed-in identity, custom day notes) is removed 90 days
after the row was written (data standards DM-6.6 / DM-7.2). The row, its `idempotency_key` and `schema_version` stay.

Commands are single-line (WSL2 rule). They need the acc2 admin profile; the function name is the same in every
command. Answers carry ids, statuses and counts only.

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
  `outbox_redact_failed` lines). A failed live run keeps the batches it committed; the job result says how many.
- `aa-tripplanner-dev-jobs-not-run`: no invocation for 2 days (schedule disabled or broken).

## What stays working after a redaction

- AA-Booking reads `tripplanner.v_inquiry_outbox` by cursor and dedupes on `idempotency_key`
  (`booking.inbox_events`); both keys are unchanged, and the view's columns did not change.
- A second hand-off with the same key is still refused by `outbox_idempotency_key_key` (`ON CONFLICT DO NOTHING`).
