"""tp-025 replay (AA-1027 E5): tripplanner.llm_spend_daily (the tp-010 spend counter) dropped, applied the way DB
Migrate applies it (as tp_owner, after tp-013 … tp-024).

Runs only with ``--pg-dsn`` (a SUPERUSER DSN of a throwaway LOCAL Postgres), on the stand-in of test_tp013_pg.py:
tp-001…012 replayed as files, the CIS 243 grants, tp-013 as the master, tp-014 … tp-024 with ``SET ROLE tp_owner``,
then tp-025 the same way. Each guard case runs in its own database.

Checks: the destructive header (MIG-12) in one BEGIN … COMMIT; the table is gone, every other live table stays with
its runtime grants; the guards refuse a row written less than an hour ago, an object that depends on the table and a
run as another role (nothing changes); registration once and a re-run as tp_owner being a no-op.
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

BEFORE_TP025 = sorted(f for f in MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql") if 13 < int(f.name[:3]) < 25)
TP025 = MIGRATIONS / "025_drop_llm_spend_daily.sql"
DROPPED = "llm_spend_daily"
LIVE = ("customers", "sessions", "trip_events", "trip_drafts", "outbox", "narration_cache", "component_id_map",
        "traveller_accounts", "job", "ai_rate_counter")
TABLES_SQL = "SELECT tablename::text FROM pg_tables WHERE schemaname = 'tripplanner' ORDER BY 1"


async def _as_owner(admin, path) -> None:
    await admin.execute("SET ROLE tp_owner")
    try:
        await admin.execute(path.read_text(encoding="utf-8"))
    finally:
        await admin.execute("RESET ROLE")


def _database(pg_dsn, *, written: str = "2 days", extra_sql: str = "", role: str = "tp_owner"):  # noqa: F811
    """Builds a database up to tp-024, seeds two counter rows (the newer written `written` ago), runs `extra_sql` as
    the superuser, then tp-025 as `role`. Returns (context, the tp-025 error or None); the caller drops it."""
    name = f"tp_aa1027_{uuid.uuid4().hex[:8]}"
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
            for f in BEFORE_TP025:
                await _as_owner(admin, f)
        finally:
            await admin.close()
        su_db = await asyncpg.connect(_dsn(pg_dsn, database=name))
        try:
            await su_db.execute(
                "INSERT INTO tripplanner.llm_spend_daily (day, stage, cost_usd, calls, unpriced_calls, updated_at) "
                "VALUES (current_date - 3, 'tp_compose', 0.12, 4, 0, now() - interval '3 days'), "
                f"(current_date, 'tp_search_embed', 0.0004, 9, 1, now() - interval '{written}')")
            if extra_sql:
                await su_db.execute(extra_sql)
        finally:
            await su_db.close()
        admin = await asyncpg.connect(admin_dsn)
        error = None
        try:
            if role != "aa_cis_admin":
                await admin.execute(f"SET ROLE {role}")
            try:
                await admin.execute(TP025.read_text(encoding="utf-8"))
            except asyncpg.PostgresError as e:
                error = f"{type(e).__name__}: {e}"
                if admin.is_in_transaction():
                    await admin.execute("ROLLBACK")
        finally:
            await admin.close()
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


def test_files_before_tp025_run_from_tp014_without_a_gap():
    numbers = [int(f.name[:3]) for f in BEFORE_TP025]
    assert numbers == list(range(14, 14 + len(numbers))) and numbers[-1] >= 23, numbers


def test_file_is_destructive_in_one_transaction_block():
    """MIG-12: the destructive header (DB Migrate takes a snapshot first); one BEGIN … COMMIT."""
    text = TP025.read_text(encoding="utf-8")
    assert text.splitlines()[1] == "-- migrate: destructive"
    code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("--"))
    assert code.count("BEGIN;") == 1 and code.count("COMMIT;") == 1
    assert "DROP TABLE IF EXISTS tripplanner.llm_spend_daily;" in code


def test_the_counter_is_gone_and_the_live_tables_stay_with_their_grants(db):
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            tables = {r[0] for r in await su.fetch(TABLES_SQL)}
            grants = {t: await su.fetchval("SELECT has_table_privilege('tripplanner', $1, 'SELECT') AS ok",
                                           f"tripplanner.{t}") for t in LIVE if t in tables}
            registered = await su.fetchval("SELECT count(*) AS n FROM shared.schema_versions WHERE version = 'tp-025'")
            return tables, grants, registered
        finally:
            await su.close()

    tables, grants, registered = run(check())
    assert DROPPED not in tables
    assert set(LIVE) <= tables, sorted(set(LIVE) - tables)
    assert all(grants.values()), grants
    assert registered == 1


def test_rerun_as_tp_owner_is_a_no_op(db):
    async def check():
        admin = await asyncpg.connect(db["admin_dsn"])
        try:
            first = await admin.fetchval("SELECT applied_at FROM shared.schema_versions WHERE version = 'tp-025'")
            await _as_owner(admin, TP025)
            rows = await admin.fetch("SELECT applied_at FROM shared.schema_versions WHERE version = 'tp-025'")
            return first, [r["applied_at"] for r in rows]
        finally:
            await admin.close()

    first, after = run(check())
    assert after == [first]


def _refused(pg_dsn, **kw) -> tuple:  # noqa: F811
    ctx, error = _database(pg_dsn, **kw)
    try:
        async def state():
            su = await asyncpg.connect(ctx["su_dsn"])
            try:
                tables = {r[0] for r in await su.fetch(TABLES_SQL)}
                rows = await su.fetchval("SELECT count(*) AS n FROM tripplanner.llm_spend_daily") \
                    if DROPPED in tables else None
                registered = await su.fetchval(
                    "SELECT count(*) AS n FROM shared.schema_versions WHERE version = 'tp-025'")
                return tables, rows, registered
            finally:
                await su.close()
        tables, rows, registered = run(state())
    finally:
        _drop(pg_dsn, ctx["name"])
    return error, tables, rows, registered


def test_a_row_written_within_the_hour_refuses_and_changes_nothing(pg_dsn):  # noqa: F811
    error, tables, rows, registered = _refused(pg_dsn, written="5 minutes")
    assert error and "less than an hour ago" in error, error
    assert DROPPED in tables and rows == 2 and registered == 0


def test_a_dependent_view_refuses_and_changes_nothing(pg_dsn):  # noqa: F811
    view = "CREATE VIEW public.reads_the_counter AS SELECT day, cost_usd FROM tripplanner.llm_spend_daily"
    error, tables, rows, registered = _refused(pg_dsn, extra_sql=view)
    assert error and "depend on" in error and "reads_the_counter" in error, error
    assert DROPPED in tables and rows == 2 and registered == 0


def test_another_role_refuses_and_changes_nothing(pg_dsn):  # noqa: F811
    error, tables, rows, registered = _refused(pg_dsn, role="aa_cis_admin")
    assert error and "expected to run as tp_owner" in error, error
    assert DROPPED in tables and rows == 2 and registered == 0
