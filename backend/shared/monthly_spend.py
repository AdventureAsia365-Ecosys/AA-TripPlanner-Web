"""AA-1041 slice 2 — monthly spend caps of one AI stage (migration tp-029, ``tripplanner.ai_spend_month``).

The planning chat (stage ``tp_plan_chat``) has a cap per UTC month for the whole app and one per tenant (tech lead,
S277: 2 USD a month on Dev). The CIS contract views give TripPlanner today's spend per stage
(``catalog.v_llm_app_spend_today_v1``) and a tenant's whole month across apps (``catalog.v_tenant_ai_budget_v1``), but
no month per stage, and the ``tripplanner`` role may only INSERT into the ledger. So the chat keeps its own month sums
here: one row per (stage, scope, month), scope ``app`` (every tenant) or ``tenant:<uuid>``. The ledger
(``shared.llm_call_log``) stays the record of every call; this table only answers "is this month's cap reached".

Fail mode: CLOSED, like the daily cap (AA-642 S0). A read that fails (tp-029 not applied yet, a DB error) answers
``REASON_UNREADABLE``, so the chat pauses. A write that fails (the spend would undercount) holds the stage closed in
this container for :data:`WRITE_FAILED_HOLD_SECONDS` and prints one ERROR line.

Between the read and the write of one turn other turns can run, so a burst may pass the cap by the cost of the turns
in flight (a few cents); the next turn after it is refused. The caps are checked again before every model call of a
turn.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional

from backend.shared import observability

TABLE = "tripplanner.ai_spend_month"
SCOPE_APP = "app"
TENANT_PREFIX = "tenant:"

REASON_APP_CAP = "monthly_cap"
REASON_TENANT_CAP = "tenant_monthly_cap"
REASON_UNREADABLE = "monthly_spend_unreadable"
CAP_REASONS = (REASON_APP_CAP, REASON_TENANT_CAP)

WRITE_FAILED_HOLD_SECONDS = 60.0

READ_SQL = f"""
    SELECT scope, cost_usd, calls FROM {TABLE}
    WHERE stage = $1 AND month = date_trunc('month', now() AT TIME ZONE 'UTC')::date AND scope = ANY($2::text[])
"""
ADD_SQL = f"""
    INSERT INTO {TABLE} (stage, scope, month, cost_usd, calls, updated_at)
    SELECT $1, s, date_trunc('month', now() AT TIME ZONE 'UTC')::date, $3::numeric, $4::int, now()
    FROM unnest($2::text[]) AS s
    ON CONFLICT (stage, scope, month) DO UPDATE
       SET cost_usd = {TABLE}.cost_usd + EXCLUDED.cost_usd,
           calls = {TABLE}.calls + EXCLUDED.calls,
           updated_at = now()
"""

_hold_until: dict[str, float] = {}


@dataclass(frozen=True)
class MonthSpend:
    app_usd: float
    tenant_usd: float
    app_calls: int = 0
    tenant_calls: int = 0


def tenant_scope(tenant_id: Optional[str]) -> Optional[str]:
    return f"{TENANT_PREFIX}{tenant_id}" if tenant_id else None


def _scopes(tenant_id: Optional[str]) -> list[str]:
    t = tenant_scope(tenant_id)
    return [SCOPE_APP, t] if t else [SCOPE_APP]


def clear() -> None:
    _hold_until.clear()


async def read(db: Any, stage: str, tenant_id: Optional[str]) -> MonthSpend:
    """This month's sums for the app and the tenant (0 when no row yet). Raises on a read error."""
    rows = await db.fetch(READ_SQL, stage, _scopes(tenant_id))
    by_scope = {r["scope"]: r for r in rows}
    app, ten = by_scope.get(SCOPE_APP), by_scope.get(tenant_scope(tenant_id) or "")
    return MonthSpend(float(app["cost_usd"]) if app else 0.0, float(ten["cost_usd"]) if ten else 0.0,
                      int(app["calls"]) if app else 0, int(ten["calls"]) if ten else 0)


async def check(db: Any, stage: str, tenant_id: Optional[str], *, app_cap_usd: float,
                tenant_cap_usd: float) -> Optional[str]:
    """None when the stage may call a model this month, else the reason it may not (never raises)."""
    if time.monotonic() < _hold_until.get(stage, 0.0):
        return REASON_UNREADABLE
    try:
        spent = await read(db, stage, tenant_id)
    except Exception as e:  # noqa: BLE001 — fail closed, only the type and SQLSTATE are logged
        observability.emit("llm_gateway_error", level="error", what="monthly_spend_unreadable", stage=stage,
                           error=type(e).__name__, sqlstate=getattr(e, "sqlstate", None))
        return REASON_UNREADABLE
    if spent.app_usd >= app_cap_usd:
        observability.emit("llm_spend_alert", level="error", threshold="cap", scope="app_month", stage=stage,
                           spent_usd=round(spent.app_usd, 6), cap_usd=app_cap_usd)
        return REASON_APP_CAP
    if tenant_id and spent.tenant_usd >= tenant_cap_usd:
        observability.emit("llm_spend_alert", level="error", threshold="cap", scope="tenant_month", stage=stage,
                           tenant_id=tenant_id, spent_usd=round(spent.tenant_usd, 6), cap_usd=tenant_cap_usd)
        return REASON_TENANT_CAP
    return None


async def charge(db: Any, stage: str, tenant_id: Optional[str], usd: float, calls: int) -> None:
    """Add a turn's billed cost and call count to the app and tenant rows of this month. Never raises: a failed write
    holds the stage closed for WRITE_FAILED_HOLD_SECONDS in this container (the month would undercount)."""
    if calls <= 0 and usd <= 0:
        return
    try:
        await db.execute(ADD_SQL, stage, _scopes(tenant_id), round(float(usd), 6), int(calls))
    except Exception as e:  # noqa: BLE001
        _hold_until[stage] = time.monotonic() + WRITE_FAILED_HOLD_SECONDS
        observability.emit("llm_gateway_error", level="error", what="monthly_spend_write_failed", stage=stage,
                           error=type(e).__name__, sqlstate=getattr(e, "sqlstate", None))
