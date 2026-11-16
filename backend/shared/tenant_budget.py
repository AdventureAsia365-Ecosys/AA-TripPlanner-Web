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

AA-1116 (option B, CIS 438, contract tenant.entitlements.v1 1.9): the stop and alert shares come from CIS, not a
literal. The view appends ``alert_pct`` / ``approval_pct`` / ``hard_stop`` (the tenant's AI budget row, else the CIS
settings ``cis.billing.ai_alert_pct_default`` / ``ai_approval_pct_default`` / ``ai_hard_stop_default``, 80 / 100 / on,
edited on CIS Platform › Settings). TripPlanner applies the CIS rule (``shared/cost_guard.py`` tenant budget): with the
hard stop on (or a 0 USD budget), AI stops at ``approval_pct`` of the budget; a traveller-facing stage degrades there,
it never waits for staff approval. With the hard stop off, the budget only alerts (as CIS's own jobs); the daily app
caps of ``llm_gateway.budget_status`` still apply. ``alert_pct`` prints one ``tenant_ai_budget_alert`` line per
container, tenant and UTC month. A stop share that is not readable (NULL) fails closed (``REASON_UNREADABLE``).

Reads are cached ``CACHE_TTL_SECONDS`` per tenant and container; a
call this container makes adds its cost to the cached spend (``charge``), so a burst cannot run far past the cap
between two reads. A failed read is cached ``FAILURE_TTL_SECONDS`` (the answer stays closed meanwhile).
"""
from __future__ import annotations

import dataclasses
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from backend import config
from backend.shared import observability

VIEW = "catalog.v_tenant_ai_budget_v1"
# Column names, never * (the CIS test test_aa1038_migration_390.py runs this exact statement as tripplanner).
BUDGET_SQL = (f"SELECT tenant_id, budget_source, monthly_usd, month_spent_usd, month_calls, alert_pct, approval_pct,"
              f" hard_stop FROM {VIEW} WHERE tenant_id = $1")
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
    # AA-1116 (CIS 438): the shares of the budget where the bell rings and where AI stops, and the hard stop switch.
    alert_pct: Optional[int] = None
    approval_pct: Optional[int] = None
    hard_stop: Optional[bool] = None

    def used_pct(self) -> Optional[float]:
        if self.monthly_usd is None or self.monthly_usd <= 0:
            return None
        return self.month_spent_usd * 100.0 / self.monthly_usd

    def stop_reason(self) -> Optional[str]:
        if self.source == "unreadable":
            return REASON_UNREADABLE
        if self.source in ("none", "missing"):
            return REASON_NONE
        if self.source == "disabled":
            return REASON_CAP
        if self.monthly_usd is None:
            return None                              # unlimited by contract
        if self.monthly_usd <= 0:
            return REASON_CAP                        # "0 = no AI", whatever the hard stop says (CIS S266 rule)
        if self.hard_stop is False:
            return None                              # alert only (CIS: no hard stop); the app caps still apply
        if self.approval_pct is None:
            return REASON_UNREADABLE                 # the stop share cannot be read: closed
        used = self.used_pct()
        return REASON_CAP if used is not None and used >= self.approval_pct else None

    def alerting(self) -> bool:
        used = self.used_pct()
        return used is not None and self.alert_pct is not None and used >= self.alert_pct


def _int(v: Any) -> Optional[int]:
    return None if v is None else int(v)


_cache: dict[str, tuple[TenantBudget, float]] = {}
# (UTC month, tenant) whose alert line this container printed already.
_alerted: set[tuple[str, str]] = set()
_clock: Callable[[], float] = time.monotonic


def exempt(tenant_id: Optional[str]) -> bool:
    """AA's own planner (the bare path, the AA-hosted tenant: config.is_aa_hosted): no tenant budget applies."""
    return tenant_id is None or config.is_aa_hosted(tenant_id)


def clear() -> None:
    _cache.clear()
    _alerted.clear()


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
                                  float(row["month_spent_usd"] or 0), int(row["month_calls"] or 0),
                                  _int(row["alert_pct"]), _int(row["approval_pct"]),
                                  None if row["hard_stop"] is None else bool(row["hard_stop"]))
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
                           spent_usd=round(budget.month_spent_usd, 6), approval_pct=budget.approval_pct,
                           hard_stop=budget.hard_stop)
    elif budget.alerting():
        key = (time.strftime("%Y-%m", time.gmtime()), budget.tenant_id)
        if key not in _alerted:
            _alerted.add(key)
            observability.emit("tenant_ai_budget_alert", level="warning", tenant_id=budget.tenant_id,
                               monthly_usd=budget.monthly_usd, spent_usd=round(budget.month_spent_usd, 6),
                               alert_pct=budget.alert_pct, approval_pct=budget.approval_pct,
                               hard_stop=budget.hard_stop)
    return reason


def charge(tenant_id: Optional[str], usd: Optional[float]) -> None:
    """Add a call's cost to the cached spend of the tenant (until the next read refreshes it from the ledger)."""
    if exempt(tenant_id) or not usd:
        return
    hit = _cache.get(str(tenant_id))
    if hit is None:
        return
    b, until = hit
    _cache[str(tenant_id)] = (dataclasses.replace(b, month_spent_usd=b.month_spent_usd + float(usd),
                                                  month_calls=b.month_calls + 1), until)
