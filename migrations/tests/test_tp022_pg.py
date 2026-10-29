"""tp-022 replay (AA-1003 R2): the inquiry key contract view tripplanner.v_ops_inquiry_keys, applied the way DB Migrate
applies it (as tp_owner, after tp-013 … tp-021).

Runs only with ``--pg-dsn`` (a SUPERUSER DSN of a throwaway LOCAL Postgres), on the stand-in of test_tp013_pg.py:
tp-001…012 replayed as files, the CIS 243 grants, tp-013 as the master, tp-014 … tp-021 with ``SET ROLE tp_owner``,
then tp-022 the same way.

Checks: the exact column list (the contract), owner, comment and registration; the view lists the key and time of
inquiry.submitted outbox rows of the last 30 days only and never the payload; cis_app reads the view (and the earlier
contract views) and nothing else (every base table 42501, no CREATE); the runtime role reads but cannot write the
view; the guard refuses a runner other than tp_owner; a re-run as tp_owner is a no-op; the file stays a contract
file (no expand header: its cis_app grant is a cross-owner grant).
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

BEFORE_TP022 = sorted(f for f in MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql") if 13 < int(f.name[:3]) < 22)
TP022 = MIGRATIONS / "022_ops_inquiry_keys_view.sql"
VIEW = "tripplanner.v_ops_inquiry_keys"
BASE_TABLES = ("tripplanner.outbox", "tripplanner.sessions", "tripplanner.trip_drafts", "tripplanner.customers")
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
    name = f"tp_inqkeys_{uuid.uuid4().hex[:8]}"
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
            for f in BEFORE_TP022:
                await _as_owner(admin, f)
            await _as_owner(admin, TP022)
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


def test_files_before_tp022_are_tp014_to_tp021():
    assert [f.name[:3] for f in BEFORE_TP022] == ["014", "015", "016", "017", "018", "019", "020", "021"]


def test_file_is_a_contract_file():
    """No `-- migrate: expand` header: the cis_app grant is a cross-owner grant (root ADR 0017 'What stays manual'),
    which the Infra expand lint refuses; the strict-prefix auto-apply stops here and a person applies it."""
    head = [ln for ln in TP022.read_text(encoding="utf-8").splitlines() if ln.startswith("-- migrate:")]
    assert head == []


def test_view_has_the_contract_columns_owner_comment_and_registration(db):
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            registered = await su.fetchval("SELECT count(*) FROM shared.schema_versions WHERE version = 'tp-022'")
            cols = await su.fetch(
                "SELECT column_name, data_type FROM information_schema.columns"
                " WHERE table_schema = 'tripplanner' AND table_name = 'v_ops_inquiry_keys' ORDER BY ordinal_position")
            owner = await su.fetchval(f"SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid = '{VIEW}'::regclass")
            comment = await su.fetchval(f"SELECT obj_description('{VIEW}'::regclass, 'pg_class')")
            return registered, [tuple(r) for r in cols], owner, comment
        finally:
            await su.close()

    registered, cols, owner, comment = run(check())
    assert registered == 1
    assert cols == [("idempotency_key", "text"), ("created_at", "timestamp with time zone")]
    assert owner == "tp_owner"
    assert comment.startswith("tp-022 (AA-1003)") and "ops.overview.v1" in comment and "cis_app" in comment


def test_view_lists_recent_inquiry_keys_and_never_the_payload(db):
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            async with su.transaction():
                await su.execute("SET LOCAL ROLE tripplanner")
                rows = []
                for age in ("1 minute", "29 days", "31 days"):
                    trip = uuid.uuid4()
                    key = f"{trip}:h-{age.replace(' ', '')}"
                    payload = {"contract": "inquiry.submitted", "idempotency_key": key,
                               "customer": {"name": "Jane Traveller", "email": "jane@example.test"}}
                    await su.execute(
                        "INSERT INTO tripplanner.outbox (event, schema_version, idempotency_key, aggregate_id, payload,"
                        f" created_at) VALUES ('inquiry.submitted', 1, $1, $2, $3::jsonb, now() - interval '{age}')",
                        key, trip, json.dumps(payload))
                    rows.append((age, key))
                await su.execute("RESET ROLE")
                await su.execute("SET LOCAL ROLE cis_app")
                seen = {r["idempotency_key"]: r["created_at"] for r in await su.fetch(f"SELECT * FROM {VIEW}")}
                text = json.dumps([list(map(str, r)) for r in await su.fetch(f"SELECT * FROM {VIEW}")])
                raise _Rollback((rows, seen, text))
        except _Rollback as r:
            return r.value
        finally:
            await su.close()

    rows, seen, text = run(check())
    keys = dict(rows)
    assert keys["1 minute"] in seen and keys["29 days"] in seen
    assert keys["31 days"] not in seen
    assert "Jane" not in text and "example.test" not in text


def test_cis_app_reads_the_contract_views_and_nothing_else(db):
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            denied = []
            async with su.transaction():
                await su.execute("SET LOCAL ROLE cis_app")
                await su.fetch(f"SELECT * FROM {VIEW}")
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
                      ("v_ops_pin_components", "SELECT"), ("v_ops_component_id_map", "SELECT"),
                      ("v_ops_inquiry_keys", "SELECT")}
    assert not create


def test_runtime_reads_but_cannot_write_the_view(db):
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            return {p: await su.fetchval("SELECT has_table_privilege('tripplanner', $1, $2)", VIEW, p) for p in PRIVS}
        finally:
            await su.close()

    assert run(check()) == {p: p == "SELECT" for p in PRIVS}


def test_guard_refuses_a_runner_other_than_tp_owner(db):
    async def check():
        admin = await asyncpg.connect(db["admin_dsn"])
        try:
            with pytest.raises(asyncpg.RaiseError, match="expected to run as tp_owner"):
                await admin.execute(TP022.read_text(encoding="utf-8"))
        finally:
            await admin.close()

    run(check())


def test_rerun_as_tp_owner_is_a_no_op(db):
    async def check():
        admin = await asyncpg.connect(db["admin_dsn"])
        try:
            first = await admin.fetchval("SELECT applied_at FROM shared.schema_versions WHERE version = 'tp-022'")
            await _as_owner(admin, TP022)
            rows = await admin.fetch("SELECT applied_at FROM shared.schema_versions WHERE version = 'tp-022'")
            return first, [r["applied_at"] for r in rows]
        finally:
            await admin.close()

    first, after = run(check())
    assert after == [first]
