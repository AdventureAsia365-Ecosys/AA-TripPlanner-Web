"""AA-1143 (tech lead decision G3; CIS 455 / 456): TripPlanner's share of each LLM connection's monthly budget, read
from the CIS contract view ``catalog.v_llm_connection_cap_v2`` (root ADR 0009 d13: the control plane TripPlanner
already reads; ``SELECT`` to ``tripplanner`` only).

A connection's budget is one set of rows in CIS: its total, one fixed-USD share per app, CIS taking the remainder (CIS
AI gateway › Budgets). The view gives one row per connection and app; TripPlanner reads its own rows
(``app = 'tripplanner'``):

- with a share of its own (``in_cis_pool`` false): ``stops`` = its own spend this UTC month (its rows of
  ``shared.llm_call_log`` on the connection) at or above the share, under a hard-stop connection policy;
- without one (``in_cis_pool`` true, today): it spends inside the CIS remainder and ``stops`` follows the remainder.

What a stop does is the setting ``tripplanner.spend.connection_share_enforce`` (CIS 456, default false): false = log
only (``llm_connection_share_reached``, once per connection and cache window) and the call goes on (the first release
of the shares); true = under the dev spend rules (``tripplanner.spend.environment``) the gateway leaves the connection
out of the route (``llm_gateway.deployment_plan``), under prod it only logs (pay per use, S283).

Reads: one query, cached ``CACHE_TTL_SECONDS`` per container (the gateway view's pace), refreshed by
``llm_gateway.load_route``. Fail open, the app profile's rule: the view missing or not granted (SQLSTATE 42P01 / 42501,
CIS 456 not applied, retried after ``VIEW_RETRY_SECONDS``) or any other read error (retried after
``FAILURE_TTL_SECONDS``, one ``llm_gateway_error`` line) = no share known, nothing left out. The CIS gateway keeps its
own hard stop on the CIS remainder either way.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional

from backend.shared import observability, settings

APP = "tripplanner"
VIEW = "catalog.v_llm_connection_cap_v2"
COLUMNS = ("connection_key", "share_usd", "in_cis_pool", "month_spent_usd", "cis_pool_spent_usd", "hard_stop",
           "over_cap", "stops")
# Column names, never *, so a column CIS appends changes nothing (contract v2).
SHARE_SQL = f"SELECT {', '.join(COLUMNS)} FROM {VIEW} WHERE app = '{APP}'"  # noqa: S608 — fixed identifiers
CACHE_TTL_SECONDS = 20.0  # technical-constant: reader cache pace (the app profile's)
FAILURE_TTL_SECONDS = 60.0  # technical-constant: retry pace after a read error
VIEW_RETRY_SECONDS = 300.0  # technical-constant: retry pace while the view is not migrated
_MISSING_VIEW_STATES = ("42P01", "42501")


@dataclass(frozen=True)
class Share:
    connection_key: str
    share_usd: Optional[float]
    in_cis_pool: bool
    month_spent_usd: float
    cis_pool_spent_usd: float
    hard_stop: bool
    over_cap: bool
    stops: bool


@dataclass
class _State:
    shares: dict
    loaded_at: float = -1e18
    retry_after: float = 0.0
    logged: Optional[set] = None


_state = _State(shares={})


def reset() -> None:
    """Tests: forget the cache."""
    global _state
    _state = _State(shares={})


def _f(v: Any) -> float:
    return float(v) if v is not None else 0.0


def _row(r: Any) -> Share:
    share = r["share_usd"]
    return Share(connection_key=str(r["connection_key"]), share_usd=None if share is None else float(share),
                 in_cis_pool=bool(r["in_cis_pool"]), month_spent_usd=_f(r["month_spent_usd"]),
                 cis_pool_spent_usd=_f(r["cis_pool_spent_usd"]), hard_stop=bool(r["hard_stop"]),
                 over_cap=bool(r["over_cap"]), stops=bool(r["stops"]))


async def _fetch(db: Any) -> list:
    if hasattr(db, "acquire"):
        async with db.acquire() as conn:
            return list(await conn.fetch(SHARE_SQL))
    async with db.transaction():          # a savepoint on the caller's connection: a failed read never aborts it
        return list(await db.fetch(SHARE_SQL))


async def refresh(db: Any, *, now: Optional[float] = None) -> None:
    """Re-read the view when the cache is older than ``CACHE_TTL_SECONDS`` (and no retry pause runs). Never raises."""
    now = time.monotonic() if now is None else now
    if now - _state.loaded_at < CACHE_TTL_SECONDS or now < _state.retry_after:
        return
    try:
        rows = await _fetch(db)
    except Exception as e:  # noqa: BLE001 — fail open, see the module docstring
        missing = getattr(e, "sqlstate", None) in _MISSING_VIEW_STATES
        _state.retry_after = now + (VIEW_RETRY_SECONDS if missing else FAILURE_TTL_SECONDS)
        if not missing:
            observability.emit("llm_gateway_error", level="error", reason="connection_share_unreadable",
                               detail=str(e)[:200])
        return
    _state.shares = {s.connection_key: s for s in (_row(r) for r in rows)}
    _state.loaded_at, _state.retry_after, _state.logged = now, 0.0, set()


def share_of(connection_key: str) -> Optional[Share]:
    """TripPlanner's row for a connection from the last read (None: no total on it, or nothing read)."""
    return _state.shares.get(connection_key)


def enforced() -> bool:
    """True when a reached share leaves the connection out: the switch on AND the dev spend rules."""
    try:
        return bool(settings.get(settings.CONNECTION_SHARE_ENFORCE)) and not settings.spend_prod()
    except Exception:  # noqa: BLE001 — never on the call path: log only
        return False


def leave_out(stage: str, connection_key: str) -> bool:
    """For ``llm_gateway.deployment_plan``: True when this connection must be left out of the route now. A reached
    share is logged once per connection and cache window whatever the switch says. Never raises."""
    s = share_of(connection_key)
    if s is None or not s.stops:
        return False
    enforce = enforced()
    logged = _state.logged if _state.logged is not None else set()
    if connection_key not in logged:
        logged.add(connection_key)
        _state.logged = logged
        observability.emit("llm_connection_share_reached", level="warning", stage=stage, connection=connection_key,
                           share_usd=s.share_usd, in_cis_pool=s.in_cis_pool,
                           spent_usd=round(s.cis_pool_spent_usd if s.in_cis_pool else s.month_spent_usd, 4),
                           enforced=enforce)
    return enforce
