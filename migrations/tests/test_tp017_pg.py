"""tp-017 replay (AA-847, AA-712 slice 6c): tripplanner.component_id_map, applied the way DB Migrate applies it
(as tp_owner, after tp-013 … tp-016).

Runs only with ``--pg-dsn`` (a SUPERUSER DSN of a throwaway LOCAL Postgres), on the stand-in of test_tp013_pg.py:
tp-001…012 replayed as files, the CIS 243 grants, tp-013 as the master, tp-014 … tp-016 with ``SET ROLE tp_owner``,
then tp-017 the same way.

Checks: the table is tp_owner's with the expected columns; the runtime role tripplanner may SELECT and INSERT but
not UPDATE / DELETE / TRUNCATE; matched_by is checked; an INSERT … ON CONFLICT DO NOTHING by tripplanner (the fill)
keeps the first row; registration once and a re-run as tp_owner being a no-op.
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

asyncpg = pytest.importorskip("asyncpg")

BEFORE_TP017 = sorted(f for f in MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql") if 13 < int(f.name[:3]) < 17)
TP017 = MIGRATIONS / "017_component_id_map.sql"
CIS_READER = "DO $$ BEGIN IF to_regrole('cis_app') IS NULL THEN CREATE ROLE cis_app NOLOGIN; END IF; END $$;"
COLUMNS_SQL = """
SELECT a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull
  FROM pg_attribute a
 WHERE a.attrelid = 'tripplanner.component_id_map'::regclass AND a.attnum > 0 AND NOT a.attisdropped
 ORDER BY a.attnum"""
PRIVS = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")


async def _as_owner(admin, path) -> None:
    """DB Migrate's switch for a file after tp-013: SET ROLE to the live owner of schema tripplanner."""
    await admin.execute("SET ROLE tp_owner")
    try:
        await admin.execute(path.read_text(encoding="utf-8"))
    finally:
        await admin.execute("RESET ROLE")


@pytest.fixture(scope="module")
def db(pg_dsn):  # noqa: F811
    name = f"tp_aa847_{uuid.uuid4().hex[:8]}"
    password = secrets.token_urlsafe(16)

    async def setup() -> dict:
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
            for f in BEFORE_TP017:
                await _as_owner(admin, f)
            await _as_owner(admin, TP017)
        finally:
            await admin.close()
        return {"admin_dsn": admin_dsn, "su_dsn": _dsn(pg_dsn, database=name)}

    async def teardown() -> None:
        su = await asyncpg.connect(pg_dsn)
        try:
            await su.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        finally:
            await su.close()

    try:
        yield run(setup())
    finally:
        run(teardown())


def test_files_before_tp017_are_tp014_to_tp016():
    assert [f.name[:3] for f in BEFORE_TP017] == ["014", "015", "016"]


def test_table_is_tp_owners_with_the_expected_columns(db):
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            owner = await su.fetchval("SELECT tableowner FROM pg_tables WHERE schemaname = 'tripplanner' "
                                      "AND tablename = 'component_id_map'")
            cols = [tuple(r) for r in await su.fetch(COLUMNS_SQL)]
            return owner, cols
        finally:
            await su.close()

    owner, cols = run(check())
    assert owner == "tp_owner"
    assert cols == [("old_id", "uuid", True), ("new_id", "uuid", True), ("matched_by", "text", True),
                    ("created_at", "timestamp with time zone", True)]


def test_runtime_reads_and_inserts_only(db):
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            return {p: await su.fetchval("SELECT has_table_privilege('tripplanner', "
                                         "'tripplanner.component_id_map', $1)", p) for p in PRIVS}
        finally:
            await su.close()

    assert run(check()) == {"SELECT": True, "INSERT": True, "UPDATE": False, "DELETE": False, "TRUNCATE": False}


def test_fill_shape_as_tripplanner_keeps_the_first_row_and_checks_matched_by(db):
    old, new1, new2 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            async with su.transaction():
                await su.execute("SET LOCAL ROLE tripplanner")
                ins = ("INSERT INTO tripplanner.component_id_map (old_id, new_id, matched_by) "
                       "VALUES ($1, $2, $3) ON CONFLICT (old_id) DO NOTHING")
                await su.execute(ins, old, new1, "name")
                await su.execute(ins, old, new2, "destination")
                kept = await su.fetchval("SELECT new_id FROM tripplanner.component_id_map WHERE old_id = $1", old)
                with pytest.raises(asyncpg.CheckViolationError):
                    async with su.transaction():
                        await su.execute(ins, uuid.uuid4(), new1, "guess")
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    async with su.transaction():
                        await su.execute("DELETE FROM tripplanner.component_id_map")
                raise _Rollback(kept)
        except _Rollback as r:
            return r.value
        finally:
            await su.close()

    assert run(check()) == new1


class _Rollback(Exception):
    def __init__(self, value):
        super().__init__(value)
        self.value = value


def test_tp017_registered_and_rerun_as_tp_owner_is_a_no_op(db):
    async def check():
        admin = await asyncpg.connect(db["admin_dsn"])
        try:
            await _as_owner(admin, TP017)
            return await admin.fetchval("SELECT count(*) FROM shared.schema_versions WHERE version = 'tp-017'")
        finally:
            await admin.close()

    assert run(check()) == 1
