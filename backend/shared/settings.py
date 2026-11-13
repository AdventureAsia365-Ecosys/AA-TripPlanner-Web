"""S279 thresholds lane L5 (AA-1096): TripPlanner's admin-editable settings, read from the CIS contract view
``catalog.v_app_settings_v1`` (CIS migration 415; rows of ``shared.setting_definition`` / ``shared.setting_value`` with
app ``tripplanner``, CIS 407 / 414). The administrator edits them on the CIS admin page Platform › Settings
(TripPlanner tab; maker-checker, audited); the TripPlanner daily AI cap is not here: its one source is the app profile
(``backend/shared/app_profile.py``, AI gateway › App profiles).

    from backend.shared import settings
    await settings.refresh(db)                                   # once per request / job run (cached)
    settings.get(settings.RATE_NARRATE_PER_HOUR)                 # sync: the last snapshot, never the database
    settings.get(settings.LEAD_TENANT_DAILY_CAP, tenant_id)      # a tenant override when the view has one

Resolution of ``get(key, tenant_id=None)``:
  1. ``TP_SETTINGS_ENV_FIRST=on`` (an emergency switch for one release, off by default) and the key's env var is set:
     the env value (the module attribute of ``backend/config.py`` that parses it), logged once as a warning;
  2. the view's row of the tenant (``tenant_id`` given; only the keys with a per-tenant scope have such rows);
  3. the view's platform row (the approved value in force, else the CIS default as version 0);
  4. the fallback: today's value before the registry, i.e. the env var when set, else the code default (the same
     config attribute; ``backend/tests/test_s279_settings.py`` pins it equal to the CIS seed).
A value from the view that breaks its definition here (type, bounds) is refused (one ``settings_value_refused`` error
line) and the fallback is served.

Cache: one snapshot of the view per container, re-read when older than ``CACHE_TTL_SECONDS`` (25 s, inside the 20-30 s
window of the lane). Feature-detected: the view missing or not granted yet (SQLSTATE 42P01 / 42501, CIS 415 not
applied) keeps the fallbacks and retries after ``VIEW_RETRY_SECONDS``; any other read error keeps the last good
snapshot (or the fallbacks) and retries after ``FAILURE_TTL_SECONDS`` (one ``settings_unreadable`` error line). A read
never raises. The read runs in a savepoint when ``db`` is a connection, so a failed read never aborts the caller's
transaction.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from backend import config
from backend.shared import observability

APP = "tripplanner"
VIEW = "catalog.v_app_settings_v1"
# Column names, never *, so a column CIS appends changes nothing (contract v1).
SETTINGS_SQL = (f"SELECT key, value::text AS value_text, version, tenant_id::text AS tenant_id "  # noqa: S608
                f"FROM {VIEW} WHERE app = '{APP}'")
CACHE_TTL_SECONDS = 25.0  # technical-constant: reader cache inside the lane's 20-30 s window (CIS cache_s is 30)
FAILURE_TTL_SECONDS = 60.0  # technical-constant: retry pace after a read error (the app profile's)
VIEW_RETRY_SECONDS = 300.0  # technical-constant: retry pace while the view is not migrated (the app profile's)
ENV_FIRST_SWITCH = "TP_SETTINGS_ENV_FIRST"
_MISSING_VIEW_STATES = ("42P01", "42501")

RATE_NARRATE_PER_HOUR = "tripplanner.rate.narrate_per_hour"
RATE_SEARCH_PER_MINUTE = "tripplanner.rate.search_per_minute"
RATE_SIGNED_IN_FACTOR = "tripplanner.rate.signed_in_factor"
RATE_SEND_PER_HOUR = "tripplanner.rate.send_per_hour"
RATE_SEND_IP_PER_HOUR = "tripplanner.rate.send_ip_per_hour"
RATE_CHAT_PER_HOUR = "tripplanner.rate.chat_per_hour"
RATE_CHAT_IP_PER_HOUR = "tripplanner.rate.chat_ip_per_hour"
PLAN_CHAT_MONTHLY_CAP_USD = "tripplanner.spend.plan_chat_monthly_cap_usd"
PLAN_CHAT_TENANT_MONTHLY_CAP_USD = "tripplanner.spend.plan_chat_tenant_monthly_cap_usd"
LEAD_TENANT_DAILY_CAP = "tripplanner.leads.tenant_daily_cap"
LEAD_HOLD_DAYS = "tripplanner.leads.hold_days"
NARRATION_CACHE_TTL_HOURS = "tripplanner.ai.narration_cache_ttl_hours"
NARRATION_MAX_TOKENS = "tripplanner.ai.narration_max_tokens"
PLAN_CHAT_MAX_STEPS = "tripplanner.ai.plan_chat_max_steps"
PLAN_CHAT_MAX_TOKENS = "tripplanner.ai.plan_chat_max_tokens"
ADVISOR_HANDOFF_DEMO = "tripplanner.handoff.advisor_demo"
REQUIRE_REGISTRATION = "tripplanner.handoff.require_registration"


@dataclass(frozen=True)
class Setting:
    """One key: its type (int | float | bool), the bounds of its CIS definition, the config attribute that holds the
    fallback (the env var parsed at import, else the code default) and that env var's name (None: a code constant)."""

    key: str
    kind: str
    attr: str
    env: Optional[str] = None
    lo: Optional[float] = None
    hi: Optional[float] = None
    tenant_scoped: bool = False

    def fallback(self) -> Any:
        return getattr(config, self.attr)


SETTINGS: dict[str, Setting] = {s.key: s for s in (
    Setting(RATE_NARRATE_PER_HOUR, "int", "TP_RATE_NARRATE_PER_HOUR", "TP_RATE_NARRATE_PER_HOUR", 1, 200),
    Setting(RATE_SEARCH_PER_MINUTE, "int", "TP_RATE_SEARCH_PER_MINUTE", "TP_RATE_SEARCH_PER_MINUTE", 1, 600),
    Setting(RATE_SIGNED_IN_FACTOR, "int", "TP_RATE_SIGNED_IN_FACTOR", "TP_RATE_SIGNED_IN_FACTOR", 1, 10),
    Setting(RATE_SEND_PER_HOUR, "int", "TP_RATE_SEND_PER_HOUR", "TP_RATE_SEND_PER_HOUR", 1, 100),
    Setting(RATE_SEND_IP_PER_HOUR, "int", "TP_RATE_SEND_IP_PER_HOUR", "TP_RATE_SEND_IP_PER_HOUR", 1, 500),
    Setting(RATE_CHAT_PER_HOUR, "int", "TP_RATE_CHAT_PER_HOUR", "TP_RATE_CHAT_PER_HOUR", 1, 200),
    Setting(RATE_CHAT_IP_PER_HOUR, "int", "TP_RATE_CHAT_IP_PER_HOUR", "TP_RATE_CHAT_IP_PER_HOUR", 1, 1000),
    Setting(PLAN_CHAT_MONTHLY_CAP_USD, "float", "TP_PLAN_CHAT_MONTHLY_CAP_USD", "TP_PLAN_CHAT_MONTHLY_CAP_USD",
            0, 200),
    Setting(PLAN_CHAT_TENANT_MONTHLY_CAP_USD, "float", "TP_PLAN_CHAT_TENANT_MONTHLY_CAP_USD",
            "TP_PLAN_CHAT_TENANT_MONTHLY_CAP_USD", 0, 200, tenant_scoped=True),
    Setting(LEAD_TENANT_DAILY_CAP, "int", "TP_LEAD_TENANT_DAILY_CAP", "TP_LEAD_TENANT_DAILY_CAP", 1, 5000,
            tenant_scoped=True),
    Setting(LEAD_HOLD_DAYS, "int", "LEAD_HOLD_DAYS", "TP_LEAD_HOLD_DAYS", 3, 7),
    Setting(NARRATION_CACHE_TTL_HOURS, "int", "NARRATION_CACHE_TTL_HOURS", "NARRATION_CACHE_TTL_HOURS", 1, 720),
    Setting(NARRATION_MAX_TOKENS, "int", "NARRATION_MAX_TOKENS", None, 400, 4000),
    Setting(PLAN_CHAT_MAX_STEPS, "int", "PLAN_CHAT_MAX_STEPS", None, 1, 5),
    Setting(PLAN_CHAT_MAX_TOKENS, "int", "PLAN_CHAT_MAX_TOKENS", None, 200, 2000),
    Setting(ADVISOR_HANDOFF_DEMO, "bool", "ADVISOR_HANDOFF_DEMO", "ADVISOR_HANDOFF_DEMO"),
    Setting(REQUIRE_REGISTRATION, "bool", "REQUIRE_REGISTRATION_BEFORE_HANDOFF", "REQUIRE_REGISTRATION_BEFORE_HANDOFF"),
)}


def problem(s: Setting, value: Any) -> Optional[str]:
    """Why ``value`` (JSON-decoded) does not fit ``s``; None when it fits. Pure."""
    if s.kind == "bool":
        return None if isinstance(value, bool) else "not true or false"
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "not a number"
    if s.kind == "int" and float(value) != int(value):
        return "not a whole number"
    if (s.lo is not None and value < s.lo) or (s.hi is not None and value > s.hi):
        return f"outside {s.lo:g}..{s.hi:g}"
    return None


def _typed(s: Setting, value: Any) -> Any:
    return int(value) if s.kind == "int" else float(value) if s.kind == "float" else value


@dataclass
class _State:
    snapshot: Optional[dict] = None       # (key, tenant_id | None) -> (value, version)
    loaded_at: float = -1e18
    retry_after: float = 0.0
    source: str = "fallback"              # view | fallback (never read, view missing) | last_good (a read failed)
    error: Optional[str] = None


_state = _State()
_warned: set = set()
Read = Callable[[Any], Any]


async def read(db: Any) -> list:
    """One read of the view (no cache), in a savepoint when ``db`` is a connection. Raises on a database error."""
    transaction = getattr(db, "transaction", None)
    if transaction is None:
        return list(await db.fetch(SETTINGS_SQL))
    async with transaction():
        return list(await db.fetch(SETTINGS_SQL))


def invalidate() -> None:
    global _state
    _state = _State()
    _warned.clear()


def _once(event: str, mark: tuple, **kw: Any) -> None:
    if mark not in _warned:
        _warned.add(mark)
        observability.emit(event, level="error" if event.endswith("refused") else "warning", **kw)


def snapshot_of(rows: list) -> dict:
    """The snapshot of the view's rows: refused rows are left out (logged once). Unknown keys (a newer CIS migration)
    are ignored."""
    snap: dict = {}
    for r in rows:
        s = SETTINGS.get(r["key"])
        if s is None:
            continue
        try:
            value = json.loads(r["value_text"])
        except (TypeError, ValueError):
            value = None
        why = problem(s, value)
        if why is not None:
            _once("settings_value_refused", (s.key, r["version"]), key=s.key, version=r["version"], problem=why)
            continue
        snap[(s.key, r["tenant_id"])] = (_typed(s, value), int(r["version"]))
    return snap


async def refresh(db: Any, *, force: bool = False) -> str:
    """Re-read the view when the snapshot is older than CACHE_TTL_SECONDS (or ``force``). Never raises. Returns the
    source the snapshot now has (view | last_good | fallback)."""
    now = time.monotonic()
    if not force and (now - _state.loaded_at < CACHE_TTL_SECONDS or now < _state.retry_after):
        return _state.source
    try:
        rows = await read(db)
    except Exception as e:  # noqa: BLE001 — feature-detected, fail to the last good snapshot / the fallbacks
        missing = getattr(e, "sqlstate", None) in _MISSING_VIEW_STATES
        _state.retry_after = now + (VIEW_RETRY_SECONDS if missing else FAILURE_TTL_SECONDS)
        _state.error = type(e).__name__
        _state.source = "last_good" if _state.snapshot is not None else "fallback"
        if missing:
            observability.emit("settings_note", view=VIEW, error=type(e).__name__,
                               detail=f"{VIEW} is not readable yet (CIS migration 415): env values and code defaults")
        else:
            observability.emit("settings_unreadable", level="error", view=VIEW, error=type(e).__name__,
                               detail=str(e)[:200])
        return _state.source
    _state.snapshot, _state.loaded_at, _state.source, _state.error = snapshot_of(rows), now, "view", None
    return _state.source


def _env_first(s: Setting) -> bool:
    if (os.environ.get(ENV_FIRST_SWITCH) or "off").strip().lower() not in ("on", "true", "1", "yes"):
        return False
    return bool(s.env and (os.environ.get(s.env) or "").strip())


def resolve(key: str, tenant_id: Optional[str] = None) -> tuple[Any, str, Optional[int]]:
    """(value, source, version): source env | tenant | platform | fallback (module docstring)."""
    s = SETTINGS[key]
    if _env_first(s):
        _once("settings_env_first", (key, "env"), key=key, env=s.env)
        return s.fallback(), "env", None
    snap = _state.snapshot or {}
    if tenant_id is not None and s.tenant_scoped and (key, str(tenant_id)) in snap:
        value, version = snap[(key, str(tenant_id))]
        return value, "tenant", version
    if (key, None) in snap:
        value, version = snap[(key, None)]
        return value, "platform", version
    return s.fallback(), "fallback", None


def get(key: str, tenant_id: Optional[str] = None) -> Any:
    """The value in force (sync; the last snapshot, never the database)."""
    return resolve(key, tenant_id)[0]


def status() -> dict:
    """What this container serves: the snapshot's source and every key's value, source and version (ids, numbers and
    switches only). For the jobs Lambda op ``settings``."""
    out = {}
    for key in sorted(SETTINGS):
        value, source, version = resolve(key)
        out[key] = {"value": value, "source": source, "version": version}
    tenants = sorted({t for (_k, t) in (_state.snapshot or {}) if t is not None})
    return {"view": VIEW, "source": _state.source, "error": _state.error, "settings": out,
            "tenant_overrides": len(tenants)}
