"""AA-1038 R5 integration — the assembly and jobs Lambdas' tenant context against the real tp-028 policies (FORCE row-level
security; slice 1 ran this module on the tp-025 shadow policy).

Runs only with ``pytest --pg-dsn postgresql://postgres@127.0.0.1:<port>/postgres`` (a SUPERUSER DSN of a throwaway
LOCAL Postgres; other hosts are refused). The database is built by the migration replays
(``migrations/tests/test_tp028_pg.forced_database``: tp-001 … tp-024 as the live roles, invented rows in the four
tables, tp-025 … tp-028 as ``tp_owner``); the code then runs ``handler.serve`` and the job kinds as the logged-in
runtime role ``tripplanner``, so row security applies to it exactly as on the Lambdas.

Checks: a trip created under the demo tenant is stored with the demo tenant, answers 200 from GET /trip/{id} under the
demo tenant and the same 404 as an unknown trip under a forged tenant; a mutation under a forged tenant reaches no
session (401) and writes nothing; a trip written before tp-025 (backfilled) still loads with its guest token; the
connection carries no tenant context after the request (unbind, not only the pool's RESET ALL) and then sees no row;
the outbox redaction and the lead expiry of the jobs Lambda reach every tenant's rows through the system scope and
leave the connection without it.
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

import test_tp025_pg as replay  # noqa: E402  (the tp-025 replay helpers: _drop)
import test_tp028_pg as forced  # noqa: E402  (the tp-028 database: same one the migration test builds)

from backend import config  # noqa: E402
from backend.assembly import handler  # noqa: E402
from backend.jobs import lead_deliver, outbox_redact  # noqa: E402
from backend.jobs.queue import Job  # noqa: E402
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
    """tp-024 + seeded rows + tp-025 … tp-028 (sync: the replay helpers run their own event loop)."""
    ctx, error = forced.forced_database(pg_dsn)
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
    # tp-028: no context = no row (the shadow policy's unset branch is gone); the demo context sees the demo rows
    assert await tp_conn.fetchval("SELECT count(*) AS n FROM tripplanner.sessions") == 0
    async with tenant.bound(tp_conn, FORGED):
        assert await tp_conn.fetchval("SELECT count(*) AS n FROM tripplanner.sessions") == 0
    async with tenant.bound(tp_conn, DEMO):
        assert await tp_conn.fetchval("SELECT count(*) AS n FROM tripplanner.sessions") > 0
    assert await tp_conn.fetchval("SELECT count(*) AS n FROM tripplanner.sessions") == 0


class _Pool:
    def __init__(self, conn):
        self.acquire = _acquire(conn)


async def _scope(conn) -> str | None:
    return await conn.fetchval("SELECT current_setting('app.tenant_scope', true) AS scope")


_INQUIRY = {"contract": "inquiry.submitted", "version": 1.4, "idempotency_key": "k", "source": "tripplanner",
            "customer": {"customer_id": None, "name": "Synthetic Traveller", "email": "synthetic@example.test",
                         "phone": "+1 555 0100", "consent": True, "consent_at": None, "verified": False,
                         "identity": None},
            "itinerary": {"legs": [], "days": [], "custom_days": []}}


async def test_the_outbox_redaction_reaches_every_tenant_through_the_system_scope(db, tp_conn):
    """The jobs Lambda binds no tenant: without the system scope it would see no outbox row under FORCE."""
    su = await asyncpg.connect(db["su_dsn"])
    try:
        ids = []
        for tid in (DEMO, FORGED):
            trip = uuid.uuid4()
            ids.append(await su.fetchval(
                "INSERT INTO tripplanner.outbox (event, schema_version, idempotency_key, aggregate_id, payload, "
                "tenant_id, created_at) VALUES ('inquiry.submitted', 1, $1, $2, $3::jsonb, $4::uuid, "
                "now() - interval '120 days') RETURNING id", f"{trip}:aa1038", trip, json.dumps(_INQUIRY), tid))
        assert await tp_conn.fetchval("SELECT count(*) AS n FROM tripplanner.outbox") == 0   # no context, no row
        res = await outbox_redact.run(_Pool(tp_conn), Job(id="j1", kind=outbox_redact.KIND,
                                                          payload={"dry_run": False, "max_rows": 100, "pause_ms": 0}),
                                      environ={})
        assert res["mode"] == "live" and res["affected_total"] >= 2, res
        rows = await su.fetch("SELECT tenant_id::text AS t, redacted_at, payload -> 'customer' ->> 'name' AS name "
                              "FROM tripplanner.outbox WHERE id = ANY($1::bigint[]) ORDER BY id", ids)
        assert [(r["t"], r["redacted_at"] is not None, r["name"]) for r in rows] == [(DEMO, True, None),
                                                                                       (FORGED, True, None)]
    finally:
        await su.close()
    assert await _scope(tp_conn) in (None, "")
    assert await tp_conn.fetchval("SELECT count(*) AS n FROM tripplanner.outbox") == 0


async def test_the_lead_expiry_reaches_every_tenant_and_resets_the_scope(db, tp_conn):
    su = await asyncpg.connect(db["su_dsn"])
    try:
        ids = []
        for tid in (DEMO, FORGED):
            trip = uuid.uuid4()
            ids.append(await su.fetchval(
                "INSERT INTO tripplanner.outbox (event, schema_version, idempotency_key, aggregate_id, payload, "
                "tenant_id, delivery_channel, created_at) VALUES ('lead.submitted', 1, $1, $2, $3::jsonb, $4::uuid, "
                "'crm', now() - interval '6 days') RETURNING id",
                f"{trip}:lead", trip, json.dumps({"lead_id": str(uuid.uuid4()), "traveller": {"name": "Synthetic"}}),
                tid))
        res = await lead_deliver.run(_Pool(tp_conn), Job(id="j2", kind=lead_deliver.KIND,
                                                         payload={"trigger": "schedule"}), deliver=None)
        assert res["expire"]["expired"] >= 2 and res["expire"]["left"] == 0, res
        rows = await su.fetch("SELECT tenant_id::text AS t, delivery_error FROM tripplanner.outbox "
                              "WHERE id = ANY($1::bigint[]) ORDER BY id", ids)
        assert [(r["t"], r["delivery_error"]) for r in rows] == [(DEMO, "expired"), (FORGED, "expired")]
    finally:
        await su.close()
    assert await _scope(tp_conn) in (None, "")


async def test_a_job_that_raises_still_leaves_the_connection_without_the_scope(db, tp_conn):
    with pytest.raises(RuntimeError):
        async with tenant.system_scope(tp_conn, op="test"):
            assert await tp_conn.fetchval("SELECT count(*) AS n FROM tripplanner.sessions") > 0
            raise RuntimeError("synthetic")
    assert await _scope(tp_conn) in (None, "")
    assert await tp_conn.fetchval("SELECT count(*) AS n FROM tripplanner.sessions") == 0
