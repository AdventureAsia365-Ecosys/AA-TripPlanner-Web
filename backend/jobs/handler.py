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
``settings``
    S279 L5 (AA-1096): what this container serves from the admin-editable settings (``backend/shared/settings.py``,
    read from ``catalog.v_app_settings_v1``): each key's value, source (tenant / platform / fallback / env) and version,
    and the daily AI cap with its source (``app_profile`` once the CIS app profile sets it, else ``env``). Read only.
``restore_drill``
    ``{"op": "restore_drill", "payload": {"tenant_id" | "tenant_slug", "backup_id"}}``: the same as an ``enqueue`` of
    ``tp_tenant_restore_drill`` (AA-1093): load a tenant backup into ``pg_temp``, compare, roll back.

AA-1093 (tenant offboarding, part 2 and 3 of the root plan): ``tp_tenant_erase`` (daily: the CIS request feed when it
exists; by ``enqueue``: an operator drill), ``tp_tenant_backup`` (daily: the tenants CIS marks ``is_pilot``, D11,
``no_feed`` until a marker view is readable; by ``enqueue``: one tenant) and ``tp_tenant_restore_drill`` (by
``enqueue`` / op ``restore_drill``).

Every answer is ids, statuses and counts only (DM-6.3); the log lines are ``job_*`` / ``jobs_*`` events.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from typing import Any, Callable, Optional

from backend import config
from backend.jobs import (
    lead_deliver,
    outbox_redact,
    queue,
    rate_counter_purge,
    runner,
    tenant_backup,
    tenant_erase,
    tenant_restore_drill,
)
from backend.shared import app_profile, observability, settings, tenant

# kind -> handler(pool, job). Every kind matches '^tp_[a-z0-9_]+$' (tp-021 job_kind_chk, owners.yaml tp_).
HANDLERS: dict[str, runner.Handler] = {
    outbox_redact.KIND: outbox_redact.run,
    rate_counter_purge.KIND: rate_counter_purge.run,  # AA-1015: tripplanner.ai_rate_counter windows > 2 days
    lead_deliver.KIND: lead_deliver.run,  # AA-1038 slice 3: the lead hold (<= 7 days), rejected leads, retries
    tenant_erase.KIND: tenant_erase.run,  # AA-1093: the TripPlanner part of the tenant offboarding saga
    tenant_backup.KIND: tenant_backup.run,  # AA-1093: one tenant's rows to the gold bucket (exports/, 7 days)
    tenant_restore_drill.KIND: tenant_restore_drill.run,  # AA-1093: a backup into pg_temp, compared, rolled back
}
# Kinds the daily tick queues: kind -> (payload, max_attempts, idempotency key of a UTC day).
DAILY: dict[str, tuple[dict[str, Any], int, Callable[[date], str]]] = {
    outbox_redact.KIND: ({"trigger": "schedule"}, outbox_redact.MAX_ATTEMPTS, outbox_redact.schedule_key),
    rate_counter_purge.KIND: ({"trigger": "schedule"}, rate_counter_purge.MAX_ATTEMPTS,
                              rate_counter_purge.schedule_key),
    lead_deliver.KIND: ({"trigger": "schedule"}, lead_deliver.MAX_ATTEMPTS, lead_deliver.schedule_key),
    tenant_erase.KIND: ({"trigger": "schedule"}, tenant_erase.MAX_ATTEMPTS, tenant_erase.schedule_key),
    # AA-1093 D11: back up the tenants CIS marks is_pilot (no_feed, nothing done, until a marker view is readable)
    tenant_backup.KIND: ({"trigger": "schedule"}, tenant_backup.MAX_ATTEMPTS, tenant_backup.schedule_key),
}
MAX_ATTEMPTS: dict[str, int] = {outbox_redact.KIND: outbox_redact.MAX_ATTEMPTS,
                                rate_counter_purge.KIND: rate_counter_purge.MAX_ATTEMPTS,
                                lead_deliver.KIND: lead_deliver.MAX_ATTEMPTS,
                                tenant_erase.KIND: tenant_erase.MAX_ATTEMPTS,
                                tenant_backup.KIND: tenant_backup.MAX_ATTEMPTS,
                                tenant_restore_drill.KIND: tenant_restore_drill.MAX_ATTEMPTS}
SCHEDULER = "scheduler:tp-jobs-daily"
OPERATOR = "operator:lambda-invoke"
OPS = ("tick", "enqueue", "run", "status", "restore_drill", "settings")
# Ops that are an enqueue of one fixed kind (AA-1093).
OP_KINDS = {"restore_drill": tenant_restore_drill.KIND}
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
    """Ids of the jobs a scheduled run must report: failed, lost, re-queued after an error, a HELD retention run, or a
    result that asks."""
    out = []
    for j in jobs:
        result = j.get("result") or {}
        # A result may also ask for attention itself (tp_lead_deliver: a lead expired undelivered, AA-1038). A run
        # that raised and was re-queued (attempts left, e.g. tp_lead_deliver) is reported now, not only at its end.
        if (j.get("status") in ("failed", "lost") or j.get("error") or result.get("mode") in ATTENTION_MODES
                or result.get("attention")):
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


async def settings_status(pool: Any) -> dict[str, Any]:
    """Op ``settings``: the settings this container serves and the daily AI cap with its source (values, sources and
    versions only)."""
    profile = await app_profile.current(pool)
    cap = profile.default_daily_budget_usd
    daily = ({"cap_usd": cap, "source": "app_profile"} if cap is not None
             else {"cap_usd": float(config.TP_DAILY_SPEND_CAP_USD), "source": "env"})
    out = {"op": "settings", **settings.status(), "daily_cap": {**daily, "profile_source": profile.source}}
    observability.emit("jobs_settings", source=out["source"], daily_cap_source=daily["source"],
                       daily_cap_usd=daily["cap_usd"])
    return out


async def serve(event: dict[str, Any], *, pool: Any, worker: str, time_left: Callable[[], float],
                today: Optional[date] = None) -> dict[str, Any]:
    """Handle one jobs event. ``pool`` is the asyncpg pool (one connection)."""
    op = event.get("op")
    if op not in OPS:
        return {"error": f"unknown op; one of {list(OPS)}"}
    emit = observability.emit
    # AA-1038: tenant-less ON PURPOSE. The kinds that touch tripplanner.outbox (the outbox redaction and the lead
    # delivery) work across every tenant: since tp-028 (FORCE row-level security, strict tenant policies) a connection
    # without a tenant context sees no row there, so each of them opens tenant.system_scope (policy outbox_system)
    # for its run. The queue (tripplanner.job) and the rate-counter purge read tables without row-level security.
    tenant.system_context(op)
    await settings.refresh(pool)                  # S279 L5: e.g. the lead hold of tp_lead_deliver (cached 25 s)
    if op == "settings":
        return await settings_status(pool)
    if op == "status":
        async with pool.acquire() as conn:
            return {"op": op, "jobs": await queue.recent(conn, sorted(HANDLERS))}
    queued: list[str] = []
    if op == "tick":
        queued = await queue_daily(pool, today or datetime.now(timezone.utc).date())
    elif op == "enqueue" or op in OP_KINDS:
        kind = OP_KINDS.get(op) or event.get("kind")
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
