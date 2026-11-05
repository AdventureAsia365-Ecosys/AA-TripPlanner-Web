"""AA-1038 R5 slice 1 integration — the assembly Lambda's tenant context against the real tp-025 shadow policy.

Runs only with ``pytest --pg-dsn postgresql://postgres@127.0.0.1:<port>/postgres`` (a SUPERUSER DSN of a throwaway
LOCAL Postgres; other hosts are refused). The database is built by the same replay as the migration tests
(``migrations/tests/test_tp025_pg.py``: tp-001 … tp-024 as the live roles, invented rows in the four tables, then
tp-025 as ``tp_owner``); the code then runs ``handler.serve`` as the logged-in runtime role ``tripplanner``, so row
security applies to it exactly as on the Lambda.

Checks: a trip created under the demo tenant is stored with the demo tenant, answers 200 from GET /trip/{id} under the
demo tenant and the same 404 as an unknown trip under a forged tenant; a mutation under a forged tenant reaches no
session (401) and writes nothing; a trip written before tp-025 (backfilled) still loads with its guest token; and the
connection carries no tenant context after the request (unbind, not only the pool's RESET ALL).
"""
from __future__ import annotations

import hashlib
import json
import secrets
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

import pytest

asyncpg = pytest.importorskip("asyncpg")

MIGRATION_TESTS = Path(__file__).resolve().parents[2] / "migrations" / "tests"
if str(MIGRATION_TESTS) not in sys.path:
    sys.path.insert(0, str(MIGRATION_TESTS))

import test_tp025_pg as replay  # noqa: E402  (the tp-025 replay fixture: same database the migration test builds)

from backend import config  # noqa: E402
from backend.assembly import handler  # noqa: E402
from backend.shared import tenant  # noqa: E402

DEMO = config.DEMO_TENANT_ID
FORGED = "7e57c0de-0000-4000-8000-00000000beef"


@pytest.fixture(scope="module")
def pg_dsn(request):
    dsn = request.config.getoption("--pg-dsn")
    if not dsn:
        pytest.skip("integration: pass --pg-dsn with a superuser DSN of a throwaway local Postgres")
    host = urlparse(dsn).hostname or "localhost"
    if host not in ("localhost", "127.0.0.1", "::1"):
        pytest.skip(f"integration tests only run against a local Postgres, not {host}")
    return dsn


@pytest.fixture(scope="module")
def db(pg_dsn):
    """tp-024 + seeded rows + tp-025 (sync: the replay helpers run their own event loop)."""
    ctx, error = replay._database(pg_dsn)
    try:
        assert error is None, error
        yield ctx
    finally:
        replay._drop(pg_dsn, ctx["name"])


@pytest.fixture
async def tp_conn(db):
    """One plain connection as the runtime role (no pool, so nothing resets it behind the code's back)."""
    conn = await asyncpg.connect(db["tp_dsn"])
    try:
        yield conn
    finally:
        await conn.close()


def _acquire(conn):
    @asynccontextmanager
    async def acquire():
        yield conn

    return acquire


def _token() -> str:
    return secrets.token_urlsafe(32)  # 43 url-safe characters, the BFF's guest token shape


async def _serve(conn, method, path, body=None, token=None):
    out = await handler.serve(method, path, body or {}, guest_token=token, acquire=_acquire(conn))
    return out["statusCode"], json.loads(out["body"])


async def _create_trip(conn) -> tuple[str, str, str]:
    """A guest session and a trip with one free day, created through the real route under the bound tenant."""
    session, trip, token = str(uuid.uuid4()), str(uuid.uuid4()), _token()
    status, body = await _serve(conn, "POST", f"/trip/{trip}/days", {"session_id": session, "kind": "free_day"},
                                token)
    assert status == 200, body
    return session, trip, token


async def _guc(conn) -> str | None:
    return await conn.fetchval("SELECT current_setting('app.tenant_id', true) AS guc")


async def _tenants(su, session, trip) -> dict:
    return {
        "sessions": await su.fetchval("SELECT array_agg(DISTINCT tenant_id::text) AS t FROM tripplanner.sessions "
                                      "WHERE id = $1::uuid", session),
        "trip_drafts": await su.fetchval("SELECT array_agg(DISTINCT tenant_id::text) AS t "
                                         "FROM tripplanner.trip_drafts WHERE id = $1::uuid", trip),
        "trip_events": await su.fetchval("SELECT array_agg(DISTINCT tenant_id::text) AS t "
                                         "FROM tripplanner.trip_events WHERE trip_id = $1::uuid", trip),
    }


async def test_runtime_role_is_subject_to_row_security(db, tp_conn):
    """Guard for the rest of the module: the login is `tripplanner`, not an owner or a BYPASSRLS role."""
    row = await tp_conn.fetchrow("SELECT current_user AS who, r.rolbypassrls AS bypass, r.rolsuper AS su, "
                                 "pg_has_role(current_user, 'tp_owner', 'USAGE') AS owner "
                                 "FROM pg_roles r WHERE r.rolname = current_user")
    assert (row["who"], row["bypass"], row["su"], row["owner"]) == ("tripplanner", False, False, False)


async def test_a_trip_of_the_demo_tenant_is_404_under_a_forged_tenant(db, tp_conn, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_TENANT_ID", DEMO)
    session, trip, token = await _create_trip(tp_conn)

    status, body = await _serve(tp_conn, "GET", f"/trip/{trip}", token=token)
    assert status == 200 and body["trip_id"] == trip
    assert [d["kind"] for d in body["custom_days"]] == ["free_day"]

    monkeypatch.setattr(config, "DEFAULT_TENANT_ID", FORGED)
    status, body = await _serve(tp_conn, "GET", f"/trip/{trip}", token=token)
    unknown_status, unknown = await _serve(tp_conn, "GET", f"/trip/{uuid.uuid4()}", token=token)
    assert (status, body) == (unknown_status, unknown) and status == 404  # existence does not leak

    monkeypatch.setattr(config, "DEFAULT_TENANT_ID", DEMO)
    status, _ = await _serve(tp_conn, "GET", f"/trip/{trip}", token=token)
    assert status == 200

    su = await asyncpg.connect(db["su_dsn"])
    try:
        assert await _tenants(su, session, trip) == {"sessions": [DEMO], "trip_drafts": [DEMO], "trip_events": [DEMO]}
    finally:
        await su.close()


async def test_a_mutation_under_a_forged_tenant_reaches_no_session_and_writes_nothing(db, tp_conn, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_TENANT_ID", DEMO)
    session, trip, token = await _create_trip(tp_conn)
    su = await asyncpg.connect(db["su_dsn"])
    try:
        before = await su.fetchval("SELECT count(*) AS n FROM tripplanner.trip_events WHERE trip_id = $1::uuid", trip)
        monkeypatch.setattr(config, "DEFAULT_TENANT_ID", FORGED)
        status, body = await _serve(tp_conn, "POST", f"/trip/{trip}/days",
                                    {"session_id": session, "kind": "free_day"}, token)
        assert (status, body) == (401, {"error": "guest_session_invalid"})
        after = await su.fetchval("SELECT count(*) AS n FROM tripplanner.trip_events WHERE trip_id = $1::uuid", trip)
        forged_rows = await su.fetchval(
            "SELECT (SELECT count(*) FROM tripplanner.sessions WHERE tenant_id = $1::uuid) "
            "+ (SELECT count(*) FROM tripplanner.trip_drafts WHERE tenant_id = $1::uuid) "
            "+ (SELECT count(*) FROM tripplanner.trip_events WHERE tenant_id = $1::uuid) AS n", FORGED)
        assert (before, after, forged_rows) == (1, 1, 0)
        assert await _tenants(su, session, trip) == {"sessions": [DEMO], "trip_drafts": [DEMO], "trip_events": [DEMO]}
    finally:
        await su.close()


async def test_a_trip_written_before_tp025_still_loads_with_its_guest_token(db, tp_conn, monkeypatch):
    """The backfilled rows belong to the demo tenant, so a guest's old trip keeps working after the shadow deploy."""
    monkeypatch.setattr(config, "DEFAULT_TENANT_ID", DEMO)
    token = _token()
    su = await asyncpg.connect(db["su_dsn"])
    try:
        # the seeded trip with no events (its stops would need the CIS catalog views, absent from this replay)
        row = await su.fetchrow(
            "SELECT d.id::text AS trip, d.session_id::text AS session, d.tenant_id::text AS tenant "
            "FROM tripplanner.trip_drafts d WHERE NOT EXISTS "
            "(SELECT 1 FROM tripplanner.trip_events e WHERE e.trip_id = d.id) AND d.session_id <> $1::uuid "
            "ORDER BY d.created_at LIMIT 1", db["session"])
        assert row is not None and row["tenant"] == DEMO
        await su.execute("UPDATE tripplanner.sessions SET guest_token_hash = $1 WHERE id = $2::uuid",
                         hashlib.sha256(token.encode()).digest(), row["session"])
    finally:
        await su.close()
    status, body = await _serve(tp_conn, "GET", f"/trip/{row['trip']}", token=token)
    assert status == 200 and body["trip_id"] == row["trip"]


async def test_the_connection_has_no_tenant_context_after_a_request(db, tp_conn, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_TENANT_ID", DEMO)
    await _create_trip(tp_conn)
    assert await _guc(tp_conn) in (None, "")
    status, _ = await _serve(tp_conn, "GET", f"/trip/{uuid.uuid4()}", token=_token())
    assert status == 404
    assert await _guc(tp_conn) in (None, "")
    # no context = the lenient branch of the shadow policy: the runtime role sees every row again
    total = await tp_conn.fetchval("SELECT count(*) AS n FROM tripplanner.sessions")
    async with tenant.bound(tp_conn, FORGED):
        assert await tp_conn.fetchval("SELECT count(*) AS n FROM tripplanner.sessions") == 0
    assert await tp_conn.fetchval("SELECT count(*) AS n FROM tripplanner.sessions") == total > 0
