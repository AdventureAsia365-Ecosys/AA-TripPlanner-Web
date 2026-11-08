"""AA-1038 R5 slice 3 (root ADR 0019 d2 / b19): the per-tenant AI cap of TripPlanner's stages.

An agency tenant's AI spend is one pot across every app: its resolved ``ai.budget_usd_monthly`` (plan, override or the
AA-1016 row) against its ``shared.llm_call_log`` rows of the current UTC month. CIS serves both to TripPlanner in the
contract view ``catalog.v_tenant_ai_budget_v1`` (CIS 390, contract tenant.entitlements.v1 1.5); TripPlanner's own rows
carry the tenant since this slice (``llm_gateway.record`` takes it from ``tenant.current()``), so a tenant's planner
use counts in the same pot and shows on CIS Tenant 360 › Cost.

``check`` answers, for the tenant of the running request, None (AI may run) or the reason it may not:

- the bare path / the AA demo tenant (``config.DEFAULT_TENANT_ID`` and the demo id) is AA's own site: no tenant
  budget, the app caps of ``llm_gateway.budget_status`` still apply (unchanged behaviour);
- ``budget_source`` 'entitlement' with ``monthly_usd`` NULL: unlimited (an enterprise or internal contract);
- spend at or past ``monthly_usd``, a 0 USD budget, or 'disabled' (an active disabled override): ``REASON_CAP``
  (narration says ``ai_budget_exhausted``, search falls back to keyword ranking);
- **fail closed** (lesson S266: "no row = no limit" is the dangerous default): 'none' (no budget key resolves), no row
  (the tenant has no TripPlanner subscription), the view missing or not granted (CIS 390 not applied: 42P01 / 42501)
  or any read error: ``REASON_NONE`` / ``REASON_UNREADABLE``, AI paused for that tenant only.

TripPlanner stops at 100 % of the budget: a traveller-facing stage degrades, it never waits for staff approval (the
AA-1016 alert / approval thresholds stay CIS's). Reads are cached ``CACHE_TTL_SECONDS`` per tenant and container; a
call this container makes adds its cost to the cached spend (``charge``), so a burst cannot run far past the cap
between two reads. A failed read is cached ``FAILURE_TTL_SECONDS`` (the answer stays closed meanwhile).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from backend import config
from backend.shared import observability

VIEW = "catalog.v_tenant_ai_budget_v1"
# Column names, never * (the CIS test test_aa1038_migration_390.py runs this exact statement as tripplanner).
BUDGET_SQL = f"SELECT tenant_id, budget_source, monthly_usd, month_spent_usd, month_calls FROM {VIEW} WHERE tenant_id = $1"
CACHE_TTL_SECONDS = 30.0
FAILURE_TTL_SECONDS = 15.0
CACHE_MAX = 1000

REASON_CAP = "tenant_budget_cap"
REASON_NONE = "tenant_budget_none"
REASON_UNREADABLE = "tenant_budget_unreadable"
STOP_REASONS = (REASON_CAP, REASON_NONE, REASON_UNREADABLE)
# The reasons that are a pause (the budget could not be decided), not a reached cap.
PAUSE_REASONS = (REASON_NONE, REASON_UNREADABLE)


@dataclass(frozen=True)
class TenantBudget:
    tenant_id: str
    source: str                       # entitlement | disabled | none | missing | unreadable
    monthly_usd: Optional[float]
    month_spent_usd: float = 0.0
    month_calls: int = 0

    def stop_reason(self) -> Optional[str]:
        if self.source == "unreadable":
            return REASON_UNREADABLE
        if self.source in ("none", "missing"):
            return REASON_NONE
        if self.source == "disabled":
            return REASON_CAP
        if self.monthly_usd is None:
            return None                              # unlimited by contract
        if self.monthly_usd <= 0 or self.month_spent_usd >= self.monthly_usd:
            return REASON_CAP
        return None


_cache: dict[str, tuple[TenantBudget, float]] = {}
_clock: Callable[[], float] = time.monotonic


def exempt(tenant_id: Optional[str]) -> bool:
    """AA's own planner (the bare path, the demo tenant): no tenant budget applies."""
    return tenant_id is None or tenant_id in (config.DEFAULT_TENANT_ID, config.DEMO_TENANT_ID)


def clear() -> None:
    _cache.clear()


async def read(db: Any, tenant_id: str) -> TenantBudget:
    """The tenant's budget row (cached). Never raises: a failure is a 'unreadable' budget (closed)."""
    now = _clock()
    hit = _cache.get(tenant_id)
    if hit and hit[1] > now:
        return hit[0]
    ttl = CACHE_TTL_SECONDS
    try:
        row = await db.fetchrow(BUDGET_SQL, tenant_id)
    except Exception as e:  # noqa: BLE001 — only the type and SQLSTATE are logged, never str(e)
        observability.emit("llm_gateway_error", level="error", what="tenant_budget_unreadable",
                           error=type(e).__name__, sqlstate=getattr(e, "sqlstate", None))
        budget, ttl = TenantBudget(tenant_id, "unreadable", None), FAILURE_TTL_SECONDS
    else:
        if row is None:
            budget = TenantBudget(tenant_id, "missing", None)
        else:
            monthly = row["monthly_usd"]
            budget = TenantBudget(tenant_id, str(row["budget_source"]),
                                  float(monthly) if monthly is not None else None,
                                  float(row["month_spent_usd"] or 0), int(row["month_calls"] or 0))
    if len(_cache) >= CACHE_MAX:
        _cache.pop(next(iter(_cache)))
    _cache[tenant_id] = (budget, now + ttl)
    return budget


async def check(db: Any, tenant_id: Optional[str]) -> Optional[str]:
    """None when the tenant's AI may run, else the stop reason (see the module docstring)."""
    if exempt(tenant_id):
        return None
    budget = await read(db, str(tenant_id))
    reason = budget.stop_reason()
    if reason is not None:
        observability.emit("tenant_ai_budget_stop", reason=reason, tenant_id=budget.tenant_id,
                           source=budget.source, monthly_usd=budget.monthly_usd,
                           spent_usd=round(budget.month_spent_usd, 6))
    return reason


def charge(tenant_id: Optional[str], usd: Optional[float]) -> None:
    """Add a call's cost to the cached spend of the tenant (until the next read refreshes it from the ledger)."""
    if exempt(tenant_id) or not usd:
        return
    hit = _cache.get(str(tenant_id))
    if hit is None:
        return
    b, until = hit
    _cache[str(tenant_id)] = (TenantBudget(b.tenant_id, b.source, b.monthly_usd, b.month_spent_usd + float(usd),
                                           b.month_calls + 1), until)
