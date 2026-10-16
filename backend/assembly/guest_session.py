"""Guest session credential (AA-827).

The BFF owns a random guest token (32 bytes, base64url, 43 chars) in an HttpOnly cookie and sends
it in the `x-tripplanner-guest` header on every mutating call. `tripplanner.sessions` stores only
sha256(token) in `guest_token_hash` (migration tp-011); the token itself is never stored, returned
or logged. `session_id` stays a non-secret identifier: what proves a request belongs to a session is
the token whose hash is bound to that session row.

One cookie can bind several session rows (after a 401 reset the browser makes a new session_id but
keeps its cookie), so there is no unique index on the hash: the lookup is always by `sessions.id`
(PK) followed by a constant-time compare.

Grace window (`config.GUEST_LEGACY_GRACE_UNTIL`): until then a session created before AA-827 (hash
NULL) is bound to the first token it sees, and a request with no token at all (an old BFF or an old
bundle) is still accepted for a session that has no hash. After it, only a matching token passes.
The window is closed (the constant is in the past since S239: Dev has no real guests to carry
over), so today a legacy session, a tokenless request and a bare session id all get 401. The grace
branches below are kept, dormant, in case a future environment needs a rollout window again.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from datetime import datetime, timezone
from typing import Any, Optional

from backend import config

TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{43}")

# Outcomes that let the request through.
TOKEN_OK = "token_ok"                  # token matches the session's hash
TOKEN_NEW = "token_new"                # new session row created with this token's hash
LEGACY_BOUND = "legacy_bound"          # pre-AA-827 session bound to this token (grace window)
LEGACY_TOKENLESS = "legacy_tokenless"  # no token, session has no hash (grace window)
# Denials -> 401 guest_session_invalid.
REJECTED_GRACE = "rejected_grace"      # legacy session or tokenless request after the cutoff
BAD_TOKEN = "bad_token"                # header present but not a well-formed token (no SQL issued)
TOKEN_MISMATCH = "token_mismatch"      # token does not match the session's hash
TOKENLESS_BOUND = "tokenless_bound"    # no token for a session that already has a hash (downgrade)
# Emitted by the handler's owner check -> 403.
FORBIDDEN_OWNER = "forbidden_owner"

DENIED = frozenset({REJECTED_GRACE, BAD_TOKEN, TOKEN_MISMATCH, TOKENLESS_BOUND})

_SELECT = "SELECT guest_token_hash FROM tripplanner.sessions WHERE id = $1::uuid"
_INSERT = (
    "INSERT INTO tripplanner.sessions (id, guest_token_hash, expires_at) "
    "VALUES ($1::uuid, $2, now() + make_interval(days => $3::int)) "
    "ON CONFLICT (id) DO NOTHING"
)
_BIND = (
    "UPDATE tripplanner.sessions SET guest_token_hash = $1 "
    "WHERE id = $2::uuid AND guest_token_hash IS NULL"
)


def is_token(value: Any) -> bool:
    return isinstance(value, str) and TOKEN_RE.fullmatch(value) is not None


def hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def in_grace(now: datetime) -> bool:
    return now < config.GUEST_LEGACY_GRACE_UNTIL


async def _stored(conn, session_id: str) -> tuple[bool, Optional[bytes]]:
    """(row exists, stored hash or None)."""
    row = await conn.fetchrow(_SELECT, session_id)
    if row is None:
        return False, None
    h = row["guest_token_hash"]
    return True, (bytes(h) if h is not None else None)


def _matches(stored: Optional[bytes], digest: bytes) -> bool:
    return stored is not None and hmac.compare_digest(stored, digest)


async def authorize(conn, session_id: str, token: Optional[str], *,
                    now: Optional[datetime] = None) -> str:
    """Check (and on first use create or bind) the session for this token. Returns an outcome;
    the request is denied when it is in DENIED. Only the digest is ever sent to SQL.

    The caller has already checked that session_id is a UUID. `token` is the raw header value
    (None when absent); a malformed value is rejected before any query.
    """
    if token is not None and not is_token(token):
        return BAD_TOKEN
    now = now or datetime.now(timezone.utc)
    exists, stored = await _stored(conn, session_id)

    if token is not None:
        digest = hash_token(token)
        if not exists:
            await conn.execute(_INSERT, session_id, digest, config.GUEST_SESSION_DAYS)
            _, stored = await _stored(conn, session_id)  # a concurrent first request may have won
            return TOKEN_NEW if _matches(stored, digest) else TOKEN_MISMATCH
        if stored is None:  # pre-AA-827 session (its old credential was the id itself)
            if not in_grace(now):
                return REJECTED_GRACE
            await conn.execute(_BIND, digest, session_id)
            _, stored = await _stored(conn, session_id)
            return LEGACY_BOUND if _matches(stored, digest) else TOKEN_MISMATCH
        return TOKEN_OK if _matches(stored, digest) else TOKEN_MISMATCH

    # No token: an old BFF / old bundle, or a direct caller.
    if not in_grace(now):
        return REJECTED_GRACE
    if stored is not None:
        return TOKENLESS_BOUND
    if not exists:
        await conn.execute(_INSERT, session_id, None, config.GUEST_SESSION_DAYS)
    return LEGACY_TOKENLESS


def emit(outcome: str, route: str) -> None:
    """One JSON log line per guarded request. Never carries a token, hash, session_id or trip_id."""
    print(json.dumps({"evt": "guest_auth", "outcome": outcome, "route": route}), flush=True)
