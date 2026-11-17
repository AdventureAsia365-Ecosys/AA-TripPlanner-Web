"""AA-1126 L4 (S283): the staff bell of a TripPlanner spend check that degraded under the prod spend rules
(``tripplanner.spend.environment`` = prod, CIS Platform › Settings).

Under prod a spend, AI budget or AI sessions read that fails lets the AI go on without that check (the dev rules fail
closed). Staff must see it, not only a log line: one ``shared.notifications`` row per check and UTC day, event
``platform.tripplanner.spend_degraded`` (``platform.*``: the CIS admin bell only, never a content role's), entity type
``tripplanner_ai``, target_roles ``{admin}``, on the AA internal tenant. CIS 447 lets the tripplanner role INSERT
exactly that row (and the agency allowance notice, ``tenant_sessions.notify_agency``) and nothing else; its partial
unique index ``notifications_tp_spend_degraded_once_uq`` refuses a second row of the same check and day. TripPlanner
may not read the table, so the write is a plain INSERT and the unique violation (SQLSTATE 23505) means "already raised
today".

Best effort: the database may be the reason the check failed. Never raises; runs in a savepoint when ``db`` is a
connection; at most one attempt per check and UTC day per container (a failed write retries after
:data:`FAILURE_TTL_SECONDS`). The ``llm_gateway_error`` line ``<check>_degraded`` is printed on every degrade, as before.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from backend.shared import observability

EVENT = "platform.tripplanner.spend_degraded"
ENTITY_TYPE = "tripplanner_ai"
PLATFORM_TENANT_ID = "00000000-0000-0000-0000-000000000001"  # the AA internal tenant of the CIS admin bell rows
SQL = ("INSERT INTO shared.notifications (tenant_id, actor_type, event_type, entity_type, entity_id, payload,"
       " target_roles) VALUES ($1::uuid, 'system', $2, $3, $4, $5::jsonb, ARRAY['admin'])")
FAILURE_TTL_SECONDS = 60.0  # technical-constant: retry pace of a failed bell write (the settings reader's)

# The checks that degrade, and what the staff bell calls each.
CHECKS = {
    "spend_unreadable": "today's AI spend",
    "tenant_budget_unreadable": "an agency's AI budget",
    "monthly_spend_unreadable": "the planning chat's monthly AI spend",
    "tenant_sessions": "an agency's AI sessions quota",
}

_attempted: dict[str, float] = {}   # "<check>:<YYYY-MM-DD>" -> monotonic time before which no new attempt
_clock: Callable[[], float] = time.monotonic


def clear() -> None:
    _attempted.clear()


def message(check: str) -> str:
    what = CHECKS.get(check, check)
    return (f"TripPlanner could not read {what}. Under the prod spend rules (the TripPlanner spend environment "
            "setting) the AI went on without this check. Check the database and the TripPlanner logs "
            "(llm_gateway_error).")


async def degraded(db: Any, check: str, *, stage: Optional[str] = None, tenant_id: Optional[str] = None,
                   now: Optional[datetime] = None, **extra: Any) -> bool:
    """Print the ``<check>_degraded`` error line and raise the day's staff bell of ``check`` (module docstring). True
    when the bell was written now."""
    observability.emit("llm_gateway_error", level="error", what=f"{check}_degraded", stage=stage, tenant_id=tenant_id,
                       **extra)
    day = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%d")
    key = f"{check}:{day}"
    if db is None or _clock() < _attempted.get(key, 0.0):
        return False
    payload = {"message": message(check), "check": check, "stage": stage, "tenant_id": tenant_id, "day": day}
    try:
        transaction = getattr(db, "transaction", None)
        if transaction is None:
            await db.execute(SQL, PLATFORM_TENANT_ID, EVENT, ENTITY_TYPE, key[:100], json.dumps(payload))
        else:
            async with transaction():
                await db.execute(SQL, PLATFORM_TENANT_ID, EVENT, ENTITY_TYPE, key[:100], json.dumps(payload))
    except Exception as e:  # noqa: BLE001 — best effort, never on the call path
        if getattr(e, "sqlstate", None) == "23505":          # the unique index: raised already today
            _attempted[key] = float("inf")
            return False
        _attempted[key] = _clock() + FAILURE_TTL_SECONDS
        observability.emit("spend_degraded_bell_failed", level="warning", check=check, error=type(e).__name__,
                           sqlstate=getattr(e, "sqlstate", None))
        return False
    _attempted[key] = float("inf")
    observability.emit("spend_degraded_bell", check=check, day=day)
    return True
