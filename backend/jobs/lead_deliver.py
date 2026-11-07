"""AA-1038 R5 slice 3 — job kind ``tp_lead_deliver``: end the hold of every lead older than ``config.LEAD_HOLD_DAYS``
(data standards DM-7.1 row ``tripplanner.outbox``: a lead is kept only until delivered, at most 7 days) and retry the
webhook / e-mail leads that are not delivered yet.

Each run, in this order (ids, counts and error codes only in the result, DM-6.3):

1. **Expire** (``expire`` part) FIRST, in its own transaction, so the retention promise never waits on delivery (a slow
   agency endpoint can use up the Lambda's 300 s; the expiry must already be committed by then): every pending lead
   (any channel, the CRM's included) written before the hold cutoff gets its payload rebuilt from the ids-only
   allowlist (``leads.redacted_payload``), ``redacted_at`` set and ``delivery_error = 'expired'``; at most
   :data:`EXPIRE_MAX` per run (the next run continues), counted per channel (``by_channel``). This is the contract's
   promise to the agency and the traveller, not a clean-up of old history, so it runs live on the schedule; a manual
   run with ``"dry_run": true`` counts only. A lead that expired undelivered means an agency never got a traveller's
   request: the result carries ``attention: ["lead_expired_undelivered"]`` and the scheduled tick fails loudly
   (backend/jobs/handler.py ``attention``), so the jobs Lambda's error alarm fires.
2. **Retry** (``retry`` part): pending ``lead.submitted`` rows of channel webhook / email, oldest first, at most
   :data:`RETRY_MAX` per run and at most :data:`RETRY_BUDGET_S` seconds of wall clock (what is left is ``deferred``
   to the next run). The tenant's route is read again from CIS (``catalog.v_tenant_tripplanner_v1``): a route whose
   channel is no longer the row's is not retried (``route_changed``; the hold ends it). One attempt per row
   (``leads.deliver_now``); delivered = redacted at once, a failure counts the attempt. Circuit breakers for the rest
   of the run: a tenant whose attempt failed is not tried again (``tenant_skipped``: one dead endpoint cannot use up
   the run), and a channel this deployment cannot use (``webhook_key_unavailable`` / ``email_sender_unavailable``)
   stops every row of that channel (``channel_skipped``).

The jobs Lambda binds no tenant (backend/jobs/handler.py): it reads the rows of every tenant through the tp-025 shadow
policy's unset branch. Its role needs the webhook key and SES send as well as the assembly role (AA-CIS-Infra, a
deploy prerequisite of the webhook / e-mail channels); without them every retry stops at the channel breaker and the
expiry still ends the hold on time, loudly.
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timezone
from typing import Any, Callable, Optional

from backend.assembly import leads
from backend.jobs.queue import Job
from backend.jobs.runner import PermanentError
from backend.shared import observability

KIND = "tp_lead_deliver"
MAX_ATTEMPTS = 1
RETRY_MAX = 200
# Wall-clock seconds the retry part may use (the jobs Lambda times out at 300 s, other kinds share the invocation).
RETRY_BUDGET_S = 120.0
# Errors that say this deployment cannot use the channel at all (not one tenant's endpoint).
CHANNEL_DOWN = frozenset({"webhook_key_unavailable", "email_sender_unavailable"})
ATTENTION_EXPIRED = "lead_expired_undelivered"
EXPIRE_MAX = 500
TRIGGERS = ("schedule", "manual")
PAYLOAD_KEYS = frozenset({"trigger", "dry_run"})

RETRY_SQL = (
    "SELECT id, tenant_id::text AS tenant_id, delivery_channel, payload FROM tripplanner.outbox "
    "WHERE event = 'lead.submitted' AND delivery_channel IN ('webhook', 'email') AND delivered_at IS NULL "
    "AND redacted_at IS NULL AND created_at >= $1 ORDER BY id LIMIT $2")
EXPIRE_PICK_SQL = (
    "SELECT id, delivery_channel, payload FROM tripplanner.outbox WHERE event = 'lead.submitted' "
    "AND delivered_at IS NULL AND redacted_at IS NULL AND created_at < $1 ORDER BY id LIMIT $2 FOR UPDATE SKIP LOCKED")
EXPIRE_SQL = (
    "UPDATE tripplanner.outbox SET payload = $2::jsonb, redacted_at = now(), delivery_error = 'expired' "
    "WHERE id = $1 AND delivered_at IS NULL AND redacted_at IS NULL")
MIGRATED_SQL = ("SELECT EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = 'tripplanner.outbox'::regclass "
                "AND attname = 'delivered_at' AND NOT attisdropped)")
COUNT_EXPIRED_SQL = (
    "SELECT count(*)::bigint FROM tripplanner.outbox WHERE event = 'lead.submitted' AND delivered_at IS NULL "
    "AND redacted_at IS NULL AND created_at < $1")


def schedule_key(day: date) -> str:
    return f"{KIND}:schedule:{day.isoformat()}"


def validate_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) - PAYLOAD_KEYS:
        raise PermanentError("bad_payload")
    if payload.get("trigger", "manual") not in TRIGGERS:
        raise PermanentError("bad_payload")
    if "dry_run" in payload and not isinstance(payload["dry_run"], bool):
        raise PermanentError("bad_payload")
    return payload


def _retry_counts() -> dict[str, int]:
    return {"tried": 0, "delivered": 0, "failed": 0, "route_changed": 0, "tenant_skipped": 0, "channel_skipped": 0,
            "deferred": 0}


async def retry(conn: Any, cutoff: datetime, *, deliver: Optional[Callable[..., Any]] = None,
                budget_s: Optional[float] = None, clock: Callable[[], float] = time.monotonic) -> dict[str, int]:
    """One attempt per pending webhook / e-mail lead still inside its hold window, within the time budget."""
    counts = _retry_counts()
    deliver = deliver or leads.deliver_now
    deadline = clock() + (RETRY_BUDGET_S if budget_s is None else budget_s)
    routes: dict[str, Any] = {}
    broken_tenants: set[str] = set()
    broken_channels: set[str] = set()
    for r in await conn.fetch(RETRY_SQL, cutoff, RETRY_MAX):
        if clock() >= deadline:
            counts["deferred"] += 1
            continue
        tid, channel = r["tenant_id"], r["delivery_channel"]
        if channel in broken_channels:
            counts["channel_skipped"] += 1
            continue
        if tid in broken_tenants:
            counts["tenant_skipped"] += 1
            continue
        if tid not in routes:
            try:
                routes[tid] = await leads.load_route(conn, tid)
            except leads.LeadRefused:
                routes[tid] = None
        route = routes[tid]
        if route is None or route.channel != channel:
            counts["route_changed"] += 1
            continue
        payload = json.loads(r["payload"]) if isinstance(r["payload"], str) else dict(r["payload"])
        counts["tried"] += 1
        error = await deliver(conn, r["id"], route, payload)
        counts["failed" if error else "delivered"] += 1
        if error in CHANNEL_DOWN:
            broken_channels.add(channel)
        elif error:
            broken_tenants.add(tid)
    return counts


async def expire(conn: Any, cutoff: datetime, *, dry_run: bool) -> dict[str, Any]:
    """End the hold of the leads written before ``cutoff`` (ids-only payload, redacted_at, 'expired')."""
    if dry_run:
        return {"candidates": int(await conn.fetchval(COUNT_EXPIRED_SQL, cutoff) or 0), "expired": 0, "by_channel": {}}
    expired = 0
    by_channel: dict[str, int] = {}
    async with conn.transaction():
        rows = await conn.fetch(EXPIRE_PICK_SQL, cutoff, EXPIRE_MAX)
        for r in rows:
            done = await conn.execute(EXPIRE_SQL, r["id"], json.dumps(leads.redacted_payload(r["payload"])))
            n = int(str(done).rsplit(" ", 1)[-1] or 0)
            expired += n
            if n:
                channel = str(r["delivery_channel"] or "unknown")
                by_channel[channel] = by_channel.get(channel, 0) + n
    return {"candidates": len(rows), "expired": expired, "by_channel": by_channel}


async def run(pool: Any, job: Job, *, now: Optional[datetime] = None,
              deliver: Optional[Callable[..., Any]] = None, budget_s: Optional[float] = None,
              clock: Callable[[], float] = time.monotonic) -> dict[str, Any]:
    payload = validate_payload(job.payload)
    dry_run = payload.get("dry_run") is True
    cutoff = leads.hold_cutoff(now or datetime.now(timezone.utc))
    async with pool.acquire() as conn:
        if not await conn.fetchval(MIGRATED_SQL):
            # The code can deploy before tp-026 is applied (MIG-4 order is migration first, but a rollback of the
            # migration must not fail every daily tick): no lead row can exist without it.
            return {"mode": "skipped", "reason": "outbox_not_migrated"}
        # Retention first, committed on its own: delivery can never hold it back.
        expired = await expire(conn, cutoff, dry_run=dry_run)
        retried = (_retry_counts() if dry_run
                   else await retry(conn, cutoff, deliver=deliver, budget_s=budget_s, clock=clock))
    result: dict[str, Any] = {"mode": "dry_run" if dry_run else "live", "hold_days": leads.config.LEAD_HOLD_DAYS,
                              "expire": expired, "retry": retried}
    if expired["expired"]:
        result["attention"] = [ATTENTION_EXPIRED]
    observability.emit("lead_deliver_run", **{f"retry_{k}": v for k, v in retried.items()},
                       expire_candidates=expired["candidates"], expired=expired["expired"],
                       **{f"expired_{k}": v for k, v in sorted(expired["by_channel"].items())}, mode=result["mode"])
    return result
