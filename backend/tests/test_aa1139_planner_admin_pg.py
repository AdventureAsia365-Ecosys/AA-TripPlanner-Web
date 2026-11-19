"""AA-1139 integration — the agency app's TripPlanner module routes on the tp-031 replay plus tp-032 and tp-035, as the
runtime role with FORCE row-level security (tp-028), through ``handler.serve_m2m`` (M2M verify faked).

Runs only with ``pytest --pg-dsn postgresql://postgres@127.0.0.1:<port>/postgres`` (a SUPERUSER DSN of a throwaway
LOCAL Postgres). A stand-in for the CIS view ``catalog.v_tenant_tripplanner_v1`` (the two columns read here) is granted
to ``tripplanner`` as CIS grants it.

Two synthetic agencies, A and B, each with leads in the outbox (written as the superuser). Checks: A's list shows A's
leads only, newest first, with their statuses and no traveller field; the month counts; usage counts A's AI sessions
of this month against its quota; a rotation writes version 1, then 2, the key info names the version and never the
key; the delivery reads the version a rotation wrote; an agency CIS does not list is 404. Invented names only.
"""
from __future__ import annotations

import asyncio
import json
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

import test_tp025_pg as replay  # noqa: E402
from test_tp013_pg import MIGRATIONS  # noqa: E402
from test_tp031_pg import erasure_database  # noqa: E402

from backend import config  # noqa: E402
from backend.assembly import handler, leads  # noqa: E402
from backend.shared import m2m_auth  # noqa: E402

TP032 = MIGRATIONS / "032_ai_session_month.sql"
TP035 = MIGRATIONS / "035_lead_webhook_key.sql"
AGENCY_A = "6d7e8f90-0000-4000-8000-0000000011a1"
AGENCY_B = "6d7e8f90-0000-4000-8000-0000000011b2"
UNKNOWN = "6d7e8f90-0000-4000-8000-0000000011c3"
MEMBER = "crm-member:6d7e8f90-0000-4000-8000-0000000000aa"
STANDIN = f"""
CREATE SCHEMA IF NOT EXISTS catalog;
CREATE TABLE catalog.zz_tp_rows (tenant_id uuid, ai_sessions_monthly integer);
INSERT INTO catalog.zz_tp_rows VALUES ('{AGENCY_A}', 300), ('{AGENCY_B}', NULL);
CREATE VIEW catalog.v_tenant_tripplanner_v1 WITH (security_barrier = true) AS SELECT * FROM catalog.zz_tp_rows;
GRANT USAGE ON SCHEMA catalog TO tripplanner;
GRANT SELECT ON catalog.v_tenant_tripplanner_v1 TO tripplanner;
"""
OUTBOX = ("INSERT INTO tripplanner.outbox (event, schema_version, idempotency_key, aggregate_id, payload, tenant_id, "
          "delivery_channel, delivered_at, redacted_at, delivery_attempts, delivery_error, created_at) "
          "VALUES ('lead.submitted', 1, $1, $2::uuid, $3::jsonb, $4::uuid, $5, $6, $7, $8, $9, now() - make_interval(mins => $10))")


@pytest.fixture(scope="module")
def pg_dsn(request):
    dsn = request.config.getoption("--pg-dsn")
    if not dsn:
        pytest.skip("integration: pass --pg-dsn with a superuser DSN of a throwaway local Postgres")
    host = urlparse(dsn).hostname or "localhost"
    if host not in ("localhost", "127.0.0.1", "::1"):
        pytest.skip(f"integration tests only run against a local Postgres, not {host}")
    return dsn


def _lead_id(n: int) -> str:
    return f"6d7e8f90-0000-4000-8000-{n:012d}"


@pytest.fixture(scope="module")
def db(pg_dsn):
    ctx, error = erasure_database(pg_dsn, seed=False)
    try:
        assert error is None, error

        async def apply():
            admin = await asyncpg.connect(ctx["admin_dsn"])
            try:
                await replay._as_owner(admin, TP032)
                await replay._as_owner(admin, TP035)
            finally:
                await admin.close()
            su = await asyncpg.connect(ctx["su_dsn"])
            try:
                await su.execute(STANDIN)
                rows = [  # (n, tenant, channel, delivered, redacted, attempts, error, age)
                    (1, AGENCY_A, "webhook", True, True, 0, None, 180),
                    (2, AGENCY_A, "webhook", False, False, 1, "webhook_http_500", 120),
                    (3, AGENCY_A, "webhook", False, False, 0, None, 60),
                    (4, AGENCY_B, "email", False, False, 0, None, 30),
                ]
                for n, tid, channel, delivered, redacted, attempts, err, age in rows:
                    lid = _lead_id(n)
                    payload = {"lead_id": lid, "traveller": {"name": "Ada Fernwick", "email": "ada@example.invalid"}}
                    await su.execute(OUTBOX, f"tp-lead:{lid}", str(uuid.uuid4()), json.dumps(payload), tid, channel,
                                     None if not delivered else await su.fetchval("SELECT now() AS t"),
                                     None if not redacted else await su.fetchval("SELECT now() AS t"),
                                     attempts, err, age)
                for i in range(3):
                    await su.execute(
                        "INSERT INTO tripplanner.ai_session_month (scope, month, session_id) "
                        "VALUES ($1, date_trunc('month', now() AT TIME ZONE 'UTC')::date, $2::uuid)",
                        f"tenant:{AGENCY_A}", _lead_id(100 + i))
            finally:
                await su.close()

        asyncio.run(apply())
        yield ctx
    finally:
        replay._drop(pg_dsn, ctx["name"])


@pytest.fixture(autouse=True)
def _master(monkeypatch):
    monkeypatch.setattr(config, "TP_LEAD_WEBHOOK_KEY", "synthetic-master")


async def _call(conn, method, path, body=None):
    @asynccontextmanager
    async def acquire():
        yield conn

    async def verify(_auth, scope=None):
        assert scope == m2m_auth.SCOPE_LEADS_MANAGE
        return "crm-client"

    out = await handler.serve_m2m(method, path, body or {}, "Bearer synthetic", acquire=acquire, verify=verify)
    return out["statusCode"], json.loads(out["body"])


@pytest.mark.asyncio
async def test_lists_one_agency_leads_with_statuses_and_no_traveller_field(db):
    conn = await asyncpg.connect(db["tp_dsn"])
    try:
        status, body = await _call(conn, "GET", f"/trip/m2m/v1/tenants/{AGENCY_A}/planner/leads", {"limit": "10"})
    finally:
        await conn.close()
    assert status == 200
    assert [(x["lead_id"], x["status"]) for x in body["leads"]] == [
        (_lead_id(3), "pending"), (_lead_id(2), "retrying"), (_lead_id(1), "delivered")]
    assert body["month"]["total"] == 3 and body["month"]["delivered"] == 1
    assert "Ada" not in json.dumps(body) and "example.invalid" not in json.dumps(body)


@pytest.mark.asyncio
async def test_usage_counts_this_month_sessions_against_the_quota(db):
    conn = await asyncpg.connect(db["tp_dsn"])
    try:
        status_a, body_a = await _call(conn, "GET", f"/trip/m2m/v1/tenants/{AGENCY_A}/planner/usage")
        status_b, body_b = await _call(conn, "GET", f"/trip/m2m/v1/tenants/{AGENCY_B}/planner/usage")
        status_u, body_u = await _call(conn, "GET", f"/trip/m2m/v1/tenants/{UNKNOWN}/planner/usage")
    finally:
        await conn.close()
    assert (status_a, body_a["ai_sessions"], body_a["leads_24h"]["count"]) == (200, {"used": 3, "quota": 300}, 3)
    assert (status_b, body_b["ai_sessions"]) == (200, {"used": 0, "quota": None})
    assert (status_u, body_u) == (404, {"error": "unknown_tenant"})


@pytest.mark.asyncio
async def test_rotation_writes_the_next_version_and_the_delivery_signs_with_it(db):
    conn = await asyncpg.connect(db["tp_dsn"])
    try:
        status, info = await _call(conn, "GET", f"/trip/m2m/v1/tenants/{AGENCY_B}/planner/webhook-key")
        assert (status, info["version"]) == (200, 0)
        _, first = await _call(conn, "POST", f"/trip/m2m/v1/tenants/{AGENCY_B}/planner/webhook-key/rotate",
                               {"actor": MEMBER})
        _, second = await _call(conn, "POST", f"/trip/m2m/v1/tenants/{AGENCY_B}/planner/webhook-key/rotate",
                                {"actor": MEMBER})
        _, info = await _call(conn, "GET", f"/trip/m2m/v1/tenants/{AGENCY_B}/planner/webhook-key")
        assert await leads.webhook_key_version(conn, AGENCY_B) == 2
        assert await leads.webhook_key_version(conn, AGENCY_A) == 0
    finally:
        await conn.close()
    assert (first["version"], second["version"], info["version"]) == (1, 2, 2) and "key" not in info
    assert second["key"] == leads.webhook_key(b"synthetic-master", AGENCY_B, 2).decode() != first["key"]
