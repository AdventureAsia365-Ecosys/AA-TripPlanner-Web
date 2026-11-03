"""AA-1025 H1b (root ADR 0009 decision 4): TripPlanner's app profile, read from the CIS contract view
``catalog.v_llm_app_profile_v1`` (CIS migration 347: the ``tripplanner`` row of ``shared.llm_app_profile``, CIS 345).

The CIS admin edits the profile (AI providers › Providers › App profiles, ``PATCH /admin/ai-gateway/app-profiles/
tripplanner``, audited). TripPlanner acts on two fields:

- ``ai_enabled`` false: every TripPlanner model call is refused before any provider attempt
  (``llm_gateway.load_route``: one 0 USD ledger row, status ``refused``, error_class ``disabled``, then
  ``AiPaused("ai_off")``), so narration answers the basic / master_basic text with ``ai_paused`` and search the
  keyword ranking (the app's degrade mode ``basic_narration``, tech lead T-21).
- ``default_daily_budget_usd``: when set, TripPlanner's daily cap over **every** ``tp_*`` stage, online and offline
  (``llm_gateway.budget_status``); it replaces ``TP_DAILY_SPEND_CAP_USD`` (the cap of the customer-facing stages,
  kept as the fallback when the profile has no budget). The CIS ops check ``llm_spend_alert`` reads the same row and the
  same ledger, so TripPlanner's 80 % / 100 % lines also reach the CIS admin bell and the ops e-mail.

``bulk_paused`` and ``degrade_mode`` are read for the log and the tests; TripPlanner has no bulk work and its degrade
mode is fixed in code (basic narration, keyword search).

Reads: one row, cached ``CACHE_TTL_SECONDS`` per container (the CIS gateway's 20 s), so a switch reaches every Lambda
container within that window. **Fail open**, the CIS rule (``shared/llm_client/app_profile.py``): no row, the view
missing or not granted yet (SQLSTATE 42P01 / 42501, CIS 347 not applied: retried after ``VIEW_RETRY_SECONDS``) or any
other read error (retried after ``FAILURE_TTL_SECONDS``, one ``llm_gateway_error`` line) = AI on, no profile budget.
The spend cap itself still fails closed (``budget_status``). ``TP_APP_PROFILE=off`` skips the read (no deploy of code).
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Optional

from backend.shared import observability

APP = "tripplanner"
VIEW = "catalog.v_llm_app_profile_v1"
COLUMNS = ("app", "ai_enabled", "bulk_paused", "default_daily_budget_usd", "degrade_mode", "updated_at")
# Column names, never *, so a column CIS appends changes nothing (contract v1).
PROFILE_SQL = f"SELECT {', '.join(COLUMNS)} FROM {VIEW} WHERE app = '{APP}'"  # noqa: S608 — fixed identifiers
SWITCH_ENV = "TP_APP_PROFILE"
CACHE_TTL_SECONDS = 20.0
FAILURE_TTL_SECONDS = 60.0
VIEW_RETRY_SECONDS = 300.0
DEFAULT_DEGRADE_MODE = "basic_narration"
_MISSING_VIEW_STATES = ("42P01", "42501")


@dataclass(frozen=True)
class AppProfile:
    """The profile TripPlanner acts on. ``source``: ``view`` (read from the row), ``default`` (no row, no view,
    switched off) or ``unreadable`` (a read error: the defaults, fail open)."""

    ai_enabled: bool = True
    bulk_paused: bool = False
    default_daily_budget_usd: Optional[float] = None
    degrade_mode: str = DEFAULT_DEGRADE_MODE
    source: str = "default"


DEFAULT = AppProfile()


def enabled() -> bool:
    return (os.environ.get(SWITCH_ENV) or "on").strip().lower() not in ("off", "false", "0", "no")


def profile_of(row: Any) -> AppProfile:
    """The profile of one view row (a mapping), the default for None. Pure."""
    if row is None:
        return DEFAULT
    budget = row["default_daily_budget_usd"]
    try:
        budget = None if budget is None else round(float(budget), 4)
    except (TypeError, ValueError):
        budget = None
    if budget is not None and budget < 0:
        budget = None
    mode = row["degrade_mode"]
    return AppProfile(ai_enabled=row["ai_enabled"] is not False, bulk_paused=row["bulk_paused"] is True,
                      default_daily_budget_usd=budget,
                      degrade_mode=mode if isinstance(mode, str) and mode else DEFAULT_DEGRADE_MODE, source="view")


async def read(db: Any) -> AppProfile:
    """One read of the view (no cache). Raises on any database error. ``db``: an asyncpg pool or connection."""
    return profile_of(await db.fetchrow(PROFILE_SQL))


_cached: Optional[tuple[AppProfile, float]] = None
_retry_after = 0.0


def invalidate() -> None:
    global _cached, _retry_after
    _cached, _retry_after = None, 0.0


async def current(db: Any) -> AppProfile:
    """The cached profile (module docstring). Never raises."""
    global _cached, _retry_after
    if not enabled():
        return DEFAULT
    now = time.monotonic()
    if _cached is not None and now - _cached[1] < CACHE_TTL_SECONDS:
        return _cached[0]
    if now < _retry_after:
        return DEFAULT
    try:
        profile = await read(db)
    except Exception as e:  # noqa: BLE001 — fail open (module docstring)
        missing = getattr(e, "sqlstate", None) in _MISSING_VIEW_STATES
        _cached = None
        _retry_after = now + (VIEW_RETRY_SECONDS if missing else FAILURE_TTL_SECONDS)
        if missing:
            observability.emit("llm_gateway_note", view=VIEW, error=type(e).__name__,
                               detail=f"{VIEW} is not readable yet (CIS migration 347): AI on, cap from the env")
        else:
            observability.emit("llm_gateway_error", level="error", what="app_profile_unreadable",
                                error=type(e).__name__, detail=str(e)[:200])
        return AppProfile(source="unreadable") if not missing else DEFAULT
    _cached = (profile, now)
    return profile
