"""AA-1090 TripPlanner R8 integration — an agency's own domain resolves to its tenant on the tp-028 replay, as the
runtime role.

Runs only with ``pytest --pg-dsn postgresql://postgres@127.0.0.1:<port>/postgres`` (a SUPERUSER DSN of a throwaway
LOCAL Postgres). The database is the tp-028 replay (``test_tp028_pg.forced_database``) plus a stand-in for the CIS
contract view ``catalog.v_tenant_tripplanner_v1`` with the CIS 429 column list (``CIS_429_COLUMNS``, copied from
AA-CIS-App tests/integration/test_aa1090_migrations_427_428.py), granted to ``tripplanner`` the way CIS grants it.

Checks: the stand-in has the CIS 429 columns; ``lookup_host`` (HOST_SQL) answers a served tenant's active host as
``tripplanner``, nothing for an unknown host or a suspended tenant; the same tenant resolves by slug and by host; a
view without ``custom_domains`` (a deploy before CIS 429) is a 404, not a 503. Invented hosts only.
"""
from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlparse

import pytest

asyncpg = pytest.importorskip("asyncpg")

MIGRATION_TESTS = Path(__file__).resolve().parents[2] / "migrations" / "tests"
if str(MIGRATION_TESTS) not in sys.path:
    sys.path.insert(0, str(MIGRATION_TESTS))

import test_tp025_pg as replay  # noqa: E402
import test_tp028_pg as forced  # noqa: E402

from backend.browse import tenant_site  # noqa: E402

AGENCY_A = "4b1d7e57-0000-4000-8000-0000000000a1"
PAUSED = "4b1d7e57-0000-4000-8000-0000000000c3"
# Copied verbatim from AA-CIS-App tests/integration/test_aa1090_migrations_427_428.py (COLUMNS = 390's + custom_domains).
CIS_429_COLUMNS = (
    "tenant_id uuid, slug text, name text, kind text, account_status text, in_service boolean, served boolean, "
    "tp_launcher boolean, tp_branding boolean, theme jsonb, locale text, crm_core boolean, lead_channel text, "
    "lead_webhook_url text, lead_email text, custom_domains text[]"
)
VIEW_STANDIN = f"""
CREATE SCHEMA IF NOT EXISTS catalog;
CREATE TABLE catalog.zz_tenant_host_rows (
    tenant_id uuid, slug text, name text, kind text, account_status text, in_service boolean, served boolean,
    tp_launcher boolean, tp_branding boolean, theme jsonb, locale text, crm_core boolean, lead_channel text,
    lead_webhook_url text, lead_email text, custom_domains text[]);
INSERT INTO catalog.zz_tenant_host_rows VALUES
    ('{AGENCY_A}', 'quillmere-voyages', 'Quillmere Voyages', 'agency', 'active', true, true, true, false,
     '{{"accent": "#1a2b3c"}}', 'en', false, 'none', NULL, NULL,
     ARRAY['go.quillmere.example.invalid', 'trip.quillmere.example.invalid']),
    ('{PAUSED}', 'saltmarsh-routes', 'Saltmarsh Routes', 'agency', 'suspended', false, false, true, false, '{{}}', 'en',
     false, 'none', NULL, NULL, ARRAY['trip.saltmarsh.example.invalid']);
CREATE VIEW catalog.v_tenant_tripplanner_v1 WITH (security_barrier = true) AS
    SELECT * FROM catalog.zz_tenant_host_rows;
GRANT USAGE ON SCHEMA catalog TO tripplanner;
GRANT SELECT ON catalog.v_tenant_tripplanner_v1 TO tripplanner;
"""


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
    ctx, error = forced.forced_database(pg_dsn, extra_sql=VIEW_STANDIN)
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


class _OneConnPool:
    def __init__(self, conn):
        self.conn = conn

    async def fetchrow(self, sql, *args):
        return await self.conn.fetchrow(sql, *args)


async def test_the_stand_in_has_the_cis_428_columns(db):
    su = await asyncpg.connect(db["su_dsn"])
    try:
        cols = await su.fetchval(
            "SELECT string_agg(a.attname || ' ' || format_type(a.atttypid, a.atttypmod), ', ' ORDER BY a.attnum) "
            "AS cols FROM pg_attribute a WHERE a.attrelid = 'catalog.v_tenant_tripplanner_v1'::regclass "
            "AND a.attnum > 0 AND NOT a.attisdropped")
    finally:
        await su.close()
    assert cols == CIS_429_COLUMNS


async def test_a_host_resolves_to_the_same_tenant_as_its_slug_as_the_runtime_role(db, tp_conn):
    pool = _OneConnPool(tp_conn)
    by_host = await tenant_site.lookup_host("trip.quillmere.example.invalid", pool=pool)
    assert by_host["tenant_id"] == AGENCY_A and by_host["theme"] == {"accent": "#1a2b3c"}
    assert await tenant_site.lookup_host("GO.Quillmere.Example.Invalid.", pool=pool) == by_host
    assert await tenant_site.lookup("quillmere-voyages", pool=pool) == by_host
    assert await tenant_site.lookup_host("trip.saltmarsh.example.invalid", pool=pool) is None   # suspended
    assert await tenant_site.lookup_host("unknown.example.invalid", pool=pool) is None


async def test_a_view_without_the_column_is_a_404_not_a_503(db, tp_conn):
    su = await asyncpg.connect(db["su_dsn"])
    try:
        await su.execute("CREATE VIEW catalog.zz_pre_428 AS SELECT tenant_id, slug FROM catalog.zz_tenant_host_rows")
        await su.execute("GRANT SELECT ON catalog.zz_pre_428 TO tripplanner")
    finally:
        await su.close()

    class Pre428Pool(_OneConnPool):
        async def fetchrow(self, sql, *args):
            return await self.conn.fetchrow(sql.replace(tenant_site.VIEW, "catalog.zz_pre_428"), *args)

    assert await tenant_site.lookup_host("trip.quillmere.example.invalid", pool=Pre428Pool(tp_conn)) is None
