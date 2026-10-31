"""AA-861 — the TripPlanner jobs Lambda (``aa-tripplanner-<env>-jobs``): the worker of ``tripplanner.job``.

Root ADR 0003a decision 4: a Lambda-only owner runs its worker as a scheduled Lambda that claims from its own table;
the EventBridge Scheduler rule only wakes it (AA-CIS-Infra ``accounts/aa365/tripplanner_jobs.tf``, daily). No API
Gateway route reaches this function: it is invoked by the scheduler or by an operator's ``aws lambda invoke`` (IAM).
Same zip as browse and assembly (``backend/package_lambdas.sh``), its own role (DB secret only: no Bedrock, no edge
key, no SES).

Events (``op``; the scheduler sends ``{"op": "tick", "source": "aa-861-daily"}``):

``tick``
    Queue today's scheduled run of every daily kind (idempotency key per kind and UTC day, so a second tick the same
    day queues nothing), then run due jobs. Raises :class:`JobsNeedAttention` after the work when a job failed or a
    retention run was HELD, so the invocation counts as an error (the Errors alarm mails the alert topic; the
    scheduler target has no retry, nothing runs twice).
``enqueue``
    ``{"op": "enqueue", "kind": "<kind>", "payload": {...}}``: queue one manual run (the kind validates its payload
    when it runs; a retention payload without ``"dry_run": false`` is a DRY RUN) and run due jobs at once, so the
    answer carries the result. ``created_by`` is ``operator:lambda-invoke``.
``run``
    Run due jobs only.
``status``
    The 20 latest jobs of every registered kind (ids, status, counts; read only).

Every answer is ids, statuses and counts only (DM-6.3); the log lines are ``job_*`` / ``jobs_*`` events.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from typing import Any, Callable, Optional

from backend.jobs import outbox_redact, queue, rate_counter_purge, runner
from backend.shared import observability

# kind -> handler(pool, job). Every kind matches '^tp_[a-z0-9_]+$' (tp-021 job_kind_chk, owners.yaml tp_).
HANDLERS: dict[str, runner.Handler] = {
    outbox_redact.KIND: outbox_redact.run,
    rate_counter_purge.KIND: rate_counter_purge.run,  # AA-1015: tripplanner.ai_rate_counter windows > 2 days
}
# Kinds the daily tick queues: kind -> (payload, max_attempts, idempotency key of a UTC day).
DAILY: dict[str, tuple[dict[str, Any], int, Callable[[date], str]]] = {
    outbox_redact.KIND: ({"trigger": "schedule"}, outbox_redact.MAX_ATTEMPTS, outbox_redact.schedule_key),
    rate_counter_purge.KIND: ({"trigger": "schedule"}, rate_counter_purge.MAX_ATTEMPTS,
                              rate_counter_purge.schedule_key),
}
MAX_ATTEMPTS: dict[str, int] = {outbox_redact.KIND: outbox_redact.MAX_ATTEMPTS,
                                rate_counter_purge.KIND: rate_counter_purge.MAX_ATTEMPTS}
SCHEDULER = "scheduler:tp-jobs-daily"
OPERATOR = "operator:lambda-invoke"
OPS = ("tick", "enqueue", "run", "status")
ATTENTION_MODES = ("held",)


class JobsNeedAttention(Exception):
    """A scheduled run finished with a failed job or a HELD retention run (the message names job ids only)."""


def _worker(context: Any) -> str:
    name = os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "tripplanner-jobs-local")
    request = getattr(context, "aws_request_id", None) or "local"
    return f"{name}:{request}"[:200]


def _time_left(context: Any) -> Callable[[], float]:
    getter = getattr(context, "get_remaining_time_in_millis", None)
    if callable(getter):
        return lambda: getter() / 1000.0
    return lambda: 600.0


def attention(jobs: list[dict[str, Any]]) -> list[str]:
    """Ids of the jobs a scheduled run must report: failed, lost, or a HELD retention run."""
    out = []
    for j in jobs:
        if j.get("status") in ("failed", "lost") or (j.get("result") or {}).get("mode") in ATTENTION_MODES:
            out.append(j["job_id"])
    return out


async def queue_daily(pool: Any, today: date) -> list[str]:
    queued = []
    async with pool.acquire() as conn:
        for kind, (payload, attempts, key) in DAILY.items():
            job_id = await queue.enqueue(conn, kind, dict(payload), created_by=SCHEDULER, idempotency_key=key(today),
                                         max_attempts=attempts)
            if job_id:
                queued.append(job_id)
    return queued


async def serve(event: dict[str, Any], *, pool: Any, worker: str, time_left: Callable[[], float],
                today: Optional[date] = None) -> dict[str, Any]:
    """Handle one jobs event. ``pool`` is the asyncpg pool (one connection)."""
    op = event.get("op")
    if op not in OPS:
        return {"error": f"unknown op; one of {list(OPS)}"}
    emit = observability.emit
    if op == "status":
        async with pool.acquire() as conn:
            return {"op": op, "jobs": await queue.recent(conn, sorted(HANDLERS))}
    queued: list[str] = []
    if op == "tick":
        queued = await queue_daily(pool, today or datetime.now(timezone.utc).date())
    elif op == "enqueue":
        kind = event.get("kind")
        if kind not in HANDLERS:
            return {"error": f"unknown kind; one of {sorted(HANDLERS)}"}
        payload = event.get("payload") or {}
        if not isinstance(payload, dict):
            return {"error": "payload must be an object"}
        if payload.get("trigger") == "schedule":
            return {"error": "trigger 'schedule' is reserved for the daily tick"}
        async with pool.acquire() as conn:
            job_id = await queue.enqueue(conn, kind, payload, created_by=OPERATOR, max_attempts=MAX_ATTEMPTS[kind])
        queued = [job_id] if job_id else []
    emit("jobs_queued", op=op, queued=len(queued))
    ran = await runner.run_due(pool, HANDLERS, worker=worker, time_left=time_left, emit=emit)
    answer = {"op": op, "queued": queued, **ran}
    needs = attention(ran["jobs"])
    emit("jobs_run", op=op, queued=len(queued), ran=len(ran["jobs"]), attention=len(needs), **ran["reaped"])
    if op == "tick" and needs:
        raise JobsNeedAttention(f"jobs need attention: {', '.join(needs)}")
    return answer


def _handle(event, context):  # pragma: no cover - thin AWS adapter
    import asyncio

    from backend.shared import db

    if "requestContext" in event:  # no API Gateway route reaches this function; refuse one anyway
        return {"error": "not an HTTP function"}

    async def go():
        return await serve(event, pool=await db.get_pool(), worker=_worker(context), time_left=_time_left(context))

    # Timestamps in the answer become ISO strings: the Lambda runtime serialises the return value with plain json.
    return json.loads(json.dumps(asyncio.get_event_loop().run_until_complete(go()), default=str))


handler = observability.instrument(_handle, "jobs", lambda method, path: "jobs")
