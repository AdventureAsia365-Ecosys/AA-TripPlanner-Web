"""The daily spend caps' helpers of the model gateway (moved from ``backend/shared/llm_gateway.py``, AA-1126) and the
AI spend rules per environment (AA-1126 G3 / L4, setting ``tripplanner.spend.environment``, CIS 447).

- dev (the default, today's behaviour): a reached app-wide cap (the app profile's daily budget, the online stages'
  daily cap, a stage cap) stops the AI of every agency; a spend or an agency AI budget that cannot be read fails closed.
- prod (tech lead policy S283, pay per use): a reached app-wide cap only alerts (``llm_spend_alert`` threshold
  ``cap_alert_only``, the answer carries ``alert_only``); a read error degrades with an ``llm_gateway_error`` line
  (``*_degraded``) and the call goes on. An agency's own AI budget (``tenant_budget``) stays hard.
"""
from __future__ import annotations

import time
from typing import Optional

from backend import config
from backend.aa_llm import budget as core_budget
from backend.shared import observability, settings, tenant, tenant_budget


# AA-1015: the stage caps (config, unset = none) and the once-per-day alert lines.
def _stage_cap(stage: Optional[str]) -> Optional[float]:
    caps = {"tp_compose": config.TP_DAILY_SPEND_CAP_COMPOSE_USD,
            "tp_search_embed": config.TP_DAILY_SPEND_CAP_SEARCH_USD}
    cap = caps.get(stage) if stage else None
    return float(cap) if cap is not None else None


# (UTC day, scope, threshold) already announced by this container, so a busy day prints each alert once.
_alerted: set[tuple[str, str, str]] = set()


def _alert(scope: str, spent: float, cap: float, cap_source: str = "env") -> None:
    """One `llm_spend_alert` line per container, UTC day, scope and threshold: 'near' at
    SPEND_NEAR_CAP_RATIO of the cap (level warning), 'cap' when reached (level error). `cap_source`: 'app_profile'
    (scope 'app': every stage against the profile's budget; the CIS ops check alerts on the same numbers, bell +
    e-mail) or 'env' (scope 'total': the online stages against TP_DAILY_SPEND_CAP_USD; a stage scope)."""
    threshold = core_budget.threshold(spent, cap, config.SPEND_NEAR_CAP_RATIO)
    if threshold is None:
        return
    level = "error" if threshold == core_budget.THRESHOLD_CAP else "warning"
    key = (time.strftime("%Y-%m-%d", time.gmtime()), scope, threshold)
    if key in _alerted:
        return
    _alerted.add(key)
    observability.emit("llm_spend_alert", level=level, threshold=threshold, scope=scope,
                       spent_usd=round(spent, 6), cap_usd=cap, ratio=config.SPEND_NEAR_CAP_RATIO,
                       cap_source=cap_source)


def unreadable(closed: dict, stage: Optional[str]) -> dict:
    """The answer when today's spend cannot be read: ``closed`` (the fail-closed answer) under dev; under prod an open
    answer marked ``degraded`` after one error line."""
    if not settings.spend_prod():
        return closed
    observability.emit("llm_gateway_error", level="error", what="spend_unreadable_degraded", stage=stage)
    return {"spent_usd": None, "cap_usd": closed.get("cap_usd"), "exhausted": False, "reason": None,
            "near_cap": False, "degraded": core_budget.REASON_SPEND_UNREADABLE}


def over_cap(out: dict, *, scope: str, stage: Optional[str], spent: float) -> dict:
    """A decision over an app-wide cap: unchanged under dev; under prod open again (``alert_only``) after one alert."""
    if not out.get("exhausted") or not settings.spend_prod():
        return out
    observability.emit("llm_spend_alert", level="error", threshold="cap_alert_only", scope=scope, stage=stage,
                       spent_usd=round(float(spent), 6), cap_usd=out.get("cap_usd"))
    return {**out, "exhausted": False, "reason": None, "alert_only": True}


def tenant_reason(reason: Optional[str], stage: Optional[str]) -> Optional[str]:
    """The agency AI budget's stop reason: an unreadable budget is no stop under prod (one error line)."""
    if reason != tenant_budget.REASON_UNREADABLE or not settings.spend_prod():
        return reason
    observability.emit("llm_gateway_error", level="error", what="tenant_budget_unreadable_degraded", stage=stage,
                       tenant_id=tenant.current_id())
    return None
