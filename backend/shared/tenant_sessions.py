"""AA-1116 (option B pricing, S282 decision 10) — the monthly AI sessions quota of an agency tenant (plan key
``tp.ai_sessions``: Hosted 300, Pro 1,500 a month in option B; NULL = no limit).

An **AI session** is a planner session (``tripplanner.sessions``) that makes at least one AI call in a UTC month,
counted once per month. The quota comes from CIS: ``catalog.v_tenant_tripplanner_v1.ai_sessions_monthly`` (CIS 438,
contract tenant.entitlements.v1 1.9, the tenant's resolved ``tp.ai_sessions``; edited on CIS Plans / Tenant 360, never a
literal here). The count is TripPlanner's own: ``tripplanner.ai_session_month`` (tp-032), one row per (tenant scope,
month, session).

:func:`admit` runs right before a stage's model call (narration, planning chat), after the money gates and the
per-visitor limits, and answers None (the call may run) or the reason it may not:

- the bare path / the AA-hosted tenant (``tenant_budget.exempt``): no quota, nothing counted;
- a session already counted this month: it continues, even past the quota (a traveller mid-trip keeps the AI);
- a new session: counted while the tenant's month count is under its quota (``ai_sessions_monthly`` NULL = no limit,
  counted anyway for the usage), else ``REASON_CAP`` (narration says ``ai_budget_exhausted``, the chat "paused");
- **fail closed for new sessions**: the quota unreadable (CIS 438 not applied, the view not granted, any read error),
  no row for the tenant, or the count unreadable / unwritable (tp-032 not applied): ``REASON_UNREADABLE``.

The count and the insert run under a per-tenant transaction advisory lock, so two new sessions at once never pass the
quota. The quota read is cached like the tenant budget (``tenant_budget.CACHE_TTL_SECONDS`` per tenant and container,
a failed read ``tenant_budget.FAILURE_TTL_SECONDS``); the count is read live. Search (browse Lambda) has no session
and is not counted.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from backend.shared import observability, tenant_budget

TABLE = "tripplanner.ai_session_month"
VIEW = "catalog.v_tenant_tripplanner_v1"
# Column names, never * (CIS 438 appends ai_sessions_monthly to the 429 columns).
QUOTA_SQL = f"SELECT tenant_id, ai_sessions_monthly FROM {VIEW} WHERE tenant_id = $1"
_MONTH = "date_trunc('month', now() AT TIME ZONE 'UTC')::date"
COUNTED_SQL = f"SELECT 1 AS counted FROM {TABLE} WHERE scope = $1 AND month = {_MONTH} AND session_id = $2::uuid"
LOCK_SQL = "SELECT pg_advisory_xact_lock(hashtextextended('tp_ai_session_month:' || $1, 0))"
COUNT_SQL = f"SELECT count(*) AS n FROM {TABLE} WHERE scope = $1 AND month = {_MONTH}"
ADD_SQL = (f"INSERT INTO {TABLE} (scope, month, session_id) VALUES ($1, {_MONTH}, $2::uuid)"
           f" ON CONFLICT (scope, month, session_id) DO NOTHING")

REASON_CAP = "tenant_sessions_cap"
REASON_UNREADABLE = "tenant_sessions_unreadable"
TENANT_PREFIX = "tenant:"


@dataclass(frozen=True)
class Quota:
    tenant_id: str
    readable: bool
    monthly: Optional[int] = None          # None = no limit (when readable)


_cache: dict[str, tuple[Quota, float]] = {}
_clock: Callable[[], float] = time.monotonic


def scope(tenant_id: str) -> str:
    return f"{TENANT_PREFIX}{tenant_id}"


def clear() -> None:
    _cache.clear()


def _unreadable(what: str, e: BaseException) -> None:
    observability.emit("llm_gateway_error", level="error", what=what, error=type(e).__name__,
                       sqlstate=getattr(e, "sqlstate", None))


async def quota(db: Any, tenant_id: str) -> Quota:
    """The tenant's monthly AI sessions quota (cached). Never raises: a failure or no row is an unreadable quota."""
    now = _clock()
    hit = _cache.get(tenant_id)
    if hit and hit[1] > now:
        return hit[0]
    ttl = tenant_budget.CACHE_TTL_SECONDS
    try:
        row = await db.fetchrow(QUOTA_SQL, tenant_id)
    except Exception as e:  # noqa: BLE001 — only the type and SQLSTATE are logged
        _unreadable("tenant_sessions_quota_unreadable", e)
        q, ttl = Quota(tenant_id, False), tenant_budget.FAILURE_TTL_SECONDS
    else:
        if row is None:
            q, ttl = Quota(tenant_id, False), tenant_budget.FAILURE_TTL_SECONDS
        else:
            monthly = row["ai_sessions_monthly"]
            q = Quota(tenant_id, True, int(monthly) if monthly is not None else None)
    if len(_cache) >= tenant_budget.CACHE_MAX:
        _cache.pop(next(iter(_cache)))
    _cache[tenant_id] = (q, now + ttl)
    return q


async def _counted(db: Any, s: str, session_id: str) -> bool:
    return bool(await db.fetchval(COUNTED_SQL, s, session_id))


async def admit(db: Any, tenant_id: Optional[str], session_id: Optional[str]) -> Optional[str]:
    """None when this session's AI call may run (counting the session on its first AI call of the month), else the
    stop reason (see the module docstring). Never raises."""
    if tenant_budget.exempt(tenant_id) or not session_id:
        return None
    tid, s = str(tenant_id), scope(str(tenant_id))
    try:
        if await _counted(db, s, session_id):
            return None
    except Exception as e:  # noqa: BLE001 — tp-032 missing or a read error: closed for a new session
        _unreadable("tenant_sessions_count_unreadable", e)
        return REASON_UNREADABLE
    q = await quota(db, tid)
    if not q.readable:
        observability.emit("tenant_ai_sessions_stop", reason=REASON_UNREADABLE, tenant_id=tid)
        return REASON_UNREADABLE
    try:
        async with db.transaction():
            await db.execute(LOCK_SQL, s)
            used = int(await db.fetchval(COUNT_SQL, s) or 0)
            if q.monthly is not None and used >= q.monthly and not await _counted(db, s, session_id):
                observability.emit("tenant_ai_sessions_stop", reason=REASON_CAP, tenant_id=tid, used=used,
                                   quota=q.monthly)
                return REASON_CAP
            await db.execute(ADD_SQL, s, session_id)
    except Exception as e:  # noqa: BLE001 — the count could not be kept: closed for a new session
        _unreadable("tenant_sessions_write_failed", e)
        return REASON_UNREADABLE
    return None
