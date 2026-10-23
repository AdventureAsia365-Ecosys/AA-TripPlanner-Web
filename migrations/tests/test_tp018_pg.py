"""tp-018 replay (AA-712 X4): the frozen place tables dropped, applied the way DB Migrate applies it (as tp_owner,
after tp-013 … tp-017).

Runs only with ``--pg-dsn`` (a SUPERUSER DSN of a throwaway LOCAL Postgres), on the stand-in of test_tp013_pg.py:
tp-001…012 replayed as files, the CIS 243 grants, tp-013 as the master, tp-014 … tp-017 with ``SET ROLE tp_owner``,
then tp-018 the same way. Each guard case runs in its own database.

Checks: the eight tables are gone and every live table (and the id-map of tp-017) stays with its rows and the runtime
grants; the guards refuse a row written after the freeze and an object outside the eight that depends on them
(nothing changes); registration once and a re-run as tp_owner being a no-op.
"""
from __future__ import annotations

import secrets
import uuid

import pytest

from test_tp013_pg import pg_dsn  # noqa: F401  (fixture of the tp-013 replay: --pg-dsn, local only)
from test_tp013_pg import (  # helpers of the tp-013 replay; same stand-in
    BOOTSTRAP_ROLES,
    CIS_243_GRANTS,
    LIVE_STANDINS,
    MIGRATIONS,
    TP013,
    _dsn,
    _replay_order,
    _replay_text,
    run,
)
from test_tp015_pg import CIS_READER

asyncpg = pytest.importorskip("asyncpg")

BEFORE_TP018 = sorted(f for f in MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql") if 13 < int(f.name[:3]) < 18)
TP018 = MIGRATIONS / "018_drop_frozen_place_tables.sql"
FROZEN = ("itinerary_components", "tour_day", "tour_stop", "tour_graph_build", "tour_graph_edge", "tour_graph_node",
          "tour_leg", "tour_junction")
LIVE = ("customers", "sessions", "trip_events", "trip_drafts", "outbox", "narration_cache", "llm_spend_daily",
        "component_id_map")
TABLES_SQL = "SELECT tablename::text FROM pg_tables WHERE schemaname = 'tripplanner' ORDER BY 1"
DEST = "00000000-0000-4000-8000-000000000001"
OLD_ID = "00000000-0000-4000-8000-0000000000c1"
NEW_ID = "00000000-0000-5000-8000-0000000000c1"


async def _as_owner(admin, path) -> None:
    """DB Migrate's switch for a file after tp-013: SET ROLE to the live owner of schema tripplanner."""
    await admin.execute("SET ROLE tp_owner")
    try:
        await admin.execute(path.read_text(encoding="utf-8"))
    finally:
        await admin.execute("RESET ROLE")


async def _seed(su, *, written_at: str) -> None:
    """Pilot rows in the frozen tables (written at `written_at`) and an id-map row, as the master."""
    await su.execute("INSERT INTO shared.destinations (id, name, country, lat, lng) "
                     "VALUES ($1, 'Vientiane', 'Laos', 17.97, 102.6) ON CONFLICT DO NOTHING", uuid.UUID(DEST))
    cols = {r["attname"] for r in await su.fetch(
        "SELECT attname::text FROM pg_attribute WHERE attrelid = 'tripplanner.itinerary_components'::regclass "
        "AND attnum > 0 AND NOT attisdropped")}
    values = {"id": uuid.UUID(OLD_ID), "source_tour_id": "t-1", "source_day_index": 1,
              "destination_id": uuid.UUID(DEST), "name": "Night market", "activity": "culinary",
              "intensity_level": "leisurely", "season_months": [1], "duration_hint": "half_day", "text_extract": "x"}
    values = {k: v for k, v in values.items() if k in cols}
    names = ", ".join(values) + (", created_at" if "created_at" in cols else "")
    params = ", ".join(f"${i}" for i in range(1, len(values) + 1))
    params += f", '{written_at}'::timestamptz" if "created_at" in cols else ""
    await su.execute(f"INSERT INTO tripplanner.itinerary_components ({names}) VALUES ({params})", *values.values())
    await su.execute("INSERT INTO tripplanner.tour_day (source_tour_id, day_index, title, places, extracted_by, "
                     f"source_hash, extracted_at) VALUES ('t-1', 1, 'Day 1', '{{}}', 'rule', 'h', "
                     f"'{written_at}'::timestamptz)")
    await su.execute("INSERT INTO tripplanner.tour_stop (source_tour_id, day_index, destination_id, country, source) "
                     "VALUES ('t-1', 1, $1, 'Laos', 'overnight')", uuid.UUID(DEST))
    await su.execute("INSERT INTO tripplanner.tour_graph_build (id, built_at, params, stats) "
                     f"VALUES ($1, '{written_at}'::timestamptz, '{{}}', '{{}}')", uuid.uuid4())
    await su.execute("INSERT INTO tripplanner.component_id_map (old_id, new_id, matched_by) VALUES ($1, $2, 'name')",
                     uuid.UUID(OLD_ID), uuid.UUID(NEW_ID))


def _database(pg_dsn, *, written_at: str = "2026-10-10 08:00:00+00", extra_sql: str = "",  # noqa: F811
              hold_reader: bool = False):
    """Builds a database up to tp-017, seeds the frozen tables, runs `extra_sql` as the master, then tp-018 as
    tp_owner. With `hold_reader`, another session holds ACCESS SHARE on shared.destinations (an open read, like a
    catalog-view reader) while tp-018 runs with a 1s lock_timeout. Returns (context, the tp-018 error or None); the
    caller drops the database."""
    name = f"tp_aa712_{uuid.uuid4().hex[:8]}"
    password = secrets.token_urlsafe(16)

    async def setup():
        su = await asyncpg.connect(pg_dsn)
        try:
            await su.execute(BOOTSTRAP_ROLES)
            await su.execute(CIS_READER)
            await su.execute(f"ALTER ROLE aa_cis_admin PASSWORD '{password}'")
            await su.execute(f'CREATE DATABASE "{name}" OWNER aa_cis_admin')
        finally:
            await su.close()
        su_db = await asyncpg.connect(_dsn(pg_dsn, database=name))
        try:
            vector = await su_db.fetchval("SELECT count(*) > 0 FROM pg_available_extensions WHERE name = 'vector'")
            if vector:
                await su_db.execute("CREATE EXTENSION IF NOT EXISTS vector")
        finally:
            await su_db.close()
        admin_dsn = _dsn(pg_dsn, user="aa_cis_admin", password=password, database=name)
        admin = await asyncpg.connect(admin_dsn)
        try:
            await admin.execute(LIVE_STANDINS)
            for f in _replay_order():
                await admin.execute(_replay_text(f, vector))
            await admin.execute(CIS_243_GRANTS)
            await admin.execute(TP013.read_text(encoding="utf-8"))
            for f in BEFORE_TP018:
                await _as_owner(admin, f)
        finally:
            await admin.close()
        su_db = await asyncpg.connect(_dsn(pg_dsn, database=name))
        try:
            await _seed(su_db, written_at=written_at)
            if extra_sql:
                await su_db.execute(extra_sql)
        finally:
            await su_db.close()
        reader = None
        if hold_reader:
            reader = await asyncpg.connect(_dsn(pg_dsn, database=name))
            await reader.execute("BEGIN")
            await reader.fetchval("SELECT count(*) FROM shared.destinations")
        admin = await asyncpg.connect(admin_dsn)
        error = None
        try:
            if hold_reader:
                await admin.execute("SET lock_timeout = '1s'")
            await admin.execute("SET ROLE tp_owner")
            try:
                await admin.execute(TP018.read_text(encoding="utf-8"))
            except asyncpg.PostgresError as e:
                error = f"{type(e).__name__}: {e}"
                if admin.is_in_transaction():
                    await admin.execute("ROLLBACK")
        finally:
            await admin.close()
            if reader is not None:
                await reader.execute("ROLLBACK")
                await reader.close()
        return {"name": name, "admin_dsn": admin_dsn, "su_dsn": _dsn(pg_dsn, database=name)}, error

    return run(setup())


def _drop(pg_dsn, name) -> None:  # noqa: F811
    async def teardown() -> None:
        su = await asyncpg.connect(pg_dsn)
        try:
            await su.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        finally:
            await su.close()
    run(teardown())


@pytest.fixture(scope="module")
def db(pg_dsn):  # noqa: F811
    ctx, error = _database(pg_dsn)
    try:
        assert error is None, error
        yield ctx
    finally:
        _drop(pg_dsn, ctx["name"])


def test_files_before_tp018_are_tp014_to_tp017():
    assert [f.name[:3] for f in BEFORE_TP018] == ["014", "015", "016", "017"]


def test_the_frozen_tables_are_gone_and_the_live_ones_stay(db):
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            tables = {r[0] for r in await su.fetch(TABLES_SQL)}
            kept = await su.fetchval("SELECT new_id::text FROM tripplanner.component_id_map WHERE old_id = $1",
                                     uuid.UUID(OLD_ID))
            dest = await su.fetchval("SELECT count(*) FROM shared.destinations WHERE id = $1", uuid.UUID(DEST))
            return tables, kept, dest
        finally:
            await su.close()

    tables, kept, dest = run(check())
    assert not tables & set(FROZEN), sorted(tables & set(FROZEN))
    assert set(LIVE) <= tables, sorted(set(LIVE) - tables)
    assert kept == NEW_ID           # the id-map keeps resolving old draft ids
    assert dest == 1                # the FK target is CIS's table, untouched


def test_the_runtime_keeps_its_grants_on_the_live_tables(db):
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            return {t: await su.fetchval("SELECT has_table_privilege('tripplanner', $1, 'SELECT')",
                                         f"tripplanner.{t}") for t in LIVE}
        finally:
            await su.close()

    assert all(run(check()).values())


def test_tp018_registered_and_rerun_as_tp_owner_is_a_no_op(db):
    async def check():
        admin = await asyncpg.connect(db["admin_dsn"])
        try:
            await _as_owner(admin, TP018)
            return await admin.fetchval("SELECT count(*) FROM shared.schema_versions WHERE version = 'tp-018'")
        finally:
            await admin.close()

    assert run(check()) == 1


def _refused(pg_dsn, **kw) -> tuple:  # noqa: F811
    ctx, error = _database(pg_dsn, **kw)
    try:
        async def state():
            su = await asyncpg.connect(ctx["su_dsn"])
            try:
                tables = {r[0] for r in await su.fetch(TABLES_SQL)}
                registered = await su.fetchval("SELECT count(*) FROM shared.schema_versions WHERE version = 'tp-018'")
                return tables, registered
            finally:
                await su.close()
        tables, registered = run(state())
    finally:
        _drop(pg_dsn, ctx["name"])
    return error, tables, registered


def test_a_row_written_after_the_freeze_refuses_and_changes_nothing(pg_dsn):  # noqa: F811
    error, tables, registered = _refused(pg_dsn, written_at="2026-10-20 08:00:00+00")
    assert error and "after the freeze" in error, error
    assert set(FROZEN) <= tables and registered == 0


def test_a_dependent_view_refuses_and_changes_nothing(pg_dsn):  # noqa: F811
    view = "CREATE VIEW public.reads_the_pilot AS SELECT source_tour_id FROM tripplanner.tour_stop"
    error, tables, registered = _refused(pg_dsn, extra_sql=view)
    assert error and "depend on them" in error and "reads_the_pilot" in error, error
    assert set(FROZEN) <= tables and registered == 0


def test_an_open_reader_of_shared_destinations_blocks_the_drop_and_changes_nothing(pg_dsn):  # noqa: F811
    """The header's Locks note: dropping the FKs removes their RI triggers on CIS's shared.destinations, so tp-018 needs
    ACCESS EXCLUSIVE on that table too. A reader holding ACCESS SHARE on it (nothing on the eight tables) makes the
    apply fail on lock_timeout and roll back; DB Migrate retries later."""
    error, tables, registered = _refused(pg_dsn, hold_reader=True)
    assert error and "lock timeout" in error, error
    assert set(FROZEN) <= tables and registered == 0
