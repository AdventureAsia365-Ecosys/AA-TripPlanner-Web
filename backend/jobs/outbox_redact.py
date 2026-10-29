"""AA-861 — job kind ``tp_retention_outbox_redact``: redact the personal data of ``tripplanner.outbox`` rows older than
90 days (data standards DM-6.6, DM-7.2; window approved in E3).

A row whose ``created_at`` is before the cutoff (UTC midnight, 90 days before today) and that is not redacted yet gets
its ``payload`` rebuilt from the allowlist of :mod:`backend.jobs.redact` (customer name, email, phone, identity and
custom day notes nulled, every unknown key dropped) and ``redacted_at`` set (tp-021). The row, ``idempotency_key``,
``schema_version`` and ``aggregate_id`` stay, so AA-Booking's dedupe (``booking.inbox_events`` keyed by
``idempotency_key``) and the outbox's own ``ON CONFLICT (idempotency_key)`` keep working. Nothing is deleted, so
nothing is archived (DM-7.3 applies when a delete window is set). Same mode rules as AA-Booking's
``bk_retention_inbox_redact`` (AA-825) and CIS ``services/jobs/retention_job.py``:

Payload: ``{"trigger": "schedule" | "manual" | "manual_override", "dry_run": bool, "max_rows": int,
"batch_size": int, "pause_ms": int, "older_than_days": int (manual_override only)}``

- manual, ``dry_run`` absent or true -> DRY RUN: counts only, nothing written but the job result.
- manual, ``dry_run`` false -> live: redacts at most ``max_rows`` (required: the count the tech lead approved from the
  DRY RUN), oldest first.
- schedule -> DRY RUN until a live run of this kind with the same window has succeeded (the first run is always a DRY
  RUN, destructive-ops.md); then live, unless the candidates exceed 2x the last live run's count (floor
  :data:`GROWTH_FLOOR_ROWS`): then HELD, nothing written, an ``outbox_redact_held`` log line and the scheduled
  invocation reports failure (the jobs Lambda Errors alarm).
- manual_override -> a cutoff of ``older_than_days`` days (whole UTC days) in place of the 90-day window, to prove
  the counts and the redaction on Dev before the first rows reach 90 days (first Dev rows: ~01/2027). A DRY RUN
  override is allowed anywhere (it writes nothing; ``older_than_days`` >= 1). A live override fails closed
  (:func:`override_gate`): only when the raw process environment has ``ENVIRONMENT`` in
  :data:`OVERRIDE_ENVIRONMENTS` **and** :data:`OVERRIDE_FLAG` ``=true`` (Terraform sets it only on the Dev jobs
  Lambda, when asked), ``older_than_days`` >= :data:`OVERRIDE_MIN_AGE_DAYS` and ``1 <= max_rows <=``
  :data:`OVERRIDE_MAX_ROWS` (refused, not capped). It never counts as "the last live run": scheduled runs stay DRY
  RUN and the 2x rule ignores it. (AA-Booking gates its override DRY RUN too; here a DRY RUN never writes, and the
  orchestrator must be able to show counts on Dev without opening the gate.)

Every run is capped at :data:`RUN_MAX_ROWS`; the rest waits for the next run. The last live run is read back from
``tripplanner.job`` (succeeded rows of this kind whose result says ``mode = live``). A future TripPlanner job-retention
kind must keep the latest live run of each retention kind, or the next scheduled run falls back to a DRY RUN (the safe
direction). Retention jobs are queued with ``max_attempts = 1``: they keep no checkpoint, so a retry could exceed the
approved ``max_rows``; a session advisory lock per kind keeps two runs from overlapping.

Batches (Part C C5.4): at most :data:`BATCH_SIZE_MAX` rows per transaction, ``statement_timeout`` 30 s, keyset on the
indexed ``(created_at, id)`` (tp-021 ``outbox_redact_due_idx``), oldest first, ``FOR UPDATE SKIP LOCKED``; the UPDATE
must touch exactly the locked rows or the batch rolls back. Never ``VACUUM FULL``.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from backend.jobs import redact
from backend.jobs.queue import Job
from backend.jobs.runner import JobFailed, PermanentError
from backend.shared import observability

KIND = "tp_retention_outbox_redact"
TABLE = "tripplanner.outbox"
PART = "payload_pii"
WINDOW_DAYS = 90
WINDOWS = {PART: WINDOW_DAYS}

RUN_MAX_ROWS = 50_000
BATCH_SIZE_MAX = 1_000
BATCH_STATEMENT_TIMEOUT = "30s"
GROWTH_FACTOR = 2
GROWTH_FLOOR_ROWS = BATCH_SIZE_MAX
DEFAULT_PAUSE_MS = 200
MAX_ATTEMPTS = 1

OVERRIDE_TRIGGER = "manual_override"
OVERRIDE_MIN_AGE_DAYS = 7
OVERRIDE_MAX_ROWS = 1_000
OVERRIDE_ENVIRONMENTS = frozenset({"dev", "test", "local"})
OVERRIDE_FLAG = "RETENTION_OVERRIDE_ENABLED"

TRIGGERS = ("schedule", "manual", OVERRIDE_TRIGGER)
PAYLOAD_KEYS = frozenset({"trigger", "dry_run", "max_rows", "batch_size", "pause_ms", "older_than_days"})

_INDEX_RE = re.compile(r"\[\d+\]")

Environ = Mapping[str, str]


class CountMismatch(Exception):
    """The UPDATE did not touch exactly the rows that were locked."""


class OverrideRefused(ValueError):
    """A manual_override request in a shape or environment that is refused."""


# ── Payload and mode (pure) ───────────────────────────────────────────────────


def _int(payload: dict[str, Any], key: str) -> Optional[int]:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise PermanentError(f"{key} must be an integer")
    return value


def validate_payload(payload: Any) -> dict[str, Any]:
    """The payload as a dict, or PermanentError for an unknown key, trigger or type (no value is echoed)."""
    if not isinstance(payload, dict):
        raise PermanentError("the payload must be an object")
    unknown = set(payload) - PAYLOAD_KEYS
    if unknown:
        raise PermanentError(f"unknown payload keys (allowed: {sorted(PAYLOAD_KEYS)})")
    if payload.get("trigger", "manual") not in TRIGGERS:
        raise PermanentError(f"trigger must be one of {list(TRIGGERS)}")
    if "dry_run" in payload and not isinstance(payload["dry_run"], bool):
        raise PermanentError("dry_run must be true or false")
    for key in ("max_rows", "batch_size", "pause_ms", "older_than_days"):
        _int(payload, key)
    if payload.get("trigger", "manual") != OVERRIDE_TRIGGER and "older_than_days" in payload:
        raise PermanentError(f"older_than_days is only accepted with trigger {OVERRIDE_TRIGGER!r}")
    return payload


def override_gate(environ: Environ) -> str:
    """The environment name when this process may run a LIVE manual override; raises :class:`OverrideRefused`
    otherwise. Reads the raw ``environ`` with no default, so a missing ENVIRONMENT or opt-in flag refuses."""
    env = (environ.get("ENVIRONMENT") or "").strip().lower()
    if not env:
        raise OverrideRefused("a live override run is refused: ENVIRONMENT is not set")
    if env not in OVERRIDE_ENVIRONMENTS:
        raise OverrideRefused(f"a live override run is refused in environment {env!r}")
    if (environ.get(OVERRIDE_FLAG) or "").strip().lower() != "true":
        raise OverrideRefused(f"a live override run is refused: {OVERRIDE_FLAG} is not 'true' in this deployment")
    return env


def override_days(payload: dict[str, Any], environ: Environ) -> Optional[int]:
    """The ``older_than_days`` of a manual_override run (None for any other trigger); raises OverrideRefused."""
    if payload.get("trigger", "manual") != OVERRIDE_TRIGGER:
        return None
    days = payload.get("older_than_days")
    if not isinstance(days, int) or isinstance(days, bool) or days < 1:
        raise OverrideRefused("a manual override needs older_than_days as a whole number of days >= 1")
    if payload.get("dry_run", True) is not False:
        return days
    override_gate(environ)
    if days < OVERRIDE_MIN_AGE_DAYS:
        raise OverrideRefused(f"a live override needs older_than_days >= {OVERRIDE_MIN_AGE_DAYS}")
    max_rows = payload.get("max_rows")
    if not isinstance(max_rows, int) or isinstance(max_rows, bool) or not 1 <= max_rows <= OVERRIDE_MAX_ROWS:
        raise OverrideRefused(f"a live override needs 1 <= max_rows <= {OVERRIDE_MAX_ROWS}")
    return days


def cutoff_for(today: date, days: int) -> datetime:
    """UTC midnight ``days`` days before ``today`` (UTC): every expired day is a whole day."""
    day = today - timedelta(days=days)
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)


def decide_mode(payload: dict[str, Any], candidates: int, prev_live: Optional[dict[str, Any]]) -> tuple[str, int, str]:
    """-> (mode, limit_rows, reason); mode is 'dry_run', 'live' or 'held'. Pure."""
    trigger = payload.get("trigger", "manual")
    if trigger == OVERRIDE_TRIGGER:
        if payload.get("dry_run", True) is not False:
            return "dry_run", 0, "manual override dry run (explicit age)"
        return "live", int(payload["max_rows"]), "manual override live run (explicit age), approved max_rows"
    if trigger != "schedule":
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


# ── SQL ───────────────────────────────────────────────────────────────────────

# A conforming payload holds contact data at redact.CONTACT_PATHS; the DRY RUN reports how many candidates do, and a
# live run reports how many rows older than the cutoff still do afterwards (0 = done; the runbook verification query).
CONTACT_SQL = (
    "(o.payload -> 'customer' ->> 'name' IS NOT NULL OR o.payload -> 'customer' ->> 'email' IS NOT NULL "
    "OR o.payload -> 'customer' ->> 'phone' IS NOT NULL "
    "OR coalesce(o.payload -> 'customer' -> 'identity', 'null'::jsonb) <> 'null'::jsonb)"
)
COUNT_SQL = f"""
    SELECT count(*)::bigint AS n, count(*) FILTER (WHERE {CONTACT_SQL})::bigint AS with_contact,
           min(o.created_at) AS oldest, max(o.created_at) AS newest
      FROM tripplanner.outbox o
     WHERE o.redacted_at IS NULL AND o.created_at < $1
"""
REMAINING_SQL = f"SELECT count(*)::bigint FROM tripplanner.outbox o WHERE o.created_at < $1 AND {CONTACT_SQL}"
BATCH_FIRST_SQL = """
    SELECT o.id, o.created_at AS ts, o.payload::text AS payload
      FROM tripplanner.outbox o
     WHERE o.redacted_at IS NULL AND o.created_at < $1
     ORDER BY o.created_at, o.id
     LIMIT $2
     FOR UPDATE OF o SKIP LOCKED
"""
BATCH_NEXT_SQL = """
    SELECT o.id, o.created_at AS ts, o.payload::text AS payload
      FROM tripplanner.outbox o
     WHERE o.redacted_at IS NULL AND o.created_at < $1 AND (o.created_at, o.id) > ($3, $4)
     ORDER BY o.created_at, o.id
     LIMIT $2
     FOR UPDATE OF o SKIP LOCKED
"""
REDACT_SQL = """
    UPDATE tripplanner.outbox o
       SET payload = u.payload::jsonb, redacted_at = now()
      FROM unnest($1::bigint[], $2::text[]) AS u(id, payload)
     WHERE o.id = u.id AND o.redacted_at IS NULL
"""
LAST_LIVE_SQL = """
    SELECT result::text FROM tripplanner.job
     WHERE kind = $1 AND status = 'succeeded' AND result ->> 'mode' = 'live'
       AND coalesce(result ->> 'trigger', 'manual') <> 'manual_override'
     ORDER BY finished_at DESC
     LIMIT 1
"""
TODAY_SQL = "SELECT (now() AT TIME ZONE 'UTC')::date"
LOCK_SQL = "SELECT pg_try_advisory_lock(hashtextextended($1, 0))"
UNLOCK_SQL = "SELECT pg_advisory_unlock(hashtextextended($1, 0))"


@dataclass
class PartResult:
    part: str
    window_days: int
    cutoff: str
    candidates: int = 0
    candidates_with_contact: int = 0
    oldest: Optional[str] = None
    newest: Optional[str] = None
    affected: int = 0
    batches: int = 0
    fields_redacted: dict[str, int] = field(default_factory=dict)


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


async def last_live(conn: Any) -> Optional[dict[str, Any]]:
    raw = await conn.fetchval(LAST_LIVE_SQL, KIND)
    return json.loads(raw) if raw else None


async def count(conn: Any, cut: datetime, window_days: int) -> PartResult:
    row = await conn.fetchrow(COUNT_SQL, cut)
    return PartResult(part=PART, window_days=window_days, cutoff=cut.isoformat(), candidates=int(row["n"]),
                      candidates_with_contact=int(row["with_contact"]), oldest=_iso(row["oldest"]),
                      newest=_iso(row["newest"]))


async def process(conn: Any, cut: datetime, res: PartResult, *, limit_rows: int, batch_size: int,
                  pause_s: float) -> PartResult:
    """Redact up to ``limit_rows`` candidates, oldest first, one transaction per batch, into ``res``."""
    batch_size = max(1, min(int(batch_size), BATCH_SIZE_MAX))
    cursor: Optional[tuple[datetime, int]] = None
    while res.affected < limit_rows:
        n = min(batch_size, limit_rows - res.affected)
        async with conn.transaction():
            await conn.execute(f"SET LOCAL statement_timeout = '{BATCH_STATEMENT_TIMEOUT}'")
            if cursor is None:
                rows = await conn.fetch(BATCH_FIRST_SQL, cut, n)
            else:
                rows = await conn.fetch(BATCH_NEXT_SQL, cut, n, cursor[0], cursor[1])
            if not rows:
                break
            ids, payloads = [], []
            for r in rows:
                rebuilt, paths = redact.redact_payload(json.loads(r["payload"]))
                for path in paths:
                    label = _INDEX_RE.sub("[]", path)
                    res.fields_redacted[label] = res.fields_redacted.get(label, 0) + 1
                ids.append(r["id"])
                payloads.append(json.dumps(rebuilt))
            status = await conn.execute(REDACT_SQL, ids, payloads)
            touched = int(status.split()[-1])
            if touched != len(rows):
                raise CountMismatch(f"{TABLE}: locked {len(rows)} rows, redact touched {touched}")
        res.batches += 1
        res.affected += touched
        cursor = (rows[-1]["ts"], rows[-1]["id"])
        if pause_s:
            await asyncio.sleep(pause_s)
    return res


def summary_line(result: dict[str, Any]) -> str:
    override = result.get("override")
    prefix = f"override older_than_days {override['older_than_days']}: " if override else ""
    if result["mode"] == "live":
        return (f"{prefix}redacted {result['affected_total']} rows; {result.get('remaining_with_contact')} rows "
                f"before the cutoff still hold contact data")
    if result["mode"] == "failed":
        return f"{prefix}failed after {result['affected_total']} rows: {result.get('error', '')}"
    return (f"{prefix}{result['mode']}: {result['candidates_total']} candidates "
            f"({result['candidates_with_contact']} with contact data); nothing written")


# ── Run ───────────────────────────────────────────────────────────────────────


async def run(pool: Any, job: Job, *, environ: Optional[Environ] = None) -> dict[str, Any]:
    """The kind handler (runner signature). Holds the pool's connection for the whole run (advisory lock + batches)."""
    payload = validate_payload(job.payload or {})
    environ = environ if environ is not None else os.environ
    async with pool.acquire() as conn:
        if not await conn.fetchval(LOCK_SQL, KIND):
            raise PermanentError(f"another {KIND} run holds the lock; this one did nothing")
        try:
            return await _run_locked(conn, job, payload, environ)
        finally:
            await conn.fetchval(UNLOCK_SQL, KIND)


async def _run_locked(conn: Any, job: Job, payload: dict[str, Any], environ: Environ) -> dict[str, Any]:
    trigger = payload.get("trigger", "manual")
    try:
        days = override_days(payload, environ)
    except OverrideRefused as exc:
        raise PermanentError(str(exc)) from None
    today = await conn.fetchval(TODAY_SQL)
    policy_cut = cutoff_for(today, WINDOW_DAYS)
    cut = cutoff_for(today, days) if days is not None else policy_cut
    part = await count(conn, cut, days if days is not None else WINDOW_DAYS)
    prev = await last_live(conn)
    mode, limit_rows, reason = decide_mode(payload, part.candidates, prev)
    result: dict[str, Any] = {
        "kind": KIND,
        "table": TABLE,
        "action": "redact",
        "mode": mode,
        "reason": reason,
        "trigger": trigger,
        "windows": WINDOWS,
        "cutoff": cut.isoformat(),
        "candidates_total": part.candidates,
        "candidates_with_contact": part.candidates_with_contact,
        "limit_rows": limit_rows,
        "previous_live_affected": (prev or {}).get("affected_total"),
        "affected_total": 0,
        "parts": [asdict(part)],
    }
    if days is not None:
        result["override"] = {
            "older_than_days": days,
            "cutoff": cut.isoformat(),
            "policy_cutoff": policy_cut.isoformat(),
            "min_age_days_live": OVERRIDE_MIN_AGE_DAYS,
            "max_rows_cap": OVERRIDE_MAX_ROWS,
        }
    if mode == "held":
        observability.emit("outbox_redact_held", job_id=job.id, candidates=part.candidates,
                           previous_live_affected=result["previous_live_affected"])
        result["summary"] = summary_line(result)
        return result
    if mode == "dry_run":
        result["summary"] = summary_line(result)
        return result

    pause_s = max(0, payload.get("pause_ms", DEFAULT_PAUSE_MS)) / 1000.0
    batch_size = payload.get("batch_size") or BATCH_SIZE_MAX
    try:
        await process(conn, cut, part, limit_rows=limit_rows, batch_size=batch_size, pause_s=pause_s)
    except Exception as exc:
        # Batches already committed stay redacted: the failed job's result says how far it got (class name only,
        # DM-6.3); the job is not retried (max_attempts 1), the next run starts from a fresh count.
        failed = {**result, "mode": "failed", "error": type(exc).__name__, "affected_total": part.affected,
                  "parts": [asdict(part)]}
        failed["summary"] = summary_line(failed)
        observability.emit("outbox_redact_failed", job_id=job.id, affected=part.affected, error=type(exc).__name__)
        raise JobFailed(type(exc).__name__, failed) from exc
    result["parts"] = [asdict(part)]
    result["affected_total"] = part.affected
    result["remaining_with_contact"] = int(await conn.fetchval(REMAINING_SQL, cut))
    result["summary"] = summary_line(result)
    return result


def schedule_key(day: date) -> str:
    return f"{KIND}:schedule:{day.isoformat()}"
