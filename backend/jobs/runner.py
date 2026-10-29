"""AA-861 — the TripPlanner job runner: claims due jobs from ``tripplanner.job`` and runs their kind handler.

TripPlanner has no ECS worker (root ADR 0003a decision 4): the runner is the jobs Lambda, woken by an EventBridge
Scheduler rule (daily) or a direct ``lambda invoke``. One run: reap expired leases and cancelled queued jobs, then claim
and run due jobs one after another until none is due, ``max_jobs`` ran, or the Lambda is about to time out.

The lease of a claimed job is the time this invocation has left plus a margin, so a job whose Lambda was killed is
reaped by the next run (re-queued while attempts remain, else failed). There is no heartbeat: the lease already
covers the whole invocation.

The pool has ONE connection (``backend/shared/db.py``, connection budget C2.2): every step acquires and releases it in
turn (claim, handler, finish), never nested.

Errors: a :class:`PermanentError` fails the job at once with its own (fixed, payload-free) message; any other
exception is retried with backoff while attempts remain, and only its class name is stored (DM-6.3: an exception
message can echo data).
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from backend.jobs import queue
from backend.jobs.queue import Job

log = logging.getLogger(__name__)

Handler = Callable[[Any, Job], Awaitable[dict[str, Any]]]

LEASE_MARGIN_S = 60
LEASE_MAX_S = 900
# Stop claiming when less than this is left: a retention batch run needs room to finish and write its result.
MIN_TIME_LEFT_S = 45
MAX_JOBS_PER_RUN = 10


class PermanentError(Exception):
    """The job cannot succeed by retrying (bad payload, refused mode). Its message is stored: keep it fixed text."""


class JobFailed(Exception):
    """A run that stopped half-way after writing (a live retention batch failed): stored as failed, not retried, with
    ``result`` (what the committed part did) and ``error`` (fixed text, e.g. the exception class name)."""

    def __init__(self, error: str, result: dict[str, Any]):
        super().__init__(error)
        self.error = error
        self.result = result


def lease_seconds(time_left_s: float) -> int:
    return int(max(LEASE_MARGIN_S, min(time_left_s + LEASE_MARGIN_S, LEASE_MAX_S)))


async def run_due(
    pool: Any,
    handlers: Mapping[str, Handler],
    *,
    worker: str,
    time_left: Callable[[], float],
    max_jobs: int = MAX_JOBS_PER_RUN,
    emit: Callable[..., None] = lambda evt, **fields: None,
) -> dict[str, Any]:
    """Run due jobs of the registered kinds. Returns ``{"reaped": {...}, "jobs": [summary, ...]}``."""
    async with pool.acquire() as conn:
        reaped = await queue.reap(conn)
    if reaped["reaped"] or reaped["cancelled"]:
        emit("job_reap", **reaped)
    summaries: list[dict[str, Any]] = []
    kinds = sorted(handlers)
    while len(summaries) < max_jobs and time_left() >= MIN_TIME_LEFT_S:
        async with pool.acquire() as conn:
            job = await queue.claim(conn, worker, kinds, lease_seconds(time_left()))
        if job is None:
            break
        summaries.append(await _run_one(pool, handlers[job.kind], job, worker, emit))
    return {"reaped": reaped, "jobs": summaries}


async def _run_one(pool: Any, handler: Handler, job: Job, worker: str, emit: Callable[..., None]) -> dict[str, Any]:
    summary: dict[str, Any] = {"job_id": job.id, "kind": job.kind, "attempt": job.attempt}
    try:
        result = await handler(pool, job)
    except JobFailed as exc:
        async with pool.acquire() as conn:
            summary["status"] = await queue.fail(conn, job, worker, exc.error, retryable=False, result=exc.result)
        summary["error"] = exc.error
        summary["result"] = exc.result
    except PermanentError as exc:
        async with pool.acquire() as conn:
            summary["status"] = await queue.fail(conn, job, worker, f"PermanentError: {exc}", retryable=False)
        summary["error"] = str(exc)[: queue.ERROR_MAX_CHARS]
    except Exception as exc:  # noqa: BLE001 — every failure is recorded on the job; the class name only (DM-6.3)
        log.exception("job %s (%s) failed", job.id, job.kind)
        async with pool.acquire() as conn:
            summary["status"] = await queue.fail(conn, job, worker, type(exc).__name__, retryable=True)
        summary["error"] = type(exc).__name__
    else:
        async with pool.acquire() as conn:
            done = await queue.complete(conn, job.id, worker, result)
        summary["status"] = "succeeded" if done else "lost"
        summary["result"] = result
    emit("job_finished", job_id=job.id, kind=job.kind, status=summary["status"], attempt=job.attempt,
         mode=(summary.get("result") or {}).get("mode"), error=summary.get("error"))
    return summary
