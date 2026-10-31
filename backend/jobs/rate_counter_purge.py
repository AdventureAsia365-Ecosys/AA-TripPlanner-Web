"""AA-1015 — job kind ``tp_retention_ai_rate_counter``: delete the ``tripplanner.ai_rate_counter`` windows (tp-023)
that started before UTC midnight 2 days ago (data standards DM-7.1 row, DM-7.2 owner-run TTL kind).

The counters hold no personal data (an HMAC of scope + kind + value, a window start and a count) and only the current
window of a key is ever read, so older rows are useless; the purge keeps the table small. Nothing is archived (a
counter is not a record anyone needs back, DM-7.3 does not apply).

Same mode rules as ``tp_retention_outbox_redact`` (backend/jobs/outbox_redact.py) and the CIS / Booking retention
kinds, without the override (a 2-day window needs none to be proven on Dev):

Payload: ``{"trigger": "schedule" | "manual", "dry_run": bool, "max_rows": int, "batch_size": int, "pause_ms": int}``

- manual, ``dry_run`` absent or true -> DRY RUN: counts only.
- manual, ``dry_run`` false -> live: deletes at most ``max_rows`` (required: the count approved from the DRY RUN),
  oldest window first.
- schedule -> DRY RUN until a live run of this kind with the same window has succeeded (the first run is always a DRY
  RUN, destructive-ops.md); then live, unless the candidates exceed 2x the last live run's count (floor
  :data:`GROWTH_FLOOR_ROWS`): then HELD, nothing deleted, a ``rate_counter_purge_held`` line, and the scheduled
  invocation reports failure (the jobs Lambda Errors alarm).

Every run is capped at :data:`RUN_MAX_ROWS`. Batches of at most :data:`BATCH_SIZE_MAX` rows, one transaction each,
``statement_timeout`` 30 s, oldest window first through ``ai_rate_counter_window_idx``, ``FOR UPDATE SKIP LOCKED``
(a limiter upsert on a live window is never waited for: live windows are not candidates anyway). Queued with
``max_attempts = 1`` and a session advisory lock per kind, like the outbox kind. Never ``VACUUM FULL``.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

from backend.jobs.outbox_redact import LOCK_SQL, TODAY_SQL, UNLOCK_SQL, _int, cutoff_for
from backend.jobs.queue import Job
from backend.jobs.runner import JobFailed, PermanentError
from backend.shared import observability

KIND = "tp_retention_ai_rate_counter"
TABLE = "tripplanner.ai_rate_counter"
PART = "windows"
WINDOW_DAYS = 2
WINDOWS = {PART: WINDOW_DAYS}

RUN_MAX_ROWS = 200_000
BATCH_SIZE_MAX = 5_000
BATCH_STATEMENT_TIMEOUT = "30s"
GROWTH_FACTOR = 2
GROWTH_FLOOR_ROWS = 10_000
DEFAULT_PAUSE_MS = 100
MAX_ATTEMPTS = 1

TRIGGERS = ("schedule", "manual")
PAYLOAD_KEYS = frozenset({"trigger", "dry_run", "max_rows", "batch_size", "pause_ms"})

COUNT_SQL = """
    SELECT count(*)::bigint AS n, min(window_start) AS oldest, max(window_start) AS newest
      FROM tripplanner.ai_rate_counter WHERE window_start < $1
"""
DELETE_BATCH_SQL = """
    WITH doomed AS (
        SELECT key_hash, window_start FROM tripplanner.ai_rate_counter
         WHERE window_start < $1
         ORDER BY window_start
         LIMIT $2
         FOR UPDATE SKIP LOCKED
    )
    DELETE FROM tripplanner.ai_rate_counter c
     USING doomed d
     WHERE c.key_hash = d.key_hash AND c.window_start = d.window_start
"""
REMAINING_SQL = "SELECT count(*)::bigint FROM tripplanner.ai_rate_counter WHERE window_start < $1"
LAST_LIVE_SQL = """
    SELECT result::text FROM tripplanner.job
     WHERE kind = $1 AND status = 'succeeded' AND result ->> 'mode' = 'live'
     ORDER BY finished_at DESC
     LIMIT 1
"""


def validate_payload(payload: Any) -> dict[str, Any]:
    """The payload as a dict, or PermanentError for an unknown key, trigger or type (no value is echoed)."""
    if not isinstance(payload, dict):
        raise PermanentError("the payload must be an object")
    if set(payload) - PAYLOAD_KEYS:
        raise PermanentError(f"unknown payload keys (allowed: {sorted(PAYLOAD_KEYS)})")
    if payload.get("trigger", "manual") not in TRIGGERS:
        raise PermanentError(f"trigger must be one of {list(TRIGGERS)}")
    if "dry_run" in payload and not isinstance(payload["dry_run"], bool):
        raise PermanentError("dry_run must be true or false")
    for key in ("max_rows", "batch_size", "pause_ms"):
        _int(payload, key)
    return payload


def decide_mode(payload: dict[str, Any], candidates: int, prev_live: Optional[dict[str, Any]]) -> tuple[str, int, str]:
    """-> (mode, limit_rows, reason); mode is 'dry_run', 'live' or 'held'. Pure."""
    if payload.get("trigger", "manual") != "schedule":
        if payload.get("dry_run", True) is not False:
            return "dry_run", 0, "manual dry run"
        max_rows = payload.get("max_rows")
        if not isinstance(max_rows, int) or isinstance(max_rows, bool) or max_rows < 1:
            raise PermanentError("a live manual run needs max_rows (the approved count from a DRY RUN)")
        return "live", min(max_rows, RUN_MAX_ROWS), "manual live run, approved max_rows"
    if prev_live is None:
        return "dry_run", 0, "no approved live run of this kind yet (first run is a DRY RUN)"
    if (prev_live.get("windows") or {}) != WINDOWS:
        return "dry_run", 0, "window changed since the last live run (DRY RUN again)"
    prev = int(prev_live.get("affected_total") or 0)
    allowed = max(GROWTH_FACTOR * prev, GROWTH_FLOOR_ROWS)
    if candidates > allowed:
        return ("held", 0, f"{candidates} candidates > {GROWTH_FACTOR}x the last live run ({prev}, floor "
                           f"{GROWTH_FLOOR_ROWS}); needs a manual live run")
    return "live", min(candidates, RUN_MAX_ROWS), "scheduled live run within the growth limit"


def summary_line(result: dict[str, Any]) -> str:
    if result["mode"] == "live":
        return f"deleted {result['affected_total']} windows; {result.get('remaining')} older than the cutoff remain"
    if result["mode"] == "failed":
        return f"failed after {result['affected_total']} windows: {result.get('error', '')}"
    return f"{result['mode']}: {result['candidates_total']} windows older than the cutoff; nothing deleted"


async def _delete(conn: Any, cut: Any, progress: dict[str, Any], *, limit_rows: int, batch_size: int,
                  pause_s: float) -> None:
    """Oldest windows first, one transaction per batch; `progress` keeps affected_total / batches of the committed
    batches (a failed run reports how far it got)."""
    batch_size = max(1, min(int(batch_size), BATCH_SIZE_MAX))
    while progress["affected_total"] < limit_rows:
        n = min(batch_size, limit_rows - progress["affected_total"])
        async with conn.transaction():
            await conn.execute(f"SET LOCAL statement_timeout = '{BATCH_STATEMENT_TIMEOUT}'")
            status = await conn.execute(DELETE_BATCH_SQL, cut, n)
        touched = int(status.split()[-1])
        if not touched:
            break
        progress["affected_total"] += touched
        progress["batches"] += 1
        if pause_s:
            await asyncio.sleep(pause_s)


async def run(pool: Any, job: Job) -> dict[str, Any]:
    """The kind handler (runner signature)."""
    payload = validate_payload(job.payload or {})
    async with pool.acquire() as conn:
        if not await conn.fetchval(LOCK_SQL, KIND):
            raise PermanentError(f"another {KIND} run holds the lock; this one did nothing")
        try:
            return await _run_locked(conn, job, payload)
        finally:
            await conn.fetchval(UNLOCK_SQL, KIND)


async def _run_locked(conn: Any, job: Job, payload: dict[str, Any]) -> dict[str, Any]:
    cut = cutoff_for(await conn.fetchval(TODAY_SQL), WINDOW_DAYS)
    row = await conn.fetchrow(COUNT_SQL, cut)
    candidates = int(row["n"])
    raw = await conn.fetchval(LAST_LIVE_SQL, KIND)
    prev = json.loads(raw) if raw else None
    mode, limit_rows, reason = decide_mode(payload, candidates, prev)
    result: dict[str, Any] = {
        "kind": KIND, "table": TABLE, "action": "delete", "mode": mode, "reason": reason,
        "trigger": payload.get("trigger", "manual"), "windows": WINDOWS, "cutoff": cut.isoformat(),
        "candidates_total": candidates,
        "oldest": row["oldest"].isoformat() if row["oldest"] else None,
        "newest": row["newest"].isoformat() if row["newest"] else None,
        "limit_rows": limit_rows, "previous_live_affected": (prev or {}).get("affected_total"),
        "affected_total": 0, "batches": 0,
    }
    if mode == "held":
        observability.emit("rate_counter_purge_held", job_id=job.id, candidates=candidates,
                           previous_live_affected=result["previous_live_affected"])
    if mode != "live":
        result["summary"] = summary_line(result)
        return result
    pause_s = max(0, payload.get("pause_ms", DEFAULT_PAUSE_MS)) / 1000.0
    try:
        await _delete(conn, cut, result, limit_rows=limit_rows,
                      batch_size=payload.get("batch_size") or BATCH_SIZE_MAX, pause_s=pause_s)
    except Exception as exc:
        failed = {**result, "mode": "failed", "error": type(exc).__name__}
        failed["summary"] = summary_line(failed)
        observability.emit("rate_counter_purge_failed", job_id=job.id, error=type(exc).__name__)
        raise JobFailed(type(exc).__name__, failed) from exc
    result["remaining"] = int(await conn.fetchval(REMAINING_SQL, cut))
    result["summary"] = summary_line(result)
    return result


def schedule_key(day: Any) -> str:
    return f"{KIND}:schedule:{day.isoformat()}"
