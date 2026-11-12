"""AA-1038 R5 slice 2 integration — the /{slug} path end to end on the tp-028 replay (FORCE row-level security), as the
runtime role.

Runs only with ``pytest --pg-dsn postgresql://postgres@127.0.0.1:<port>/postgres`` (a SUPERUSER DSN of a throwaway
LOCAL Postgres; other hosts are refused). The database is the tp-025 replay of ``migrations/tests/test_tp025_pg.py``
then tp-026 … tp-028 (``test_tp028_pg.forced_database``), plus a stand-in for the CIS contract view ``catalog.v_tenant_tripplanner_v1`` (CIS 379) with the exact column list the
CIS test pins (``CIS_379_COLUMNS``), granted to ``tripplanner`` the way CIS 379 grants it.

Checks: the lookup SQL answers as ``tripplanner`` (served tenants only); two synthetic agencies each create a trip
through a signed assertion and the rows carry their tenant; under the other agency's assertion (or the bare path) the
trip is the same 404 as an unknown one and a mutation reaches no session (401) and writes nothing; a forged or expired
assertion is refused before any connection is used, so nothing is bound and nothing is written.
"""
from __future__ import annotations

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

import test_tp025_pg as replay  # noqa: E402
import test_tp028_pg as forced  # noqa: E402

from backend import config  # noqa: E402
from backend.assembly import handler  # noqa: E402
from backend.browse import catalog, tenant_site, tenant_tours  # noqa: E402
from backend.shared import tenant_assertion as ta  # noqa: E402

DEMO = config.DEMO_TENANT_ID
AGENCY_A = "4b1d7e57-0000-4000-8000-0000000000a1"
AGENCY_B = "4b1d7e57-0000-4000-8000-0000000000b2"
PAUSED = "4b1d7e57-0000-4000-8000-0000000000c3"
# Copied verbatim from AA-CIS-App tests/integration/test_aa1038_migration_379.py (COLUMNS).
CIS_379_COLUMNS = (
    "tenant_id uuid, slug text, name text, kind text, account_status text, in_service boolean, served boolean, "
    "tp_launcher boolean, tp_branding boolean, theme jsonb, locale text"
)
# The CIS view as a stand-in over a plain table (the CIS control plane is not in this replay).
VIEW_STANDIN = f"""
CREATE SCHEMA IF NOT EXISTS catalog;
CREATE TABLE catalog.zz_tenant_tripplanner_rows (
    tenant_id uuid, slug text, name text, kind text, account_status text, in_service boolean, served boolean,
    tp_launcher boolean, tp_branding boolean, theme jsonb, locale text);
INSERT INTO catalog.zz_tenant_tripplanner_rows VALUES
    ('{DEMO}', 'aa-tripplanner', 'Adventure Asia TripPlanner demo', 'demo', 'active', true, true, true, false, '{{}}', 'en'),
    ('{AGENCY_A}', 'quillmere-voyages', 'Quillmere Voyages', 'agency', 'active', true, true, true, false,
     '{{"accent": "#1a2b3c"}}', 'en'),
    ('{AGENCY_B}', 'ottervale-journeys', 'Ottervale Journeys', 'agency', 'active', true, true, false, true, '{{}}', 'vi'),
    ('{PAUSED}', 'saltmarsh-routes', 'Saltmarsh Routes', 'agency', 'suspended', false, false, true, false, '{{}}', 'en');
CREATE VIEW catalog.v_tenant_tripplanner_v1 WITH (security_barrier = true) AS
    SELECT * FROM catalog.zz_tenant_tripplanner_rows;
GRANT USAGE ON SCHEMA catalog TO tripplanner;
GRANT SELECT ON catalog.v_tenant_tripplanner_v1 TO tripplanner;
"""
# AA-1094: copied verbatim from AA-CIS-App tests/integration/test_aa1094_migration_404.py (COLUMNS).
CIS_404_COLUMNS = (
    "tenant_id uuid, source_system text, source_id text, tour_id uuid, slug text, title text, subtitle text, "
    "summary text, highlights jsonb, itinerary_text text, seo_title text, seo_meta text, country text, "
    "duration_raw text, duration_days integer, best_time_to_go text, cover_photo_path text, photo_count bigint, "
    "version_id uuid, edit_source text, text_updated_at timestamp with time zone, human_edited boolean, "
    "content_hash text"
)
TOUR_A1 = "7a000000-0000-4000-8000-0000000000a1"
TOUR_B1 = "7b000000-0000-4000-8000-0000000000b1"
# AA-1094: catalog.v_tenant_tour_v1 (CIS 404) as a stand-in over a plain table, granted the way CIS 404 grants it.
TOUR_VIEW_STANDIN = f"""
CREATE TABLE catalog.zz_tenant_tour_rows (
    tenant_id uuid, source_system text, source_id text, tour_id uuid, slug text, title text, subtitle text,
    summary text, highlights jsonb, itinerary_text text, seo_title text, seo_meta text, country text,
    duration_raw text, duration_days integer, best_time_to_go text, cover_photo_path text, photo_count bigint,
    version_id uuid, edit_source text, text_updated_at timestamptz, human_edited boolean, content_hash text);
INSERT INTO catalog.zz_tenant_tour_rows VALUES
    ('{AGENCY_A}', 'cis', 'a1a1a1a1-0000-4000-8000-000000000001', '{TOUR_A1}', 'invented-fern-valley-walk-a1a1a1a1',
     'Quillmere fern valley walk', NULL, 'Invented summary A', '["Invented ferns"]', 'Day 1 — Arrive', NULL, NULL,
     'Laos', '4 days', 4, 'November to March', '/photos/a1.jpg', 1, gen_random_uuid(), 'ai_generated', now(), false,
     'h1'),
    ('{AGENCY_B}', 'cis', 'b1b1b1b1-0000-4000-8000-000000000001', '{TOUR_B1}', 'invented-salt-flat-loop-b1b1b1b1',
     'Ottervale salt flat loop', NULL, 'Invented summary B', '[]', NULL, NULL, NULL,
     'Laos', '6 days', 6, NULL, '/photos/b1.jpg', 1, gen_random_uuid(), 'tenant_edit', now(), true, 'h2');
CREATE VIEW catalog.v_tenant_tour_v1 WITH (security_barrier = true) AS SELECT * FROM catalog.zz_tenant_tour_rows;
GRANT SELECT ON catalog.v_tenant_tour_v1 TO tripplanner;
"""
EDGE = "-".join(["aa1038", "pg", "edge"])


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
    ctx, error = forced.forced_database(pg_dsn, extra_sql=VIEW_STANDIN + TOUR_VIEW_STANDIN)
    try:
        assert error is None, error
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
def edge_key(monkeypatch):
    monkeypatch.setattr(config, "TRIPPLANNER_API_KEY", EDGE)
    monkeypatch.setattr(config, "DEFAULT_TENANT_ID", DEMO)


class _OneConnPool:
    def __init__(self, conn):
        self.conn = conn

    async def fetchrow(self, sql, *args):
        return await self.conn.fetchrow(sql, *args)

    async def fetch(self, sql, *args):
        return await self.conn.fetch(sql, *args)


def _acquire(conn, used: list):
    @asynccontextmanager
    async def acquire():
        used.append("acquire")
        yield conn

    return acquire


def _header(tenant_id: str, slug: str, **kw) -> dict[str, str]:
    return {config.TENANT_ASSERTION_HEADER: ta.sign(ta.derive_key(EDGE), tenant_id, slug, **kw)}


async def _request(conn, method, path, body=None, token=None, headers=None):
    """The Lambda adapter's order: the assertion first (no connection on a refusal), then serve()."""
    used: list = []
    picked = handler.tenant_assertion_answer(headers or {})
    if "response" in picked:
        out = picked["response"]
    else:
        out = await handler.serve(method, path, body or {}, guest_token=token, acquire=_acquire(conn, used),
                                  **picked["tenant"])
    return out["statusCode"], json.loads(out["body"]), used


async def _create_trip(conn, headers):
    session, trip, token = str(uuid.uuid4()), str(uuid.uuid4()), secrets.token_urlsafe(32)
    status, body, _ = await _request(conn, "POST", f"/trip/{trip}/days", {"session_id": session, "kind": "free_day"},
                                     token, headers)
    assert status == 200, body
    return session, trip, token


async def _stamped(db, session, trip) -> set[str]:
    su = await asyncpg.connect(db["su_dsn"])
    try:
        rows = await su.fetch(
            "SELECT tenant_id::text AS t FROM tripplanner.sessions WHERE id = $1::uuid "
            "UNION SELECT tenant_id::text FROM tripplanner.trip_drafts WHERE id = $2::uuid "
            "UNION SELECT tenant_id::text FROM tripplanner.trip_events WHERE trip_id = $2::uuid", session, trip)
        return {r["t"] for r in rows}
    finally:
        await su.close()


async def test_the_stand_in_has_the_cis_379_columns(db):
    su = await asyncpg.connect(db["su_dsn"])
    try:
        cols = await su.fetchval(
            "SELECT string_agg(a.attname || ' ' || format_type(a.atttypid, a.atttypmod), ', ' ORDER BY a.attnum) "
            "AS cols FROM pg_attribute a WHERE a.attrelid = 'catalog.v_tenant_tripplanner_v1'::regclass "
            "AND a.attnum > 0 AND NOT a.attisdropped")
    finally:
        await su.close()
    assert cols == CIS_379_COLUMNS


async def test_lookup_answers_served_tenants_only_as_the_runtime_role(db, tp_conn):
    pool = _OneConnPool(tp_conn)
    a = await tenant_site.lookup("quillmere-voyages", pool=pool)
    assert a["tenant_id"] == AGENCY_A and a["theme"] == {"accent": "#1a2b3c"} and a["launcher"] is True
    b = await tenant_site.lookup("ottervale-journeys", pool=pool)
    assert b["tenant_id"] == AGENCY_B and b["locale"] == "vi" and b["branding_badge"] is True
    assert (await tenant_site.lookup("aa-tripplanner", pool=pool))["tenant_id"] == DEMO
    assert await tenant_site.lookup("saltmarsh-routes", pool=pool) is None
    assert await tenant_site.lookup("no-such-agency", pool=pool) is None


async def test_lookup_real_errors_absent_view_is_404_a_broken_connection_is_unavailable(db, tp_conn, monkeypatch):
    # The SQLSTATE split of tenant_site.lookup against real asyncpg errors: a view that does not exist (before CIS 379,
    # 42P01) is "no tenant" (None -> 404); a connection that is gone is TenantLookupUnavailable (-> 503, not cached).
    monkeypatch.setattr(tenant_site, "TENANT_SQL", tenant_site.TENANT_SQL.replace(tenant_site.VIEW,
                                                                                  "catalog.zz_no_such_view"))
    assert await tenant_site.lookup("quillmere-voyages", pool=_OneConnPool(tp_conn)) is None
    monkeypatch.undo()
    gone = await asyncpg.connect(db["tp_dsn"])
    await gone.close()
    with pytest.raises(tenant_site.TenantLookupUnavailable):
        await tenant_site.lookup("quillmere-voyages", pool=_OneConnPool(gone))


async def test_two_agencies_are_isolated_through_their_assertions(db, tp_conn):
    a_head, b_head = _header(AGENCY_A, "quillmere-voyages"), _header(AGENCY_B, "ottervale-journeys")
    session_a, trip_a, token_a = await _create_trip(tp_conn, a_head)
    session_b, trip_b, token_b = await _create_trip(tp_conn, b_head)
    assert await _stamped(db, session_a, trip_a) == {AGENCY_A}
    assert await _stamped(db, session_b, trip_b) == {AGENCY_B}

    status, body, _ = await _request(tp_conn, "GET", f"/trip/{trip_a}", token=token_a, headers=a_head)
    assert status == 200 and body["trip_id"] == trip_a

    unknown = await _request(tp_conn, "GET", f"/trip/{uuid.uuid4()}", token=token_a, headers=b_head)
    for headers in (b_head, {}):                      # the other agency, and the bare path (demo tenant)
        got = await _request(tp_conn, "GET", f"/trip/{trip_a}", token=token_a, headers=headers)
        assert got[:2] == unknown[:2] and got[0] == 404   # existence does not leak

    su = await asyncpg.connect(db["su_dsn"])
    try:
        before = await su.fetchval("SELECT count(*) AS n FROM tripplanner.trip_events WHERE trip_id = $1::uuid",
                                   trip_a)
        status, body, _ = await _request(tp_conn, "POST", f"/trip/{trip_a}/days",
                                         {"session_id": session_a, "kind": "free_day"}, token_a, b_head)
        assert (status, body) == (401, {"error": "guest_session_invalid"})
        after = await su.fetchval("SELECT count(*) AS n FROM tripplanner.trip_events WHERE trip_id = $1::uuid",
                                  trip_a)
        assert before == after == 1
    finally:
        await su.close()
    assert await _stamped(db, session_a, trip_a) == {AGENCY_A}
    assert await tp_conn.fetchval("SELECT current_setting('app.tenant_id', true) AS guc") in (None, "")


@pytest.mark.parametrize("make", [
    lambda: {config.TENANT_ASSERTION_HEADER: ta.sign(ta.derive_key("someone-else"), AGENCY_A, "quillmere-voyages")},
    lambda: _header(AGENCY_A, "quillmere-voyages", now=1762400000),
    lambda: {config.TENANT_ASSERTION_HEADER: "v1.e30.c2lnbmF0dXJl"},
])
async def test_a_forged_or_expired_assertion_binds_nothing_and_writes_nothing(db, tp_conn, make):
    su = await asyncpg.connect(db["su_dsn"])
    try:
        before = await su.fetchval("SELECT count(*) AS n FROM tripplanner.sessions")
        status, body, used = await _request(tp_conn, "POST", f"/trip/{uuid.uuid4()}/days",
                                            {"session_id": str(uuid.uuid4()), "kind": "free_day"},
                                            secrets.token_urlsafe(32), make())
        assert (status, body, used) == (401, {"error": "tenant_assertion_invalid"}, [])
        assert await su.fetchval("SELECT count(*) AS n FROM tripplanner.sessions") == before
    finally:
        await su.close()


# --- AA-1094: the tenant's own tours (catalog.v_tenant_tour_v1, CIS 404) as the runtime role ----------------------

async def test_the_tour_stand_in_has_the_cis_404_columns(db):
    su = await asyncpg.connect(db["su_dsn"])
    try:
        cols = await su.fetchval(
            "SELECT string_agg(a.attname || ' ' || format_type(a.atttypid, a.atttypmod), ', ' ORDER BY a.attnum) "
            "AS cols FROM pg_attribute a WHERE a.attrelid = 'catalog.v_tenant_tour_v1'::regclass "
            "AND a.attnum > 0 AND NOT a.attisdropped")
    finally:
        await su.close()
    assert cols == CIS_404_COLUMNS


async def test_each_agency_lists_only_its_own_tours_as_the_runtime_role(db, tp_conn):
    pool = _OneConnPool(tp_conn)
    q = catalog.parse_list_query({})
    a = await tenant_tours.list_tours(await tenant_site.lookup("quillmere-voyages", pool=pool), q, pool=pool)
    assert a["source"] == "tenant" and [t["tour_id"] for t in a["tours"]] == [TOUR_A1]
    assert a["tours"][0]["title"] == "Quillmere fern valley walk"
    b = await tenant_tours.list_tours(await tenant_site.lookup("ottervale-journeys", pool=pool), q, pool=pool)
    assert [t["tour_id"] for t in b["tours"]] == [TOUR_B1] and b["tours"][0]["ai"]["human_edited"] is True
    laos = await tenant_tours.list_tours(await tenant_site.lookup("quillmere-voyages", pool=pool),
                                         catalog.parse_list_query({"country": "Laos", "max_days": "3"}), pool=pool)
    assert laos["tours"] == []


async def test_the_tour_page_sql_finds_the_tour_by_slug_and_suffix_and_not_another_agencys(db, tp_conn):
    pool = _OneConnPool(tp_conn)
    a_site = await tenant_site.lookup("quillmere-voyages", pool=pool)
    by_slug = await tp_conn.fetch(tenant_tours.BY_SLUG_SQL, a_site["tenant_id"], "invented-fern-valley-walk-a1a1a1a1",
                                  "a1a1a1a1")
    assert [str(r["tour_id"]) for r in by_slug] == [TOUR_A1]
    by_suffix = await tp_conn.fetch(tenant_tours.BY_SLUG_SQL, a_site["tenant_id"], "an-old-title-a1a1a1a1", "a1a1a1a1")
    assert [r["slug"] for r in by_suffix] == ["invented-fern-valley-walk-a1a1a1a1"]
    other = await tp_conn.fetch(tenant_tours.BY_SLUG_SQL, a_site["tenant_id"], "invented-salt-flat-loop-b1b1b1b1",
                                "b1b1b1b1")
    assert other == []
