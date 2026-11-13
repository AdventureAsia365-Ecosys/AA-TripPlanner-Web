# AA-1093 follow-up (S279 lane l25-tp-erase-feed-cadence): TripPlanner reads the erasure feed every 5 minutes

Why: recording the DRILL-01 drill video showed that after the CIS saga's "Run now", CIS and Booking report their DRY
RUN within minutes (Booking's `EraseSchedule` polls every 300 s), but TripPlanner read the CIS request feed only in
the daily tick (`DAILY[tp_tenant_erase]`, payload `{"trigger": "schedule"}`), and an operator `enqueue` with that
trigger is refused. A drill, and any real request, waited up to a day for TripPlanner.

Ships with the AA-CIS-Infra PR of the same lane (the 5-minute schedule and the not-run alarm on the tick's log line).

## Decisions

- **A jobs op `erasure_feed`, plus a payload trigger `feed`.** The op reads `catalog.v_tenant_erasure_v1` (same
  feature detection as the daily path), and for each request due in a (round, phase) that has no CIS report yet
  queues one `tp_tenant_erase` job `{"trigger": "feed", request_id, tenant_id, round, mode}` with the idempotency key
  `tp_tenant_erase:<request>:<round>:<phase>` (Booking's `EraseSchedule` key shape). Then it runs due jobs of
  `tp_tenant_erase` only. One job per request keeps each report on its own job id (as Booking), and a request whose
  job fails does not hold the others.
- **The feed job reads its request again** (newest view row) and writes NO report when the request moved on
  (`superseded`: tenant, round or phase changed, step not active, row gone), when CIS moved `not_before` into the
  future (`not_due`: a refused `not_due` report would close that round and phase for good; the daily tick runs it once
  due) or when a report of that round and mode already exists (`already_reported`: the daily tick ran it first).
- **The daily path stays.** `DAILY[tp_tenant_erase]` with `{"trigger": "schedule"}` still runs every request with no
  report. It is the safety net for a feed job that never wrote a report (Lambda killed: the job is reaped as failed
  after its one attempt and the idempotency key stops the feed from queuing it again).
- **Only erase jobs run on the 5-minute path.** `run_due` gets `{tp_tenant_erase: handler}`: lead retries, retention
  and backups keep the daily cadence (their semantics and alarms were designed for one run a day). The reap at the
  start of `run_due` stays global (cheap; it only re-queues expired leases).
- **Triggers `schedule` and `feed` are refused in an operator `enqueue`** (`RESERVED_TRIGGERS`): an operator who wants
  the feed now invokes `{"op": "erasure_feed"}` instead. `created_by` is `scheduler:tp-erasure-feed` when the event
  carries the schedule's `source` (`aa-1093-erasure-feed`), else `operator:lambda-invoke`.
- **Attention.** `erasure_feed` raises `JobsNeedAttention` like `tick` when an erase job it ran failed or refused an
  apply (the errors alarm). A view without the contract columns (`feed_shape_unknown`) does NOT raise every 5 minutes:
  the daily tick still reports it once a day (attention), so the alarm does not stay in ALARM on a CIS-side change.

## Changed (vs the lane brief)

- Nothing dropped. The brief's "e.g. a new op 'erasure_feed' (or a 'feed' trigger)" became both: the op queues, the
  trigger marks the queued jobs.

## Tradeoffs

- **No new setting.** The cadence is the Infra schedule expression (`tripplanner_jobs_erasure_feed_schedule_expression`,
  `rate(5 minutes)`), a wake-up interval of the scheduler, like Booking's technical `SLOT_SECONDS = 300`. TripPlanner
  has an admin-editable settings reader since S279 L5 (`backend/shared/settings.py`), but an EventBridge schedule is
  not read by the app, so it cannot be a CIS setting. No threshold, limit or retry count was added in code
  (`MAX_ATTEMPTS` of the kind is reused).
- **Cost.** ~288 invocations a day of ~1 s at 256 MB: inside the Lambda and Scheduler free tiers; logs a few MB a
  month (30-day retention); the tick metric filter emits only on the tick's line (one metric-hour a day). About $0.
- **Concurrency.** Reserved concurrency 1 holds: the scheduler invokes asynchronously, so a run that finds the
  function busy (the daily tick, a long apply) waits in Lambda's async queue (max event age 1 h). An operator's
  synchronous invoke can meet a 429 while a scheduled run holds the slot (runbook: retry).

## Should know

- The Infra PR moves the `-not-run` alarm from the `Invocations` metric to a log metric filter on the tick's
  `jobs_run` line (`{ $.evt = "jobs_run" && $.op = "tick" }`); with a 5-minute schedule, invocations no longer prove
  that the daily tick runs.
- Order: deploy this code first, then the Infra apply. The other order is harmless (the old code answers
  `{"error": "unknown op ..."}` without raising), but nothing is queued until the code is live.
- Tests: `backend/tests/test_aa1093_tenant_erase.py` (feed queues once per request/round/phase, no view = `no_feed`
  and nothing queued, feed job skips, operator op and created_by, attention, reserved triggers) and
  `test_aa1093_tenant_erase_pg.py::test_the_erasure_feed_op_queues_each_new_request_once_and_the_daily_tick_adds_nothing`
  (real Postgres, runtime role).
