"""The daily spend caps' helpers of the model gateway (moved from ``backend/shared/llm_gateway.py``, AA-1126) and the
AI spend rules per environment (AA-1126 G3 / L4, setting ``tripplanner.spend.environment``, CIS 447).

- dev (the default, today's behaviour): a reached app-wide cap (the app profile's daily budget, the online stages'
  daily cap, a stage cap) stops the AI of every agency; a spend or an agency AI budget that cannot be read fails closed.
- prod (tech lead policy S283, pay per use): a reached app-wide cap only alerts (``llm_spend_alert`` threshold
  ``cap_alert_only``, the answer carries ``alert_only``); a read error degrades with an ``llm_gateway_error`` line
  (``*_degraded``), one staff bell per check and UTC day (``spend_bell``, CIS 447), and the call goes on. An agency's
  own AI budget (``tenant_budget``) stays hard.

The stage caps and the near-cap share are settings too (CIS 447: ``tripplanner.spend.stage_cap_compose_usd`` /
``_search_usd``, 0 = no stage cap; ``tripplanner.spend.near_cap_pct``), so the CIS Apps board shows what is enforced.
"""
from __future__ import annotations

import time
from typing import Any, Optional

from backend.aa_llm import budget as core_budget
from backend.shared import observability, settings, spend_bell, tenant, tenant_budget


# AA-1015: the stage caps (settings since AA-1126, 0 = none) and the once-per-day alert lines.
STAGE_CAP_KEYS = {"tp_compose": settings.STAGE_CAP_COMPOSE_USD, "tp_search_embed": settings.STAGE_CAP_SEARCH_USD}


def _stage_cap(stage: Optional[str]) -> Optional[float]:
    key = STAGE_CAP_KEYS.get(stage) if stage else None
    if key is None:
        return None
    cap = settings.get(key)
    return float(cap) if cap is not None and float(cap) > 0 else None


def near_ratio() -> float:
    """The share of a cap at which the "near cap" alert fires (``tripplanner.spend.near_cap_pct`` / 100)."""
    return float(settings.get(settings.NEAR_CAP_PCT)) / 100.0


# (UTC day, scope, threshold) already announced by this container, so a busy day prints each alert once.
_alerted: set[tuple[str, str, str]] = set()


def _alert(scope: str, spent: float, cap: float, cap_source: str = "env") -> None:
    """One `llm_spend_alert` line per container, UTC day, scope and threshold: 'near' at
    near_ratio() of the cap (level warning), 'cap' when reached (level error). `cap_source`: 'app_profile'
    (scope 'app': every stage against the profile's budget; the CIS ops check alerts on the same numbers, bell +
    e-mail) or 'env' (scope 'total': the online stages against TP_DAILY_SPEND_CAP_USD; a stage scope)."""
    ratio = near_ratio()
    threshold = core_budget.threshold(spent, cap, ratio)
    if threshold is None:
        return
    level = "error" if threshold == core_budget.THRESHOLD_CAP else "warning"
    key = (time.strftime("%Y-%m-%d", time.gmtime()), scope, threshold)
    if key in _alerted:
        return
    _alerted.add(key)
    observability.emit("llm_spend_alert", level=level, threshold=threshold, scope=scope,
                       spent_usd=round(spent, 6), cap_usd=cap, ratio=ratio,
                       cap_source=cap_source)


async def unreadable(db: Any, closed: dict, stage: Optional[str]) -> dict:
    """The answer when today's spend cannot be read: ``closed`` (the fail-closed answer) under dev; under prod an open
    answer marked ``degraded`` after one error line and the day's staff bell."""
    if not settings.spend_prod():
        return closed
    await spend_bell.degraded(db, "spend_unreadable", stage=stage)
    return {"spent_usd": None, "cap_usd": closed.get("cap_usd"), "exhausted": False, "reason": None,
            "near_cap": False, "degraded": core_budget.REASON_SPEND_UNREADABLE}


def over_cap(out: dict, *, scope: str, stage: Optional[str], spent: float) -> dict:
    """A decision over an app-wide cap: unchanged under dev; under prod open again (``alert_only``) after one alert."""
    if not out.get("exhausted") or not settings.spend_prod():
        return out
    observability.emit("llm_spend_alert", level="error", threshold="cap_alert_only", scope=scope, stage=stage,
                       spent_usd=round(float(spent), 6), cap_usd=out.get("cap_usd"))
    return {**out, "exhausted": False, "reason": None, "alert_only": True}


async def tenant_reason(db: Any, reason: Optional[str], stage: Optional[str]) -> Optional[str]:
    """The agency AI budget's stop reason: an unreadable budget is no stop under prod (one error line, the day's staff
    bell)."""
    if reason != tenant_budget.REASON_UNREADABLE or not settings.spend_prod():
        return reason
    await spend_bell.degraded(db, "tenant_budget_unreadable", stage=stage, tenant_id=tenant.current_id())
    return None
