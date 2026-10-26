"""tp-020 replay (AA-992): the data coverage contract views tripplanner.v_ops_data_totals and
tripplanner.v_ops_pin_components / v_ops_component_id_map, applied the way DB Migrate applies them (as tp_owner, after tp-013 … tp-019).

Runs only with ``--pg-dsn`` (a SUPERUSER DSN of a throwaway LOCAL Postgres), on the stand-in of test_tp013_pg.py:
tp-001…012 replayed as files, the CIS 243 grants, tp-013 as the master, tp-014 … tp-019 with ``SET ROLE tp_owner``,
then tp-020 the same way.

Checks: the exact column lists (the contract), owner and registration; the totals count rows written as the runtime
role, the pin view exposes only catalog component ids (no trip, session or payload text) and the id-map view only
the map's id pairs, and a non-UUID component_id (free text a traveller sent) is counted as invalid, never shown;
cis_app reads the three views and nothing else (every base table 42501, no CREATE); the runtime role reads but cannot
write the views; the guard refuses a runner other than tp_owner; a re-run as tp_owner is a no-op.
"""
from __future__ import annotations

import json
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

BEFORE_TP020 = sorted(f for f in MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql") if 13 < int(f.name[:3]) < 20)
TP020 = MIGRATIONS / "020_ops_data_coverage_views.sql"
TOTALS = "tripplanner.v_ops_data_totals"
PINS = "tripplanner.v_ops_pin_components"
IDMAP = "tripplanner.v_ops_component_id_map"
VIEWS = (TOTALS, PINS, IDMAP)
METRICS = ("sessions", "trip_drafts", "trip_drafts_sent", "trips_with_events", "pin_events", "pin_events_invalid_id",
           "component_id_map_rows", "customers", "traveller_accounts", "outbox_rows", "narration_cache_rows")
BASE_TABLES = ("tripplanner.sessions", "tripplanner.trip_drafts", "tripplanner.trip_events",
               "tripplanner.component_id_map", "tripplanner.customers", "tripplanner.traveller_accounts",
               "tripplanner.outbox", "tripplanner.narration_cache")
PRIVS = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")


async def _as_owner(admin, path) -> None:
    """DB Migrate's switch for a file after tp-013: SET ROLE to the live owner of schema tripplanner."""
    await admin.execute("SET ROLE tp_owner")
    try:
        await admin.execute(path.read_text(encoding="utf-8"))
    finally:
        await admin.execute("RESET ROLE")


class _Rollback(Exception):
    def __init__(self, value):
        super().__init__("rollback")
        self.value = value


@pytest.fixture(scope="module")
def db(pg_dsn):  # noqa: F811
    name = f"tp_datacov_{uuid.uuid4().hex[:8]}"
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
            for f in BEFORE_TP020:
                await _as_owner(admin, f)
            await _as_owner(admin, TP020)
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


def test_files_before_tp020_are_tp014_to_tp019():
    assert [f.name[:3] for f in BEFORE_TP020] == ["014", "015", "016", "017", "018", "019"]


def test_views_have_the_contract_columns_owner_comment_and_registration(db):
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            registered = await su.fetchval("SELECT count(*) FROM shared.schema_versions WHERE version = 'tp-020'")
            out = {}
            for view in VIEWS:
                schema, rel = view.split(".")
                cols = await su.fetch(
                    "SELECT column_name, data_type FROM information_schema.columns"
                    " WHERE table_schema = $1 AND table_name = $2 ORDER BY ordinal_position", schema, rel)
                owner = await su.fetchval(f"SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid = '{view}'::regclass")
                comment = await su.fetchval(f"SELECT obj_description('{view}'::regclass, 'pg_class')")
                out[view] = ([tuple(r) for r in cols], owner, comment)
            return registered, out
        finally:
            await su.close()

    registered, out = run(check())
    assert registered == 1
    assert out[TOTALS][0] == [("metric", "text"), ("n", "bigint")]
    assert out[PINS][0] == [("component_id", "text"), ("trips", "bigint")]
    assert out[IDMAP][0] == [("old_id", "text"), ("new_id", "text")]
    for view in VIEWS:
        _, owner, comment = out[view]
        assert owner == "tp_owner"
        assert comment.startswith("tp-020 (AA-992)") and "ops.data_coverage.v1" in comment and "cis_app" in comment


def test_totals_and_pins_count_rows_written_by_the_runtime(db):
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            async with su.transaction():
                before = {r["metric"]: r["n"] for r in await su.fetch(f"SELECT metric, n FROM {TOTALS}")}
                await su.execute("SET LOCAL ROLE tripplanner")
                s1 = await su.fetchval("INSERT INTO tripplanner.sessions DEFAULT VALUES RETURNING id")
                s2 = await su.fetchval("INSERT INTO tripplanner.sessions DEFAULT VALUES RETURNING id")
                t1 = await su.fetchval("INSERT INTO tripplanner.trip_drafts (session_id, status, itinerary)"
                                       " VALUES ($1, 'draft', '{}') RETURNING id", s1)
                t2 = await su.fetchval("INSERT INTO tripplanner.trip_drafts (session_id, status, itinerary)"
                                       " VALUES ($1, 'sent', '{}') RETURNING id", s2)
                old, new, plain = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
                await su.execute("INSERT INTO tripplanner.component_id_map (old_id, new_id, matched_by)"
                                 " VALUES ($1, $2, 'name')", old, new)
                events = (
                    (t1, s1, "add_component", {"component_id": str(old), "note": "secret traveller text"}),
                    (t1, s1, "add_component", {"component_id": str(old)}),  # same trip twice: one pin
                    (t2, s2, "add_component", {"component_id": str(old)}),
                    (t2, s2, "add_component", {"component_id": str(plain)}),
                    (t2, s2, "add_component", {"no_component": True}),  # no id: not a pin
                    # traveller-supplied, not a catalog key: counted as invalid, never exposed
                    (t2, s2, "add_component", {"component_id": "call me on +84 90 123 4567, Jane"}),
                    (t2, s2, "add_component", {"component_id": 12345}),
                    (t2, s2, "add_component", {"component_id": str(plain) + " extra"}),
                    (t1, s1, "remove_component", {"component_id": str(plain)}),
                    (t2, s2, "sent", {}),
                )
                for trip, session, kind, payload in events:
                    await su.execute("INSERT INTO tripplanner.trip_events (trip_id, session_id, event_type, payload)"
                                     " VALUES ($1, $2, $3, $4::jsonb)", trip, session, kind, json.dumps(payload))
                await su.execute("RESET ROLE")
                after = {r["metric"]: r["n"] for r in await su.fetch(f"SELECT metric, n FROM {TOTALS}")}
                pins = {r["component_id"]: r["trips"] for r in await su.fetch(f"SELECT * FROM {PINS}")}
                idmap = {(r["old_id"], r["new_id"]) for r in await su.fetch(f"SELECT * FROM {IDMAP}")}
                raise _Rollback((before, after, pins, idmap, old, new, plain))
        except _Rollback as r:
            return r.value
        finally:
            await su.close()

    before, after, pins, idmap, old, new, plain = run(check())
    assert set(after) == set(METRICS) and len(after) == len(METRICS)
    delta = {k: after[k] - before.get(k, 0) for k in after}
    assert delta == {"sessions": 2, "trip_drafts": 2, "trip_drafts_sent": 1, "trips_with_events": 2,
                     "pin_events": 8, "pin_events_invalid_id": 3, "component_id_map_rows": 1, "customers": 0, "traveller_accounts": 0,
                     "outbox_rows": 0, "narration_cache_rows": 0}
    assert pins == {str(old): 2, str(plain): 1}
    assert not any("Jane" in k or "12345" in k or "extra" in k for k in pins)
    assert idmap == {(str(old), str(new))}


def test_cis_app_reads_the_views_and_nothing_else(db):
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            denied = []
            async with su.transaction():
                await su.execute("SET LOCAL ROLE cis_app")
                for view in VIEWS:
                    await su.fetch(f"SELECT * FROM {view}")
                for table in BASE_TABLES:
                    try:
                        async with su.transaction():
                            await su.fetch(f"SELECT 1 FROM {table} LIMIT 1")
                    except asyncpg.InsufficientPrivilegeError:
                        denied.append(table)
            grants = await su.fetch(
                "SELECT c.relname, a.privilege_type FROM pg_class c, aclexplode(c.relacl) a"
                " WHERE c.relnamespace = 'tripplanner'::regnamespace AND a.grantee = 'cis_app'::regrole")
            create = await su.fetchval("SELECT has_schema_privilege('cis_app', 'tripplanner', 'CREATE')")
            return denied, {(r["relname"], r["privilege_type"]) for r in grants}, create
        finally:
            await su.close()

    denied, grants, create = run(check())
    assert denied == list(BASE_TABLES)
    assert grants == {("v_ops_traffic_daily", "SELECT"), ("v_ops_data_totals", "SELECT"),
                      ("v_ops_pin_components", "SELECT"), ("v_ops_component_id_map", "SELECT")}
    assert not create


def test_runtime_reads_but_cannot_write_the_views(db):
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            return {(v, p): await su.fetchval("SELECT has_table_privilege('tripplanner', $1, $2)", v, p)
                    for v in VIEWS for p in PRIVS}
        finally:
            await su.close()

    privs = run(check())
    assert privs == {(v, p): p == "SELECT" for v in VIEWS for p in PRIVS}


def test_guard_refuses_a_runner_other_than_tp_owner(db):
    async def check():
        admin = await asyncpg.connect(db["admin_dsn"])
        try:
            with pytest.raises(asyncpg.RaiseError, match="expected to run as tp_owner"):
                await admin.execute(TP020.read_text(encoding="utf-8"))
        finally:
            await admin.close()

    run(check())


def test_rerun_as_tp_owner_is_a_no_op(db):
    async def check():
        admin = await asyncpg.connect(db["admin_dsn"])
        try:
            first = await admin.fetchval("SELECT applied_at FROM shared.schema_versions WHERE version = 'tp-020'")
            await _as_owner(admin, TP020)
            rows = await admin.fetch("SELECT applied_at FROM shared.schema_versions WHERE version = 'tp-020'")
            return first, [r["applied_at"] for r in rows]
        finally:
            await admin.close()

    first, after = run(check())
    assert after == [first]
