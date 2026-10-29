"""S257 (AA-995 follow-up, C4.3 W44) — the catalog.v_tour_place reads' transaction-local settings, on a real Postgres.

Runs only with ``pytest --pg-dsn postgresql://postgres@127.0.0.1:<port>/postgres`` against a throwaway LOCAL Postgres
(non-local hosts are refused); the test creates and drops its own ``tp_s257_*`` database. catalog.v_tour_place is
stood in for by a TABLE with the columns the readers select (the view's logic is CIS's).
"""
from __future__ import annotations

import uuid
from urllib.parse import urlparse, urlunparse

import pytest

asyncpg = pytest.importorskip("asyncpg")

from backend.browse import catalog as cat  # noqa: E402

TOUR = "3f2b1c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
DEST = "4a3b2c1d-6e5f-4b7a-9c8d-1e0f2a3b4c5d"

SCHEMA = """
CREATE SCHEMA catalog;
CREATE TABLE catalog.v_tour_place (
    tour_id uuid, destination_id uuid, destination_name text, day_number smallint, lat double precision,
    lng double precision, activity_types text[], located_by text, precision text
);
"""


@pytest.fixture(scope="module")
def pg_dsn(request):
    dsn = request.config.getoption("--pg-dsn")
    if not dsn:
        pytest.skip("integration: pass --pg-dsn for a throwaway local Postgres")
    host = urlparse(dsn).hostname or "localhost"
    if host not in ("localhost", "127.0.0.1", "::1"):
        pytest.skip(f"integration tests only run against a local Postgres, not {host}")
    return dsn


class _SeenConn:
    """The connection, with fetch() recording the session's jit / plan_cache_mode just before each read."""

    def __init__(self, conn, seen):
        self._c, self._seen = conn, seen

    def __getattr__(self, name):
        return getattr(self._c, name)

    async def fetch(self, query, *args):
        self._seen.append((await self._c.fetchval("SHOW jit"), await self._c.fetchval("SHOW plan_cache_mode")))
        return await self._c.fetch(query, *args)


class _Pool:
    def __init__(self, conn, seen):
        self.c, self.seen = conn, seen

    def acquire(self):
        proxy = _SeenConn(self.c, self.seen)

        class _Ctx:
            async def __aenter__(self):
                return proxy

            async def __aexit__(self, *a):
                return False
        return _Ctx()


@pytest.fixture
async def conn(pg_dsn):
    name = f"tp_s257_{uuid.uuid4().hex[:8]}"
    admin = await asyncpg.connect(pg_dsn)
    await admin.execute(f'CREATE DATABASE "{name}"')
    c = await asyncpg.connect(urlunparse(urlparse(pg_dsn)._replace(path=f"/{name}")))
    try:
        await c.execute(SCHEMA)
        await c.execute("INSERT INTO catalog.v_tour_place VALUES ($1, $2, 'Hanoi', 1, 21.03, 105.85, '{food}', "
                        "'geocoder', 'point'), ($1, NULL, NULL, 2, NULL, NULL, '{}', NULL, NULL)", TOUR, DEST)
        yield c
    finally:
        await c.close()
        await admin.execute(f'DROP DATABASE "{name}"')
        await admin.close()


async def test_place_read_runs_custom_plan_without_jit_then_restores_the_session(conn):
    # asyncpg introspects a non-core parameter type (uuid[]) once per connection and turns jit off for it with a
    # session-level set_config, restoring only a value other than 'off'; inside this read's transaction it reads the
    # SET LOCAL 'off' and so leaves jit off for the connection's life. Harmless for the browse Lambda (short reads
    # only), but it would hide whether SET LOCAL is scoped, so the codec is warmed first.
    await conn.fetch("SELECT $1::uuid[]", [TOUR])
    before = (await conn.fetchval("SHOW jit"), await conn.fetchval("SHOW plan_cache_mode"))
    seen: list = []
    rows = await cat.fetch_place_read(_Pool(conn, seen), cat.TOUR_DESTINATIONS_SQL, [TOUR])
    assert [(r["tour_id"], r["destination_id"], r["day_number"]) for r in rows] == [(TOUR, DEST, 1)]
    assert seen == [("off", "force_custom_plan")]
    # SET LOCAL ended with the read's transaction: the pooled connection is back on its own settings.
    assert (await conn.fetchval("SHOW jit"), await conn.fetchval("SHOW plan_cache_mode")) == before


async def test_first_place_read_on_a_fresh_connection_leaves_jit_off_for_the_session(conn):
    # Documents the asyncpg behaviour above (catalog.py S257 note): plan_cache_mode is still transaction-local.
    await cat.fetch_place_read(_Pool(conn, []), cat.TOUR_DESTINATIONS_SQL, [TOUR])
    assert await conn.fetchval("SHOW plan_cache_mode") == "auto"
    assert await conn.fetchval("SHOW jit") == "off"


async def test_country_places_read_keeps_places_without_coordinates(conn):
    rows = await cat.fetch_place_read(_Pool(conn, []), cat.COUNTRY_PLACES_SQL, [TOUR])
    assert [(r["destination_name"], r["day_number"]) for r in rows] == [("Hanoi", 1)]  # NULL destination left out
    await conn.execute("UPDATE catalog.v_tour_place SET lat = NULL, lng = NULL")
    rows = await cat.fetch_place_read(_Pool(conn, []), cat.COUNTRY_PLACES_SQL, [TOUR])
    assert [(r["destination_name"], r["lat"]) for r in rows] == [("Hanoi", None)]  # the page filters, not the SQL
