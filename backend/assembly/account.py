"""Traveller accounts (AA-908, root ADR 0006 Phase 4; tables of migration tp-019).

A traveller signs in on the Cognito EXTERNAL pool (public `tripplanner` client, email code only). The BFF keeps the
tokens in a sealed HttpOnly cookie and forwards the access token to this Lambda in the `x-tripplanner-access`
header. Here it is verified (backend/shared/identity_verifier.py, a byte copy of AA-CIS-App's: RS256 only, the
EXTERNAL issuer and the tripplanner client only, access tokens only) and turned into a :class:`Caller`.

An account is the verified identity (iss, sub) and nothing else: `tripplanner.traveller_accounts` has no email. The
email is read from the token when it is needed (the hand-off, handoff.signed_in_contact).

What an account can do:
- **Claim** (POST /me/claim, at sign-in): every session bound to the browser's guest token (sha256 match, the same
  proof as a mutation) that no account owns yet is linked to the account. A session already owned by an account is
  never moved (`account_id IS NULL` in the UPDATE), so a guest token replayed after a claim cannot pull its trips
  into a second account.
- **Link on use** (handler._guard): a signed-in mutation whose guest token proves the session links that session too,
  so a trip started after sign-in is listed as well.
- **Open its trips anywhere** (handler._guard / _read_guard): when the guest token does not prove the session (a
  second device has its own cookie), a valid access token of the account that owns the session does.
- **List its trips** (GET /me/trips).

Without a valid token nothing changes: guards answer exactly as before (401 / 403 / the unknown-route 404). A bad or
expired access token is treated as no token by the guards, and as 401 `sign_in_required` by the /me routes. Log
lines carry outcomes and counts only, never iss, sub, email or a token (DM-6.4).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

from backend.shared import identity_config, observability
from backend.shared.identity_verifier import AuthUnavailable, InvalidToken, MultiIssuerVerifier, Principal

# Caller states.
ABSENT = "absent"            # no access token on the request (a guest)
OK = "ok"                    # a valid EXTERNAL x tripplanner access token
INVALID = "invalid"          # a token that is not acceptable (expired, other client, ID token, bad signature, ...)
UNAVAILABLE = "unavailable"  # no JWKS could ever be loaded (NAT down on a cold container): the /me routes answer 503

_BEARER = "bearer "

# How many trips GET /me/trips returns (newest first); and how many stop names one trip shows.
MAX_TRIPS = 100
MAX_STOP_NAMES = 3


@dataclass(frozen=True)
class Caller:
    state: str = ABSENT
    principal: Optional[Principal] = None

    @property
    def signed_in(self) -> bool:
        return self.state == OK and self.principal is not None


GUEST = Caller()


async def caller_from_header(value: Optional[str], verifier: Optional[MultiIssuerVerifier] = None) -> Caller:
    """The caller proved by the `x-tripplanner-access` header value ("Bearer <access token>", or the bare token)."""
    if value is None or not value.strip():
        return GUEST
    token = value.strip()
    if token[: len(_BEARER)].lower() == _BEARER:
        token = token[len(_BEARER):].strip()
    try:
        principal = await (verifier or identity_config.get_verifier()).verify(token)
    except InvalidToken:
        observability.emit("traveller_auth", outcome=INVALID)
        return Caller(INVALID)
    except AuthUnavailable:
        observability.emit("traveller_auth", outcome=UNAVAILABLE)
        return Caller(UNAVAILABLE)
    return Caller(OK, principal)


# --- SQL -------------------------------------------------------------------------------------------------------

FIND_SQL = "SELECT id FROM tripplanner.traveller_accounts WHERE iss = $1 AND sub = $2"
INSERT_SQL = (
    "INSERT INTO tripplanner.traveller_accounts (iss, sub) VALUES ($1, $2) "
    "ON CONFLICT (iss, sub) DO NOTHING RETURNING id"
)
SIGN_IN_SQL = (
    "INSERT INTO tripplanner.traveller_accounts (iss, sub, last_sign_in_at) VALUES ($1, $2, now()) "
    "ON CONFLICT (iss, sub) DO UPDATE SET last_sign_in_at = now() RETURNING id"
)
# The claim: every session bound to this guest token that no account owns yet. Never moves an owned session.
CLAIM_SQL = (
    "UPDATE tripplanner.sessions SET account_id = $1 "
    "WHERE guest_token_hash = $2 AND account_id IS NULL"
)
LINK_SQL = "UPDATE tripplanner.sessions SET account_id = $1 WHERE id = $2::uuid AND account_id IS NULL"
OWNS_SESSION_SQL = (
    "SELECT 1 FROM tripplanner.sessions s JOIN tripplanner.traveller_accounts a ON a.id = s.account_id "
    "WHERE s.id = $1::uuid AND a.iss = $2 AND a.sub = $3"
)
# The trip's owner is the session that created its draft (trip_drafts.session_id, never changed).
OWNS_TRIP_SQL = (
    "SELECT 1 FROM tripplanner.trip_drafts d JOIN tripplanner.sessions s ON s.id = d.session_id "
    "JOIN tripplanner.traveller_accounts a ON a.id = s.account_id "
    "WHERE d.id = $1::uuid AND a.iss = $2 AND a.sub = $3"
)
LIST_SQL = (
    "SELECT d.id AS trip_id, d.session_id, d.status, d.itinerary, d.date_start, d.date_end, d.created_at, "
    "d.updated_at FROM tripplanner.trip_drafts d JOIN tripplanner.sessions s ON s.id = d.session_id "
    "JOIN tripplanner.traveller_accounts a ON a.id = s.account_id "
    "WHERE a.iss = $1 AND a.sub = $2 ORDER BY d.updated_at DESC LIMIT $3"
)


async def sign_in(conn, principal: Principal) -> Any:
    """Upsert the account and stamp last_sign_in_at; returns its id."""
    row = await conn.fetchrow(SIGN_IN_SQL, principal.iss, principal.sub)
    return row["id"]


async def account_id(conn, principal: Principal) -> Any:
    """The account's id, creating the row when it is missing (no sign-in stamp)."""
    row = await conn.fetchrow(FIND_SQL, principal.iss, principal.sub)
    if row is None:
        row = await conn.fetchrow(INSERT_SQL, principal.iss, principal.sub)
    if row is None:  # a concurrent insert won the race
        row = await conn.fetchrow(FIND_SQL, principal.iss, principal.sub)
    return row["id"]


def _updated(status: Any) -> int:
    """Row count of an asyncpg command tag ("UPDATE 2" -> 2)."""
    try:
        return int(str(status).rsplit(" ", 1)[-1])
    except ValueError:
        return 0


async def claim(conn, principal: Principal, token_hash: Optional[bytes]) -> int:
    """Sign-in: upsert the account, then link every unowned session of this guest token. Returns how many."""
    acc = await sign_in(conn, principal)
    if token_hash is None:
        return 0
    return _updated(await conn.execute(CLAIM_SQL, acc, token_hash))


async def link_session(conn, principal: Principal, session_id: str) -> None:
    """A signed-in mutation proved this session with its guest token: link it when no account owns it yet."""
    await conn.execute(LINK_SQL, await account_id(conn, principal), session_id)


async def owns_session(conn, principal: Principal, session_id: str) -> bool:
    return await conn.fetchrow(OWNS_SESSION_SQL, session_id, principal.iss, principal.sub) is not None


async def owns_trip(conn, principal: Principal, trip_id: str) -> bool:
    return await conn.fetchrow(OWNS_TRIP_SQL, trip_id, principal.iss, principal.sub) is not None


def _iso(value: Any) -> Optional[str]:
    return value.isoformat() if hasattr(value, "isoformat") else value


def trip_summary(row: Any) -> dict:
    """One My Trips entry from a trip_drafts row: ids, TripPlanner status (draft | sent), dates and a short view of
    the stops (the projection's served stops; a stop AA no longer serves is counted apart)."""
    raw = row["itinerary"]
    if isinstance(raw, str):
        raw = json.loads(raw)
    stops = [e for e in raw if isinstance(e, dict)] if isinstance(raw, list) else []
    served = [e for e in stops if e.get("available", True) is not False]
    names: list[str] = []
    for e in sorted(served, key=lambda e: (e.get("day") if isinstance(e.get("day"), int) else 0)):
        name = e.get("destination_name") or e.get("name")
        if isinstance(name, str) and name and name not in names:
            names.append(name)
    days = {e.get("day") for e in served if isinstance(e.get("day"), int)}
    return {
        "trip_id": str(row["trip_id"]),
        "session_id": str(row["session_id"]),
        "status": row["status"],
        "stops": len(served),
        "unavailable_stops": len(stops) - len(served),
        "days": len(days),
        "stop_names": names[:MAX_STOP_NAMES],
        "date_start": _iso(row["date_start"]),
        "date_end": _iso(row["date_end"]),
        "created_at": _iso(row["created_at"]),
        "updated_at": _iso(row["updated_at"]),
    }


async def list_trips(conn, principal: Principal) -> list[dict]:
    """The trips of every session this account owns, newest first. Only ever the caller's own."""
    rows = await conn.fetch(LIST_SQL, principal.iss, principal.sub, MAX_TRIPS)
    return [trip_summary(r) for r in rows]
