"""AA-861 — SQL operations on ``tripplanner.job`` (tp-021, root ADR 0003a job table template).

Same claim semantics as the other owners' queues (CIS ``shared.job``, AA-Booking ``booking.job``): a due ``queued`` row
is claimed with ``FOR UPDATE SKIP LOCKED`` and leased (``locked_by`` / ``locked_until``); a lease that ran out (the
Lambda timed out or crashed) is reaped on the next run: re-queued while attempts remain, else failed; a retryable
failure is re-queued with exponential backoff through ``run_after``; one job per ``idempotency_key``, ever.

Every statement names its columns: no ``SELECT *`` / ``RETURNING *`` (root ADR 0017 hot tables). ``payload``,
``result`` and ``error`` hold ids, flags and counts only (DM-6.3, tp-021 column comments).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

BACKOFF_BASE_SECONDS = 60
BACKOFF_MAX_SECONDS = 3600
ERROR_MAX_CHARS = 500
LEASE_EXPIRED_ERROR = "lease expired (the runner stopped before finishing the job)"
CANCELLED_ERROR = "cancelled before it started"


@dataclass(frozen=True)
class Job:
    id: str
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    attempt: int = 1
    max_attempts: int = 1
    created_by: Optional[str] = None


def backoff_seconds(attempt: int) -> int:
    """Delay before the next attempt after attempt number ``attempt`` failed: 60 s, 120 s, 240 s … at most 1 h."""
    return min(BACKOFF_BASE_SECONDS * 2 ** max(attempt - 1, 0), BACKOFF_MAX_SECONDS)


def _payload(value: Any) -> dict[str, Any]:
    loaded = json.loads(value) if isinstance(value, str) else value
    return loaded if isinstance(loaded, dict) else {}


def _job(row: Any) -> Job:
    return Job(id=row["id"], kind=row["kind"], payload=_payload(row["payload"]), attempt=row["attempt"],
               max_attempts=row["max_attempts"], created_by=row["created_by"])


ENQUEUE_SQL = """
    INSERT INTO tripplanner.job (kind, payload, idempotency_key, max_attempts, created_by)
    VALUES ($1, $2::jsonb, $3, $4, $5)
    ON CONFLICT (idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING
    RETURNING id::text
"""


async def enqueue(conn: Any, kind: str, payload: dict[str, Any], *, created_by: str,
                  idempotency_key: Optional[str] = None, max_attempts: int = 1) -> Optional[str]:
    """Queue one job; returns its id, or None when a job with the same idempotency key already exists."""
    return await conn.fetchval(ENQUEUE_SQL, kind, json.dumps(payload), idempotency_key, max_attempts, created_by)


# Queued rows with cancel_requested never start; running rows whose lease ran out go back to the queue while attempts
# remain (the attempt was counted at claim time), else fail.
CANCEL_QUEUED_SQL = """
    UPDATE tripplanner.job SET status = 'cancelled', finished_at = now(), error = $1
     WHERE status = 'queued' AND cancel_requested
"""
REAP_SQL = """
    UPDATE tripplanner.job
       SET status = CASE WHEN attempt < max_attempts AND NOT cancel_requested THEN 'queued'
                         WHEN cancel_requested THEN 'cancelled' ELSE 'failed' END,
           finished_at = CASE WHEN attempt < max_attempts AND NOT cancel_requested THEN NULL ELSE now() END,
           run_after = now(),
           locked_by = NULL,
           locked_until = NULL,
           error = $1
     WHERE status = 'running' AND locked_until < now()
"""


async def reap(conn: Any) -> dict[str, int]:
    """Cancel queued jobs that were asked to stop, then reap expired leases. Returns the counts."""
    async with conn.transaction():
        cancelled = await conn.execute(CANCEL_QUEUED_SQL, CANCELLED_ERROR)
        reaped = await conn.execute(REAP_SQL, LEASE_EXPIRED_ERROR)
    return {"cancelled": int(cancelled.split()[-1]), "reaped": int(reaped.split()[-1])}


CLAIM_SQL = """
    WITH next AS (
        SELECT id FROM tripplanner.job
         WHERE status = 'queued' AND run_after <= now() AND NOT cancel_requested AND kind = ANY($3::text[])
         ORDER BY run_after, created_at
         LIMIT 1
         FOR UPDATE SKIP LOCKED
    )
    UPDATE tripplanner.job j
       SET status = 'running', attempt = j.attempt + 1, locked_by = $1,
           locked_until = now() + make_interval(secs => $2), started_at = coalesce(j.started_at, now()), error = NULL
      FROM next
     WHERE j.id = next.id
    RETURNING j.id::text AS id, j.kind, j.payload::text AS payload, j.attempt, j.max_attempts, j.created_by
"""


async def claim(conn: Any, worker: str, kinds: list[str], lease_seconds: int) -> Optional[Job]:
    row = await conn.fetchrow(CLAIM_SQL, worker, float(lease_seconds), kinds)
    return _job(row) if row is not None else None


COMPLETE_SQL = """
    UPDATE tripplanner.job
       SET status = 'succeeded', result = $3::jsonb, finished_at = now(), locked_by = NULL, locked_until = NULL,
           error = NULL
     WHERE id = $1::uuid AND status = 'running' AND locked_by = $2
"""


async def complete(conn: Any, job_id: str, worker: str, result: dict[str, Any]) -> bool:
    """False when the job is no longer this worker's (its lease was reaped meanwhile): the result is not written."""
    status = await conn.execute(COMPLETE_SQL, job_id, worker, json.dumps(result, default=str))
    return status.endswith(" 1")


FAIL_SQL = """
    UPDATE tripplanner.job
       SET status = $3, error = $4, result = coalesce($5::jsonb, result), locked_by = NULL, locked_until = NULL,
           run_after = CASE WHEN $3 = 'queued' THEN now() + make_interval(secs => $6) ELSE run_after END,
           finished_at = CASE WHEN $3 = 'queued' THEN NULL ELSE now() END
     WHERE id = $1::uuid AND status = 'running' AND locked_by = $2
"""


async def fail(conn: Any, job: Job, worker: str, error: str, *, retryable: bool,
               result: Optional[dict[str, Any]] = None) -> str:
    """Re-queue with backoff while attempts remain and the error is retryable, else fail. Returns the new status
    ('queued' / 'failed'), or 'lost' when the lease was reaped meanwhile."""
    new_status = "queued" if retryable and job.attempt < job.max_attempts else "failed"
    status = await conn.execute(FAIL_SQL, job.id, worker, new_status, error[:ERROR_MAX_CHARS],
                                json.dumps(result, default=str) if result is not None else None,
                                float(backoff_seconds(job.attempt)))
    return new_status if status.endswith(" 1") else "lost"


RECENT_SQL = """
    SELECT id::text AS id, kind, status, attempt, created_by, created_at, started_at, finished_at,
           result::text AS result, error
      FROM tripplanner.job
     WHERE kind = ANY($1::text[])
     ORDER BY created_at DESC
     LIMIT $2
"""


async def recent(conn: Any, kinds: list[str], limit: int = 20) -> list[dict[str, Any]]:
    out = []
    for r in await conn.fetch(RECENT_SQL, kinds, limit):
        out.append({
            "job_id": r["id"], "kind": r["kind"], "status": r["status"], "attempt": r["attempt"],
            "created_by": r["created_by"], "created_at": r["created_at"], "started_at": r["started_at"],
            "finished_at": r["finished_at"], "result": _payload(r["result"]) if r["result"] else None,
            "error": r["error"],
        })
    return out
