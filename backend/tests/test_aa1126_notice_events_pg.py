"""AA-1126 integration — the agency notice and the degraded-check bell as tp-034 outbox events, on PostgreSQL, as the
runtime role ``tripplanner`` with a tenant bound (the assembly Lambda's connection).

Runs only with ``pytest --pg-dsn postgresql://postgres@127.0.0.1:<port>/postgres`` (a SUPERUSER DSN of a throwaway
LOCAL Postgres). The database is tp-033's replay (``migrations/tests/test_tp033_pg._database``) plus tp-034 as
tp_owner.

Checks: ``tenant_sessions.notify_agency`` and ``spend_bell.degraded`` write their events once (a second container's
write is a no-op through ``ON CONFLICT``), under the tenant policy of tp-028; ``cis_app`` reads them through
``tripplanner.v_ai_notices_v1`` with the payload CIS turns into words. Invented ids only.
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import pytest

asyncpg = pytest.importorskip("asyncpg")

MIGRATION_TESTS = Path(__file__).resolve().parents[2] / "migrations" / "tests"
if str(MIGRATION_TESTS) not in sys.path:
    sys.path.insert(0, str(MIGRATION_TESTS))

import test_tp025_pg as replay  # noqa: E402
from test_tp013_pg import MIGRATIONS  # noqa: E402
from test_tp030_pg import OTHER, TARGET  # noqa: E402
from test_tp031_pg import _owner_file  # noqa: E402
from test_tp033_pg import _database  # noqa: E402

from backend.shared import spend_bell  # noqa: E402
from backend.shared import tenant_sessions as ts  # noqa: E402

TP034 = MIGRATIONS / "034_ai_notice_events.sql"
NOV = datetime(2026, 11, 18, 9, 0, tzinfo=timezone.utc)
BIND = "SELECT set_config('app.tenant_id', $1, false)"


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
    ctx, error = _database(pg_dsn)
    try:
        assert error is None, error

        async def apply():
            admin = await asyncpg.connect(ctx["admin_dsn"])
            try:
                await _owner_file(admin, TP034)
            finally:
                await admin.close()

        asyncio.run(apply())
        yield ctx
    finally:
        replay._drop(pg_dsn, ctx["name"])


@pytest.fixture(autouse=True)
def _fresh():
    ts.clear()
    spend_bell.clear()
    yield
    ts.clear()
    spend_bell.clear()


async def _bound(db, tenant):
    tp = await asyncpg.connect(db["tp_dsn"])
    await tp.execute(BIND, tenant)
    return tp


async def _as_cis(db):
    su = await asyncpg.connect(db["su_dsn"])
    try:
        async with su.transaction():
            await su.execute("SET LOCAL ROLE cis_app")
            rows = await su.fetch("SELECT tenant_id::text AS tenant_id, event, notice_key, payload::text AS payload "
                                  "FROM tripplanner.v_ai_notices_v1 ORDER BY id")
        return [(r["tenant_id"], r["event"], r["notice_key"], json.loads(r["payload"])) for r in rows]
    finally:
        await su.close()


async def _cleanup(db):
    su = await asyncpg.connect(db["su_dsn"])
    try:
        await su.execute("DELETE FROM tripplanner.outbox WHERE event LIKE 'ai.%'")
    finally:
        await su.close()


def test_the_agency_notice_is_written_once_and_cis_reads_it(db):
    async def scenario():
        tp = await _bound(db, TARGET)
        try:
            first = await ts.notify_agency(tp, TARGET, NOV)
            ts.clear()                                                # another container: the outbox key holds
            second = await ts.notify_agency(tp, TARGET, NOV)
        finally:
            await tp.close()
        try:
            return first, second, await _as_cis(db)
        finally:
            await _cleanup(db)

    first, second, rows = asyncio.run(scenario())
    assert (first, second) == (True, False)
    assert rows == [(TARGET, "ai.allowance_used", f"ai.allowance_used:{TARGET}:2026-11",
                     {"kind": "tripplanner", "month": "2026-11", "resets_at": "2026-12-01"})]


def test_the_degraded_bell_is_one_event_per_check_and_day_across_tenants(db):
    async def scenario():
        a = await _bound(db, TARGET)
        b = await _bound(db, OTHER)
        try:
            first = await spend_bell.degraded(a, "spend_unreadable", stage="tp_compose", tenant_id=TARGET, now=NOV)
            spend_bell.clear()
            other = await spend_bell.degraded(b, "spend_unreadable", stage="tp_compose", tenant_id=OTHER, now=NOV)
            sessions = await spend_bell.degraded(b, "tenant_sessions", tenant_id=OTHER, now=NOV)
        finally:
            await a.close()
            await b.close()
        try:
            return first, other, sessions, await _as_cis(db)
        finally:
            await _cleanup(db)

    first, other, sessions, rows = asyncio.run(scenario())
    assert (first, other, sessions) == (True, False, True)
    assert rows == [
        (TARGET, "ai.spend_degraded", "ai.spend_degraded:spend_unreadable:2026-11-18",
         {"check": "spend_unreadable", "stage": "tp_compose", "day": "2026-11-18"}),
        (OTHER, "ai.spend_degraded", "ai.spend_degraded:tenant_sessions:2026-11-18",
         {"check": "tenant_sessions", "stage": None, "day": "2026-11-18"})]


def test_a_write_for_another_tenant_than_the_bound_one_is_refused_and_never_raises(db):
    async def scenario():
        tp = await _bound(db, TARGET)
        try:
            written = await ts.notify_agency(tp, OTHER, NOV)              # tp-028 outbox_tenant WITH CHECK
            usable = await tp.fetchval("SELECT 1 AS ok")                  # the savepoint kept the connection usable
        finally:
            await tp.close()
        return written, usable, await _as_cis(db)

    assert asyncio.run(scenario()) == (False, 1, [])
