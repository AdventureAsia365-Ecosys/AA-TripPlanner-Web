"""AA-1116 integration — the monthly AI sessions quota on PostgreSQL, as the runtime role ``tripplanner``.

Runs only with ``pytest --pg-dsn postgresql://postgres@127.0.0.1:<port>/postgres`` (a SUPERUSER DSN of a throwaway
LOCAL Postgres). The database is tp-031's replay (``migrations/tests/test_tp031_pg.erasure_database``, no seed rows)
plus tp-032 as tp_owner, with a stand-in for the CIS contract view ``catalog.v_tenant_tripplanner_v1`` carrying the
CIS 438 column ``ai_sessions_monthly`` (the other 429 columns are not read here), granted to ``tripplanner``.

Checks: a tenant with a quota of 3 gets 3 new sessions, the 4th is refused, a counted one continues; a tenant with no
limit is counted; two new sessions racing on two connections for the last place never both pass (the advisory lock);
another tenant's count is its own. Invented ids only.
"""
from __future__ import annotations

import asyncio
import sys
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

from backend.shared import tenant_sessions as ts  # noqa: E402

TP032 = MIGRATIONS / "032_ai_session_month.sql"
SMALL = "7a1c0de5-0000-4000-8000-0000000000e1"     # quota 3
OPEN = "7a1c0de5-0000-4000-8000-0000000000e2"      # no limit
RACE = "7a1c0de5-0000-4000-8000-0000000000e3"      # quota 1, two sessions at once
STANDIN = f"""
CREATE SCHEMA IF NOT EXISTS catalog;
CREATE TABLE catalog.zz_tp_quota (tenant_id uuid, ai_sessions_monthly integer);
INSERT INTO catalog.zz_tp_quota VALUES ('{SMALL}', 3), ('{OPEN}', NULL), ('{RACE}', 1);
CREATE VIEW catalog.v_tenant_tripplanner_v1 WITH (security_barrier = true) AS SELECT * FROM catalog.zz_tp_quota;
GRANT USAGE ON SCHEMA catalog TO tripplanner;
GRANT SELECT ON catalog.v_tenant_tripplanner_v1 TO tripplanner;
"""


def _session(n: int) -> str:
    return f"7a1c0de5-0000-4000-8000-{n:012d}"


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
    ctx, error = erasure_database(pg_dsn, seed=False)
    try:
        assert error is None, error

        async def apply():
            admin = await asyncpg.connect(ctx["admin_dsn"])
            try:
                await replay._as_owner(admin, TP032)
            finally:
                await admin.close()
            su = await asyncpg.connect(ctx["su_dsn"])
            try:
                await su.execute(STANDIN)
            finally:
                await su.close()

        asyncio.run(apply())
        yield ctx
    finally:
        replay._drop(pg_dsn, ctx["name"])


@pytest.fixture(autouse=True)
def _clean():
    ts.clear()
    yield
    ts.clear()


@pytest.mark.asyncio
async def test_quota_counts_new_sessions_and_lets_a_counted_one_continue(db):
    conn = await asyncpg.connect(db["tp_dsn"])
    try:
        assert [await ts.admit(conn, SMALL, _session(i)) for i in range(1, 4)] == [None, None, None]
        assert await ts.admit(conn, SMALL, _session(4)) == ts.REASON_CAP
        assert await ts.admit(conn, SMALL, _session(2)) is None                # counted this month: continues
        assert [await ts.admit(conn, OPEN, _session(i)) for i in range(1, 6)] == [None] * 5
        counts = {r["scope"]: r["n"] for r in await conn.fetch(
            "SELECT scope, count(*) AS n FROM tripplanner.ai_session_month GROUP BY scope")}
        assert counts == {f"tenant:{SMALL}": 3, f"tenant:{OPEN}": 5}
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_two_new_sessions_never_both_take_the_last_place(db):
    a, b = await asyncpg.connect(db["tp_dsn"]), await asyncpg.connect(db["tp_dsn"])
    try:
        out = await asyncio.gather(ts.admit(a, RACE, _session(11)), ts.admit(b, RACE, _session(12)))
        assert sorted(out, key=str) == sorted([None, ts.REASON_CAP], key=str)
        assert await a.fetchval("SELECT count(*) AS n FROM tripplanner.ai_session_month WHERE scope = $1",
                                f"tenant:{RACE}") == 1
    finally:
        await a.close()
        await b.close()
