"""tp-015 replay (AA-858): the ops overview contract view tripplanner.v_ops_traffic_daily on a stand-in of the live
shape, applied the way DB Migrate applies it (as tp_owner, after tp-013 / tp-014).

Runs only with ``--pg-dsn`` (a SUPERUSER DSN of a throwaway LOCAL Postgres), like test_tp013_pg.py, whose stand-in
it reuses: roles + ``cis_app`` (NOLOGIN here; on Dev the CIS runtime LOGIN of X1, Infra rl-001), a fresh database
owned by the NOSUPERUSER master stand-in ``aa_cis_admin``, tp-001…012 replayed as files, the CIS 243 grants, tp-013
as the master, then tp-014 and tp-015 with ``SET ROLE tp_owner`` (the runner's live-owner switch, XD8).

Checks: shape (aggregates only), metrics from rows written as the runtime role, the 400-day bound, the reader grants
(cis_app reads the view and nothing else; tripplanner reads it but cannot write it; the master reads it through its
tp_owner membership) and a re-run as tp_owner being a no-op.
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

AFTER_TRANSFER = sorted(f for f in MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql") if 13 < int(f.name[:3]) <= 15)
TP015 = MIGRATIONS / "015_ops_overview_view.sql"
VIEW = "tripplanner.v_ops_traffic_daily"
CIS_READER = "DO $$ BEGIN IF to_regrole('cis_app') IS NULL THEN CREATE ROLE cis_app NOLOGIN; END IF; END $$;"

TODAY_SQL = f"SELECT metric, n FROM {VIEW} WHERE day = (now() AT TIME ZONE 'UTC')::date"


async def _as_owner(admin, path) -> None:
    """DB Migrate's switch for a file after tp-013: SET ROLE to the live owner of schema tripplanner."""
    await admin.execute("SET ROLE tp_owner")
    try:
        await admin.execute(path.read_text(encoding="utf-8"))
    finally:
        await admin.execute("RESET ROLE")


@pytest.fixture(scope="module")
def db(pg_dsn):  # noqa: F811
    name = f"tp_aa858_{uuid.uuid4().hex[:8]}"
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
            for f in AFTER_TRANSFER:
                await _as_owner(admin, f)
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


def test_after_transfer_files_are_tp014_and_tp015():
    assert [f.name[:3] for f in AFTER_TRANSFER] == ["014", "015"]


def test_tp015_registered_and_view_exposes_aggregates_only(db):
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            registered = await su.fetchval("SELECT count(*) FROM shared.schema_versions WHERE version = 'tp-015'")
            cols = await su.fetch(
                "SELECT column_name, data_type FROM information_schema.columns"
                " WHERE table_schema = 'tripplanner' AND table_name = 'v_ops_traffic_daily' ORDER BY ordinal_position")
            owner = await su.fetchval(f"SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid = '{VIEW}'::regclass")
            return registered, [tuple(r) for r in cols], owner
        finally:
            await su.close()

    registered, cols, owner = run(check())
    assert registered == 1
    assert cols == [("day", "date"), ("metric", "text"), ("n", "bigint")]
    assert owner == "tp_owner"


def test_metrics_count_rows_written_by_the_runtime(db):
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            async with su.transaction():
                before = {r["metric"]: r["n"] for r in await su.fetch(TODAY_SQL)}
                await su.execute("SET LOCAL ROLE tripplanner")
                s1 = await su.fetchval("INSERT INTO tripplanner.sessions DEFAULT VALUES RETURNING id")
                s2 = await su.fetchval("INSERT INTO tripplanner.sessions DEFAULT VALUES RETURNING id")
                t1 = await su.fetchval("INSERT INTO tripplanner.trip_drafts (session_id, status, itinerary)"
                                       " VALUES ($1, 'draft', '{}') RETURNING id", s1)
                t2 = await su.fetchval("INSERT INTO tripplanner.trip_drafts (session_id, status, itinerary)"
                                       " VALUES ($1, 'draft', '{}') RETURNING id", s2)
                for trip, session, kind in ((t1, s1, "add_component"), (t1, s1, "reorder"), (t1, s1, "add_day"),
                                            (t2, s2, "add_component"), (t2, s2, "sent")):
                    await su.execute("INSERT INTO tripplanner.trip_events (trip_id, session_id, event_type, payload)"
                                     " VALUES ($1, $2, $3, '{}')", trip, session, kind)
                await su.execute("INSERT INTO tripplanner.outbox (event, schema_version, idempotency_key, aggregate_id,"
                                 " payload) VALUES ('inquiry.submitted', 1, $1, $2, '{}')", f"{t2}:aa858", t2)
                # Older than the 400-day window: left out.
                await su.execute("INSERT INTO tripplanner.sessions (created_at) VALUES (now() - interval '401 days')")
                await su.execute("RESET ROLE")
                after = {r["metric"]: r["n"] for r in await su.fetch(TODAY_SQL)}
                oldest = await su.fetchval(f"SELECT min(day) FROM {VIEW}")
                floor = await su.fetchval("SELECT ((now() - interval '400 days') AT TIME ZONE 'UTC')::date")
                raise _Rollback((before, after, oldest, floor))
        except _Rollback as r:
            return r.value
        finally:
            await su.close()

    before, after, oldest, floor = run(check())
    delta = {k: after.get(k, 0) - before.get(k, 0) for k in set(after) | set(before)}
    assert delta == {"planner_session_started": 2, "trip_started": 2, "trip_edited": 2, "inquiry_sent": 1}
    assert oldest is None or oldest >= floor


def test_cis_app_reads_the_view_and_nothing_else(db):
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            denied = []
            async with su.transaction():
                await su.execute("SET LOCAL ROLE cis_app")
                await su.fetch(f"SELECT * FROM {VIEW} LIMIT 1")
                for table in ("tripplanner.sessions", "tripplanner.outbox", "tripplanner.customers"):
                    try:
                        async with su.transaction():
                            await su.fetch(f"SELECT 1 FROM {table} LIMIT 1")
                    except asyncpg.InsufficientPrivilegeError:
                        denied.append(table)
            grants = await su.fetch(
                "SELECT c.relname, a.privilege_type FROM pg_class c, aclexplode(c.relacl) a"
                " WHERE c.relnamespace = 'tripplanner'::regnamespace AND a.grantee = 'cis_app'::regrole")
            usage = await su.fetchval("SELECT has_schema_privilege('cis_app', 'tripplanner', 'USAGE')")
            create = await su.fetchval("SELECT has_schema_privilege('cis_app', 'tripplanner', 'CREATE')")
            return denied, {(r["relname"], r["privilege_type"]) for r in grants}, usage, create
        finally:
            await su.close()

    denied, grants, usage, create = run(check())
    assert denied == ["tripplanner.sessions", "tripplanner.outbox", "tripplanner.customers"]
    assert grants == {("v_ops_traffic_daily", "SELECT")}
    assert usage and not create


def test_runtime_reads_but_cannot_write_and_master_reads_through_membership(db):
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        admin = await asyncpg.connect(db["admin_dsn"])
        try:
            privs = {p: await su.fetchval("SELECT has_table_privilege('tripplanner', $1, $2)", VIEW, p)
                     for p in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")}
            master_rows = await admin.fetch(f"SELECT * FROM {VIEW} LIMIT 1")
            return privs, master_rows is not None
        finally:
            await admin.close()
            await su.close()

    privs, master_reads = run(check())
    assert privs == {"SELECT": True, "INSERT": False, "UPDATE": False, "DELETE": False, "TRUNCATE": False}
    assert master_reads


def test_rerun_as_tp_owner_is_a_no_op(db):
    async def check():
        admin = await asyncpg.connect(db["admin_dsn"])
        try:
            await _as_owner(admin, TP015)
            return await admin.fetchval("SELECT count(*) FROM shared.schema_versions WHERE version = 'tp-015'")
        finally:
            await admin.close()

    assert run(check()) == 1


class _Rollback(Exception):
    def __init__(self, value):
        super().__init__("rollback")
        self.value = value
