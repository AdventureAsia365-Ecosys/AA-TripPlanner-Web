"""Per-session / per-IP limits on the AI routes (AA-1015, capacity plan S261 §2.5).

API Gateway throttles are per route and shared by every visitor, and a counter in the Lambda container is per
instance (up to 15 browse / 5 assembly), so one visitor or a bot could use the whole day's AI budget. This module
counts each request that would call a model in a durable fixed window, ``tripplanner.ai_rate_counter`` (tp-023), and
answers whether it may go on:

  narrate  per guest session, 1 hour window, ``config.TP_RATE_NARRATE_PER_HOUR`` (10)
  search   per traveller IP,  1 minute window, ``config.TP_RATE_SEARCH_PER_MINUTE`` (30)

A signed-in traveller gets ``config.TP_RATE_SIGNED_IN_FACTOR`` (2) times the allowance. A refused request is counted
too (a client that keeps hammering stays refused until the window ends) and answers 429 with ``retry_after_s``.

Keys: ``key_hash`` = hex HMAC-SHA256 of ``"<scope>|<kind>|<value>"``, keyed with the edge API key (the BFF's shared
secret, ``auth.expected_key``), so the table never holds an IP, a session id or an account, and an IPv4 hash cannot be
reversed by trying every address. An IPv6 address counts per /64 (one household or phone network prefix), as the CRM
limiter does.

Trust: the BFF forwards the traveller's address in ``x-tp-client-ip`` and the signed-in state in ``x-tp-signed-in``.
Both are read ONLY when the request carries the right ``x-tripplanner-key`` (:func:`client_from_headers`); without the
gate (local dev) or with a wrong key they are ignored. The address API Gateway sees is Vercel's, shared by every
traveller, so it is never used as a fallback: no trusted address = no per-IP limit (the spend cap still applies).

Fail mode: when the counter cannot be written (tp-023 not applied yet, DB error) the request is ALLOWED and one
``ai_rate_counter_error`` ERROR line is printed. The limiter protects fairness; the daily spend cap (which fails
closed, AA-642 S0) protects the budget.

Retention (data standards DM-7.1 / DM-7.2): windows older than 2 days are deleted by the jobs Lambda kind
``tp_retention_ai_rate_counter`` (backend/jobs/rate_counter_purge.py).
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from backend import config
from backend.shared import auth, observability

SCOPE_NARRATE = "narrate"
SCOPE_SEARCH = "search"
KIND_SESSION = "session"
KIND_IP = "ip"

NARRATE_WINDOW_SECONDS = 3600
SEARCH_WINDOW_SECONDS = 60

# Used to key the HMAC only where the edge gate is disabled (local dev, unit tests): there is no secret to use.
_LOCAL_PEPPER = "tripplanner-local-ratelimit"

UPSERT_SQL = """
    INSERT INTO tripplanner.ai_rate_counter (key_hash, window_start, count)
    VALUES ($1, to_timestamp(floor(extract(epoch FROM now()) / $2::int) * $2::int), 1)
    ON CONFLICT (key_hash, window_start)
    DO UPDATE SET count = tripplanner.ai_rate_counter.count + 1
    RETURNING count,
              GREATEST(1, ceil(extract(epoch FROM (window_start + make_interval(secs => $2::int)) - now())))::int
                  AS retry_after_s
"""


@dataclass(frozen=True)
class Rule:
    scope: str
    window_s: int
    limit: int


@dataclass(frozen=True)
class Client:
    """What the BFF told the Lambda about the traveller, already checked against the edge key."""
    ip: Optional[str] = None
    signed_in: bool = False


UNKNOWN_CLIENT = Client()


@dataclass(frozen=True)
class Decision:
    allowed: bool
    scope: str
    limit: int
    window_s: int
    count: Optional[int] = None
    retry_after_s: int = 0
    reason: Optional[str] = None  # "counter_unavailable" when it failed open


def rule_for(scope: str, *, signed_in: bool) -> Rule:
    factor = max(1, int(config.TP_RATE_SIGNED_IN_FACTOR)) if signed_in else 1
    if scope == SCOPE_NARRATE:
        return Rule(scope, NARRATE_WINDOW_SECONDS, int(config.TP_RATE_NARRATE_PER_HOUR) * factor)
    if scope == SCOPE_SEARCH:
        return Rule(scope, SEARCH_WINDOW_SECONDS, int(config.TP_RATE_SEARCH_PER_MINUTE) * factor)
    raise ValueError(f"unknown rate-limit scope {scope!r}")


def normalise_ip(raw: Optional[str]) -> Optional[str]:
    """The address to count by: IPv4 as is, IPv6 as its /64 network; None for anything that is not an address."""
    try:
        addr = ipaddress.ip_address((raw or "").strip())
    except ValueError:
        return None
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped is not None:
            return str(addr.ipv4_mapped)
        return str(ipaddress.IPv6Network(f"{addr}/64", strict=False))
    return str(addr)


def _key_is_proven(headers: Mapping[str, Any]) -> Optional[str]:
    """The edge key when the request carries it (the BFF), else None (gate disabled, wrong or no key)."""
    try:
        expected = auth.expected_key()
    except auth.ApiKeyUnavailable:
        return None
    if not expected:
        return None
    provided = str(headers.get(config.API_KEY_HEADER) or "")
    return expected if provided and hmac.compare_digest(provided, str(expected)) else None


def client_from_headers(headers: Mapping[str, Any]) -> Client:
    """`headers` lower-cased. The BFF's client headers count only together with the right edge key."""
    if _key_is_proven(headers) is None:
        return UNKNOWN_CLIENT
    return Client(ip=normalise_ip(headers.get(config.CLIENT_IP_HEADER)),
                  signed_in=str(headers.get(config.SIGNED_IN_HEADER) or "").strip() == "1")


def _pepper() -> str:
    try:
        return auth.expected_key() or _LOCAL_PEPPER
    except auth.ApiKeyUnavailable:
        return _LOCAL_PEPPER


def key_hash(scope: str, kind: str, value: str) -> str:
    return hmac.new(_pepper().encode(), f"{scope}|{kind}|{value}".encode(), hashlib.sha256).hexdigest()


async def hit(db: Any, scope: str, kind: str, value: str, *, signed_in: bool = False) -> Decision:
    """Count one request in the current window and say whether it may go on. Never raises (fails open)."""
    rule = rule_for(scope, signed_in=signed_in)
    try:
        row = await db.fetchrow(UPSERT_SQL, key_hash(scope, kind, value), rule.window_s)
        if row is None:
            raise LookupError("the counter upsert returned no row")
        count, retry_after = int(row["count"]), int(row["retry_after_s"])
    except Exception as e:  # noqa: BLE001 — fairness limiter: allow, say so loudly
        observability.emit("ai_rate_counter_error", level="error", scope=scope, error=type(e).__name__,
                           detail=str(e)[:200])
        return Decision(True, scope, rule.limit, rule.window_s, reason="counter_unavailable")
    if count > rule.limit:
        observability.emit("ai_rate_limited", scope=scope, kind=kind, limit=rule.limit, count=count,
                           window_s=rule.window_s, signed_in=signed_in, retry_after_s=retry_after)
        return Decision(False, scope, rule.limit, rule.window_s, count, retry_after)
    return Decision(True, scope, rule.limit, rule.window_s, count)


def limited_body(decision: Decision) -> dict:
    """The 429 body: the frontend shows its own EN / VI sentence from `scope`."""
    return {"error": "rate_limited", "scope": decision.scope, "limit": decision.limit,
            "window_s": decision.window_s, "retry_after_s": decision.retry_after_s}


def limited_headers(decision: Decision, base: Mapping[str, str]) -> dict:
    return {**base, "retry-after": str(decision.retry_after_s)}
