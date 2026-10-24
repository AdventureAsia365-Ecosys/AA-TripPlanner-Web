"""AA-908 integration — the traveller-account SQL (assembly/account.py) and the account-aware guards on a real Postgres.

Runs only with ``pytest --pg-dsn postgresql://postgres@127.0.0.1:<port>/postgres`` against a throwaway LOCAL
Postgres (non-local hosts are refused); each test creates and drops its own ``tp_aa908_*`` database.

The tables are the columns the account SQL and the guards read, with the tp-019 constraints copied from
migrations/019_traveller_accounts.sql (UNIQUE (iss, sub), the iss / sub CHECKs, sessions.account_id ON DELETE SET
NULL); the migration itself is replayed by migrations/tests/test_tp019_pg.py.
"""
from __future__ import annotations

import base64
import uuid
from urllib.parse import urlparse, urlunparse

import pytest

asyncpg = pytest.importorskip("asyncpg")

from backend.assembly import account, guest_session, handler  # noqa: E402
from backend.shared.identity_verifier import Principal  # noqa: E402

SCHEMA = """
CREATE SCHEMA tripplanner;
CREATE TABLE tripplanner.traveller_accounts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), iss text NOT NULL, sub text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(), last_sign_in_at timestamptz,
    CONSTRAINT traveller_accounts_iss_sub_key UNIQUE (iss, sub),
    CONSTRAINT traveller_accounts_iss_chk CHECK (iss ~ '^https://' AND length(iss) <= 300),
    CONSTRAINT traveller_accounts_sub_chk CHECK (length(sub) BETWEEN 1 AND 128));
CREATE TABLE tripplanner.sessions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), guest_token text, guest_token_hash bytea,
    customer_id uuid, created_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz,
    account_id uuid REFERENCES tripplanner.traveller_accounts (id) ON DELETE SET NULL);
CREATE TABLE tripplanner.trip_drafts (
    id uuid PRIMARY KEY, session_id uuid NOT NULL REFERENCES tripplanner.sessions (id),
    status text NOT NULL DEFAULT 'draft', itinerary jsonb NOT NULL DEFAULT '[]', date_start date, date_end date,
    created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now());
"""

ISS = "https://cognito-idp.us-west-1.amazonaws.com/us-west-1_TESTPOOLX"
A = Principal(iss=ISS, sub="sub-a", client_id="tp", groups=(), email="a@example.com", email_verified=True,
              username="sub-a")
B = Principal(iss=ISS, sub="sub-b", client_id="tp", groups=(), email="b@example.com", email_verified=True,
              username="sub-b")
# Guest tokens in the BFF's shape (43 base64url chars), built at run time so no literal looks like a secret.
TOKEN = base64.urlsafe_b64encode(bytes(range(32))).rstrip(b"=").decode()
OTHER = base64.urlsafe_b64encode(bytes(range(32, 64))).rstrip(b"=").decode()


@pytest.fixture(scope="module")
def pg_dsn(request):
    dsn = request.config.getoption("--pg-dsn")
    if not dsn:
        pytest.skip("integration: pass --pg-dsn for a throwaway local Postgres")
    host = urlparse(dsn).hostname or "localhost"
    if host not in ("localhost", "127.0.0.1", "::1"):
        pytest.skip(f"integration tests only run against a local Postgres, not {host}")
    return dsn


@pytest.fixture
async def conn(pg_dsn):
    name = f"tp_aa908_{uuid.uuid4().hex[:8]}"
    admin = await asyncpg.connect(pg_dsn)
    await admin.execute(f'CREATE DATABASE "{name}"')
    c = await asyncpg.connect(urlunparse(urlparse(pg_dsn)._replace(path=f"/{name}")))
    try:
        await c.execute(SCHEMA)
        yield c
    finally:
        await c.close()
        await admin.execute(f'DROP DATABASE "{name}"')
        await admin.close()


async def _session(conn, token: str) -> str:
    sid = str(uuid.uuid4())
    await conn.execute("INSERT INTO tripplanner.sessions (id, guest_token_hash) VALUES ($1::uuid, $2)",
                       sid, guest_session.hash_token(token))
    return sid


async def _trip(conn, session_id: str, itinerary: str = "[]", status: str = "draft") -> str:
    tid = str(uuid.uuid4())
    await conn.execute("INSERT INTO tripplanner.trip_drafts (id, session_id, status, itinerary) "
                       "VALUES ($1::uuid, $2::uuid, $3, $4::jsonb)", tid, session_id, status, itinerary)
    return tid


async def _owner(conn, session_id: str):
    return await conn.fetchval("SELECT account_id FROM tripplanner.sessions WHERE id = $1::uuid", session_id)


async def test_claim_links_unowned_sessions_of_the_token_once(conn):
    mine1, mine2, other = await _session(conn, TOKEN), await _session(conn, TOKEN), await _session(conn, OTHER)
    assert await account.claim(conn, A, guest_session.hash_token(TOKEN)) == 2
    acc_a = await conn.fetchval("SELECT id FROM tripplanner.traveller_accounts WHERE sub = 'sub-a'")
    assert await _owner(conn, mine1) == acc_a and await _owner(conn, mine2) == acc_a
    assert await _owner(conn, other) is None
    assert await conn.fetchval("SELECT last_sign_in_at IS NOT NULL FROM tripplanner.traveller_accounts") is True
    # Again, and by another account with the same cookie: nothing moves.
    assert await account.claim(conn, A, guest_session.hash_token(TOKEN)) == 0
    assert await account.claim(conn, B, guest_session.hash_token(TOKEN)) == 0
    assert await _owner(conn, mine1) == acc_a
    assert await conn.fetchval("SELECT count(*) FROM tripplanner.traveller_accounts") == 2


async def test_link_session_and_account_id_create_the_account_once(conn):
    sid = await _session(conn, TOKEN)
    await account.link_session(conn, A, sid)
    await account.link_session(conn, B, sid)                  # owned: not moved
    acc_a = await account.account_id(conn, A)
    assert await _owner(conn, sid) == acc_a
    assert await conn.fetchval("SELECT last_sign_in_at FROM tripplanner.traveller_accounts WHERE id = $1",
                               acc_a) is None


async def test_ownership_checks_and_my_trips(conn):
    sid, other = await _session(conn, TOKEN), await _session(conn, OTHER)
    itinerary = ('[{"day": 1, "component_id": "c1", "name": "Old Quarter", "destination_name": "Hanoi", '
                 '"available": true}, {"day": 2, "component_id": "c2", "name": "Gone", "available": false}]')
    t1 = await _trip(conn, sid, itinerary, "sent")
    t2 = await _trip(conn, other)
    await account.claim(conn, A, guest_session.hash_token(TOKEN))
    await account.claim(conn, B, guest_session.hash_token(OTHER))
    assert await account.owns_session(conn, A, sid) and not await account.owns_session(conn, B, sid)
    assert await account.owns_trip(conn, A, t1) and not await account.owns_trip(conn, A, t2)
    assert not await account.owns_trip(conn, A, str(uuid.uuid4()))
    (trip,) = await account.list_trips(conn, A)
    assert (trip["trip_id"], trip["session_id"], trip["status"]) == (t1, sid, "sent")
    assert (trip["stops"], trip["unavailable_stops"], trip["days"], trip["stop_names"]) == (1, 1, 1, ["Hanoi"])
    assert trip["updated_at"] and trip["created_at"]
    assert [t["trip_id"] for t in await account.list_trips(conn, B)] == [t2]


async def test_read_guard_lets_the_owner_account_in_from_another_cookie(conn):
    sid = await _session(conn, TOKEN)
    trip = await _trip(conn, sid)
    await account.claim(conn, A, guest_session.hash_token(TOKEN))
    a, b = account.Caller(account.OK, A), account.Caller(account.OK, B)
    assert await handler._read_guard(conn, trip, OTHER, "get_trip", a) is None  # noqa: SLF001
    assert await handler._read_guard(conn, trip, OTHER, "get_trip", b) == handler._not_found()  # noqa: SLF001
    assert await handler._read_guard(conn, trip, OTHER, "get_trip") == handler._not_found()  # noqa: SLF001


async def test_erasing_an_account_leaves_its_sessions_as_guest_sessions(conn):
    sid = await _session(conn, TOKEN)
    await account.claim(conn, A, guest_session.hash_token(TOKEN))
    await conn.execute("DELETE FROM tripplanner.traveller_accounts WHERE sub = 'sub-a'")
    assert await _owner(conn, sid) is None
    assert await account.list_trips(conn, A) == []
