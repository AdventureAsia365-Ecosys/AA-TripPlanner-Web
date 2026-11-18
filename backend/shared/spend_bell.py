"""AA-1126 L4 (S283, redesigned S284): the staff bell of a TripPlanner spend check that degraded under the prod spend
rules (``tripplanner.spend.environment`` = prod, CIS Platform › Settings).

Under prod a spend, AI budget or AI sessions read that fails lets the AI go on without that check (the dev rules fail
closed). Staff must see it, not only a log line. TripPlanner records one ``tripplanner.outbox`` event
``ai.spend_degraded`` per check and UTC day (tp-034; idempotency key ``ai.spend_degraded:<check>:<YYYY-MM-DD>``,
``ON CONFLICT DO NOTHING``, the row belongs to the tenant bound when it happened, tp-028's tenant policy), and CIS
pulls it through the contract view ``tripplanner.v_ai_notices_v1`` and raises its own admin bell (data standards
DM-5.4: TripPlanner never writes a CIS table). The payload names the check, the stage and the day; CIS writes the
words. A degrade with no tenant bound (the jobs Lambda's system scope) writes no event: its error line stays.

Best effort: the database may be the reason the check failed. Never raises; runs in a savepoint when ``db`` is a
connection; at most one attempt per check and UTC day per container (a failed write retries after
:data:`FAILURE_TTL_SECONDS`). The ``llm_gateway_error`` line ``<check>_degraded`` is printed on every degrade, as before.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from backend.shared import observability, tenant

EVENT = "ai.spend_degraded"
SQL = ("INSERT INTO tripplanner.outbox (tenant_id, event, schema_version, idempotency_key, aggregate_id, payload)"
       " VALUES ($1::uuid, $2, 1, $3, $1::uuid, $4::jsonb) ON CONFLICT (idempotency_key) DO NOTHING RETURNING id")
FAILURE_TTL_SECONDS = 60.0  # technical-constant: retry pace of a failed bell write (the settings reader's)

# The checks that degrade (CIS names each one in its admin bell, services/tripplanner_notices.py).
CHECKS = ("spend_unreadable", "tenant_budget_unreadable", "monthly_spend_unreadable", "tenant_sessions")

_attempted: dict[str, float] = {}   # "<check>:<YYYY-MM-DD>" -> monotonic time before which no new attempt
_clock: Callable[[], float] = time.monotonic


def clear() -> None:
    _attempted.clear()


async def degraded(db: Any, check: str, *, stage: Optional[str] = None, tenant_id: Optional[str] = None,
                   now: Optional[datetime] = None, **extra: Any) -> bool:
    """Print the ``<check>_degraded`` error line and record the day's staff bell event of ``check`` (module
    docstring). True when the event was written now."""
    observability.emit("llm_gateway_error", level="error", what=f"{check}_degraded", stage=stage, tenant_id=tenant_id,
                       **extra)
    day = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%d")
    key = f"{check}:{day}"
    owner = tenant_id or tenant.current_id()
    if db is None or owner is None or _clock() < _attempted.get(key, 0.0):
        return False
    payload = {"check": check, "stage": stage, "day": day}
    args = (owner, EVENT, f"{EVENT}:{key}"[:160], json.dumps(payload))
    try:
        transaction = getattr(db, "transaction", None)
        if transaction is None:
            written = await db.fetchval(SQL, *args)
        else:
            async with transaction():
                written = await db.fetchval(SQL, *args)
    except Exception as e:  # noqa: BLE001 — best effort, never on the call path
        _attempted[key] = _clock() + FAILURE_TTL_SECONDS
        observability.emit("spend_degraded_bell_failed", level="warning", check=check, error=type(e).__name__,
                           sqlstate=getattr(e, "sqlstate", None))
        return False
    _attempted[key] = float("inf")
    if written is None:                                     # the outbox has it already: raised today
        return False
    observability.emit("spend_degraded_bell", check=check, day=day)
    return True
