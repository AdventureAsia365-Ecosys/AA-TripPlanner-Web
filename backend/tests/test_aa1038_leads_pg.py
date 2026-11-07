"""AA-1038 R5 slice 3 integration — lead.submitted.v1 end to end on the tp-026 replay, as the runtime role.

Runs only with ``pytest --pg-dsn postgresql://postgres@127.0.0.1:<port>/postgres`` (a SUPERUSER DSN of a throwaway
LOCAL Postgres). The database is the tp-025 replay of ``migrations/tests/test_tp025_pg.py`` plus tp-026, with stand-ins
for the CIS contract views ``catalog.v_tenant_tripplanner_v1`` (CIS 379 + 390 columns, ``CIS_390_COLUMNS`` copied from
the CIS test) and ``catalog.v_tenant_ai_budget_v1``, granted to ``tripplanner`` as CIS grants them.

Two synthetic agencies: A routes leads to its CRM, B to a webhook; C has no route. Checks: A's send stores one pending
CRM lead with A's tenant and contract shape, and a double click answers the same hand-off; the CRM pull of A lists it,
the pull of B never does; B's ack of A's lead is a 404; A's ack redacts it (ids only) and a second ack is "already";
B's send is delivered to the (fake) webhook at once and redacted; C's send is refused and stores nothing; the bare path
keeps the demo answer; the daily run ends the hold of an old pending lead. Invented names only.
"""
from __future__ import annotations

import json
import secrets
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

import jsonschema
import pytest

asyncpg = pytest.importorskip("asyncpg")

MIGRATION_TESTS = Path(__file__).resolve().parents[2] / "migrations" / "tests"
if str(MIGRATION_TESTS) not in sys.path:
    sys.path.insert(0, str(MIGRATION_TESTS))

import test_tp025_pg as replay  # noqa: E402

from backend import config  # noqa: E402
from backend.assembly import handler, leads  # noqa: E402
from backend.jobs import lead_deliver  # noqa: E402
from backend.jobs.queue import Job  # noqa: E402
from backend.shared import tenant_assertion as ta  # noqa: E402

DEMO = config.DEMO_TENANT_ID
AGENCY_A = "4b1d7e57-0000-4000-8000-0000000001a1"
AGENCY_B = "4b1d7e57-0000-4000-8000-0000000001b2"
AGENCY_C = "4b1d7e57-0000-4000-8000-0000000001c3"
TP026 = replay.MIGRATIONS / "026_lead_outbox_delivery.sql"
SCHEMA = json.loads((Path(__file__).parent / "contracts" / "lead.submitted.v1.json").read_text())
# Copied verbatim from AA-CIS-App tests/integration/test_aa1038_migration_390.py (COLUMNS, BUDGET_COLUMNS).
CIS_390_COLUMNS = (
    "tenant_id uuid, slug text, name text, kind text, account_status text, in_service boolean, served boolean, "
    "tp_launcher boolean, tp_branding boolean, theme jsonb, locale text, crm_core boolean, lead_channel text, "
    "lead_webhook_url text, lead_email text"
)
CIS_390_BUDGET = ("tenant_id uuid, budget_source text, monthly_usd numeric, month_spent_usd numeric(14,6), "
                  "month_calls integer")
VIEW_STANDIN = f"""
CREATE SCHEMA IF NOT EXISTS catalog;
CREATE TABLE catalog.zz_tenant_rows ({CIS_390_COLUMNS});
INSERT INTO catalog.zz_tenant_rows VALUES
 ('{DEMO}', 'aa-tripplanner', 'Adventure Asia TripPlanner demo', 'demo', 'active', true, true, true, false, '{{}}',
  'en', false, 'none', NULL, NULL),
 ('{AGENCY_A}', 'quillmere-voyages', 'Quillmere Voyages', 'agency', 'active', true, true, true, false, '{{}}', 'en',
  true, 'crm', NULL, NULL),
 ('{AGENCY_B}', 'ottervale-journeys', 'Ottervale Journeys', 'agency', 'active', true, true, true, false, '{{}}', 'en',
  false, 'webhook', 'https://ottervale.example.invalid/hooks/leads', NULL),
 ('{AGENCY_C}', 'saltmarsh-routes', 'Saltmarsh Routes', 'agency', 'active', true, true, true, false, '{{}}', 'en',
  false, 'none', NULL, NULL);
CREATE VIEW catalog.v_tenant_tripplanner_v1 WITH (security_barrier = true) AS SELECT * FROM catalog.zz_tenant_rows;
CREATE TABLE catalog.zz_budget_rows ({CIS_390_BUDGET});
CREATE VIEW catalog.v_tenant_ai_budget_v1 WITH (security_barrier = true) AS SELECT * FROM catalog.zz_budget_rows;
GRANT USAGE ON SCHEMA catalog TO tripplanner;
GRANT SELECT ON catalog.v_tenant_tripplanner_v1, catalog.v_tenant_ai_budget_v1 TO tripplanner;
"""
EDGE = "-".join(["aa1038", "leads", "edge"])
FORM = {"traveller": {"name": "Ada Fernwick", "email": "ada@example.invalid"}, "party": {"adults": 2},
        "consent": {"contact": True}}


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
    ctx, error = replay._database(pg_dsn, extra_sql=VIEW_STANDIN)
    try:
        assert error is None, error

        async def apply():
            admin = await asyncpg.connect(ctx["admin_dsn"])
            try:
                await replay._as_owner(admin, TP026)
            finally:
                await admin.close()
        replay.run(apply())
        yield ctx
    finally:
        replay._drop(pg_dsn, ctx["name"])


@pytest.fixture
async def tp_conn(db):
    conn = await asyncpg.connect(db["tp_dsn"])
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    monkeypatch.setattr(config, "TRIPPLANNER_API_KEY", EDGE)
    monkeypatch.setattr(config, "DEFAULT_TENANT_ID", DEMO)
    monkeypatch.setattr(config, "ADVISOR_HANDOFF_DEMO", True)
    monkeypatch.setattr(config, "TP_LEAD_WEBHOOK_KEY", "synthetic-master")
    monkeypatch.setattr(config, "LEAD_CRM_PULL_ENABLED", True)


def _acquire(conn):
    @asynccontextmanager
    async def acquire():
        yield conn

    return acquire


def _header(tenant_id: str, slug: str) -> dict[str, str]:
    return {config.TENANT_ASSERTION_HEADER: ta.sign(ta.derive_key(EDGE), tenant_id, slug)}


async def _request(conn, method, path, body=None, token=None, headers=None):
    picked = handler.tenant_assertion_answer(headers or {})
    assert "response" not in picked
    out = await handler.serve(method, path, body or {}, guest_token=token, acquire=_acquire(conn), **picked["tenant"])
    return out["statusCode"], json.loads(out["body"])


async def _trip(conn, headers):
    session, trip, token = str(uuid.uuid4()), str(uuid.uuid4()), secrets.token_urlsafe(32)
    status, body = await _request(conn, "POST", f"/trip/{trip}/days", {"session_id": session, "kind": "free_day"},
                                  token, headers)
    assert status == 200, body
    return session, trip, token


async def _crm(conn, method, path, body=None):
    async def verify(_a):
        return "crm-client"
    out = await handler.serve_m2m(method, path, body or {}, "Bearer synthetic", acquire=_acquire(conn), verify=verify)
    return out["statusCode"], json.loads(out["body"])


async def _lead_rows(su_dsn):
    su = await asyncpg.connect(su_dsn)
    try:
        return [dict(r) for r in await su.fetch(
            "SELECT tenant_id::text AS tenant_id, delivery_channel, delivered_at, redacted_at, delivery_error, payload "
            "FROM tripplanner.outbox WHERE event = 'lead.submitted' ORDER BY id")]
    finally:
        await su.close()


async def test_crm_lead_of_one_agency_only(db, tp_conn):
    a = _header(AGENCY_A, "quillmere-voyages")
    session, trip, token = await _trip(tp_conn, a)
    before = len(await _lead_rows(db["su_dsn"]))
    # S272 review: the CRM has not listed this tenant yet (no beacon) -> refused, nothing stored.
    status, body = await _request(tp_conn, "POST", f"/trip/{trip}/send-to-advisor",
                                  {"session_id": session, "lead": FORM}, token, a)
    assert (status, body) == (503, {"error": "lead_channel_unavailable"})
    assert len(await _lead_rows(db["su_dsn"])) == before
    status, page = await _crm(tp_conn, "GET", f"/trip/m2m/v1/tenants/{AGENCY_A}/leads")  # the CRM's pull: beacon
    assert status == 200
    status, body = await _request(tp_conn, "POST", f"/trip/{trip}/send-to-advisor",
                                  {"session_id": session, "lead": FORM}, token, a)
    assert status == 200 and body["lead"]["channel"] == "crm", body
    lead_id = body["lead"]["lead_id"]
    again_status, again = await _request(tp_conn, "POST", f"/trip/{trip}/send-to-advisor",
                                         {"session_id": session, "lead": FORM}, token, a)
    assert again_status == 200 and again["already_sent"] is True

    rows = [r for r in await _lead_rows(db["su_dsn"]) if json.loads(r["payload"]).get("lead_id") == lead_id]
    assert len(rows) == 1 and rows[0]["tenant_id"] == AGENCY_A and rows[0]["delivery_channel"] == "crm"
    jsonschema.validate(json.loads(rows[0]["payload"]), SCHEMA)

    status, page = await _crm(tp_conn, "GET", f"/trip/m2m/v1/tenants/{AGENCY_A}/leads")
    assert status == 200 and lead_id in [p["lead_id"] for p in page["leads"]]
    # B's route is not crm: the CRM client (a platform credential) reaches nothing of B (S272 review hardening).
    status, page_b = await _crm(tp_conn, "GET", f"/trip/m2m/v1/tenants/{AGENCY_B}/leads")
    assert (status, page_b) == (404, {"error": "tenant_not_on_crm"})
    status, wrong = await _crm(tp_conn, "POST", f"/trip/m2m/v1/tenants/{AGENCY_B}/leads/{lead_id}/ack",
                               {"outcome": "stored"})
    assert (status, wrong) == (404, {"error": "tenant_not_on_crm"})
    # another tenant's lead id under the right tenant path is still 404 lead_not_found
    status, wrong = await _crm(tp_conn, "POST", f"/trip/m2m/v1/tenants/{AGENCY_A}/leads/{uuid.uuid4()}/ack",
                               {"outcome": "stored"})
    assert (status, wrong) == (404, {"error": "lead_not_found"})

    status, ack = await _crm(tp_conn, "POST", f"/trip/m2m/v1/tenants/{AGENCY_A}/leads/{lead_id}/ack",
                             {"outcome": "stored"})
    assert status == 200 and ack == {"lead_id": lead_id, "state": "delivered", "already": False}
    status, ack2 = await _crm(tp_conn, "POST", f"/trip/m2m/v1/tenants/{AGENCY_A}/leads/{lead_id}/ack",
                              {"outcome": "stored"})
    assert ack2["already"] is True
    row = [r for r in await _lead_rows(db["su_dsn"]) if json.loads(r["payload"]).get("lead_id") == lead_id][0]
    assert row["delivered_at"] is not None and row["redacted_at"] is not None
    stored = json.loads(row["payload"])
    assert stored["redacted"] is True and "traveller" not in stored and "trip" not in stored
    status, page = await _crm(tp_conn, "GET", f"/trip/m2m/v1/tenants/{AGENCY_A}/leads")
    assert lead_id not in [p["lead_id"] for p in page["leads"]]


async def test_webhook_lead_is_delivered_and_redacted_at_once(db, tp_conn, monkeypatch):
    sent = []

    def post(url, body, headers, timeout):
        sent.append((url, json.loads(body), headers))
        return 200

    monkeypatch.setattr(leads, "urllib_post", post)
    monkeypatch.setattr(leads, "resolve_host_bounded", lambda host: ["93.184.215.14"])  # a public address
    b = _header(AGENCY_B, "ottervale-journeys")
    session, trip, token = await _trip(tp_conn, b)
    status, body = await _request(tp_conn, "POST", f"/trip/{trip}/send-to-advisor",
                                  {"session_id": session, "lead": FORM}, token, b)
    assert status == 200 and body["lead"]["channel"] == "webhook"
    assert sent and sent[0][0] == "https://ottervale.example.invalid/hooks/leads"
    assert sent[0][1]["tenant"]["tenant_id"] == AGENCY_B and sent[0][2]["X-AA-Signature"].startswith("t=")
    row = [r for r in await _lead_rows(db["su_dsn"])
           if json.loads(r["payload"]).get("lead_id") == body["lead"]["lead_id"]][0]
    assert row["tenant_id"] == AGENCY_B and row["delivered_at"] is not None and row["redacted_at"] is not None


async def test_no_route_and_the_demo_store_nothing(db, tp_conn):
    before = len(await _lead_rows(db["su_dsn"]))
    c = _header(AGENCY_C, "saltmarsh-routes")
    session, trip, token = await _trip(tp_conn, c)
    status, body = await _request(tp_conn, "POST", f"/trip/{trip}/send-to-advisor",
                                  {"session_id": session, "lead": FORM}, token, c)
    assert (status, body) == (409, {"error": "lead_route_missing"})
    session, trip, token = await _trip(tp_conn, {})
    status, body = await _request(tp_conn, "POST", f"/trip/{trip}/send-to-advisor",
                                  {"session_id": session, "lead": FORM}, token, {})
    assert status == 410 and body["error"] == "b2b_only"
    a = _header(AGENCY_A, "quillmere-voyages")
    session, trip, token = await _trip(tp_conn, a)
    status, body = await _request(tp_conn, "POST", f"/trip/{trip}/send-to-advisor",
                                  {"session_id": session, "lead": {**FORM, "consent": {"contact": False}}}, token, a)
    assert (status, body) == (422, {"error": "consent_required"})
    assert len(await _lead_rows(db["su_dsn"])) == before


async def test_crm_pull_off_refuses_and_stores_nothing(db, tp_conn, monkeypatch):
    monkeypatch.setattr(config, "LEAD_CRM_PULL_ENABLED", False)
    before = len(await _lead_rows(db["su_dsn"]))
    a = _header(AGENCY_A, "quillmere-voyages")
    session, trip, token = await _trip(tp_conn, a)
    status, body = await _request(tp_conn, "POST", f"/trip/{trip}/send-to-advisor",
                                  {"session_id": session, "lead": FORM}, token, a)
    assert (status, body) == (503, {"error": "lead_channel_unavailable"})
    assert len(await _lead_rows(db["su_dsn"])) == before


async def test_the_daily_run_ends_the_hold(db, tp_conn):
    su = await asyncpg.connect(db["su_dsn"])
    try:
        lead_id = str(uuid.uuid4())
        await su.execute(
            "INSERT INTO tripplanner.outbox (event, schema_version, idempotency_key, aggregate_id, payload, "
            "delivery_channel, tenant_id, created_at) VALUES ('lead.submitted', 1, $1, $2, $3::jsonb, 'crm', $4, "
            "now() - interval '8 days')",
            f"tp-lead:{lead_id}", uuid.uuid4(),
            json.dumps({"lead_id": lead_id, "traveller": {"name": "Old Lead"}, "tenant": {"tenant_id": AGENCY_A}}),
            AGENCY_A)
    finally:
        await su.close()

    class Pool:
        def acquire(self):
            return _acquire(tp_conn)()

    dry = await lead_deliver.run(Pool(), Job(id="j1", kind=lead_deliver.KIND, payload={"dry_run": True},
                                             attempt=1, max_attempts=1))
    assert dry["mode"] == "dry_run" and dry["expire"]["candidates"] >= 1 and dry["expire"]["expired"] == 0
    live = await lead_deliver.run(Pool(), Job(id="j2", kind=lead_deliver.KIND, payload={"trigger": "schedule"},
                                              attempt=1, max_attempts=1))
    assert live["expire"]["expired"] >= 1 and live["expire"]["by_channel"].get("crm", 0) >= 1
    assert live["attention"] == ["lead_expired_undelivered"]
    row = [r for r in await _lead_rows(db["su_dsn"]) if json.loads(r["payload"]).get("lead_id") == lead_id][0]
    assert row["delivery_error"] == "expired" and row["redacted_at"] is not None
    assert "traveller" not in json.loads(row["payload"])
