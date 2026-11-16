"""tp-032 replay (AA-1116, option B: the monthly AI sessions quota): tripplanner.ai_session_month, applied the way
Migration Apply applies it (as tp_owner, after tp-013 … tp-031; tp-032 twice).

Runs only with ``--pg-dsn`` (a SUPERUSER DSN of a throwaway LOCAL Postgres). The database is tp-031's
(``test_tp031_pg.erasure_database``: tp-028's forced database, tp-029 … tp-031), with no seed rows, then tp-032 as
tp_owner twice.

Checks: the expand header (root ADR 0017 d3) and the Infra-safe shape (one BEGIN … COMMIT, no DO block, no ALTER /
REVOKE); the columns, key, CHECKs, owner, comment, no row-level security (no tenant_id column: tp-029's scope spelling)
and the registration; the runtime role runs the code's statements (backend/shared/tenant_sessions.py: lock, count, add,
counted, written out here) and the CHECKs refuse a bad scope or month; cis_app sees nothing; a re-run is a no-op.
The admission rule on this database: backend/tests/test_aa1116_tenant_sessions_pg.py.
"""
from __future__ import annotations

import pytest

from test_tp013_pg import pg_dsn  # noqa: F401  (fixture of the tp-013 replay: --pg-dsn, local only)
from test_tp013_pg import MIGRATIONS, run
from test_tp025_pg import _as_owner, _drop
from test_tp031_pg import erasure_database

asyncpg = pytest.importorskip("asyncpg")

TP032 = MIGRATIONS / "032_ai_session_month.sql"
TABLE = "tripplanner.ai_session_month"
TENANT = "7a1c0de5-0000-4000-8000-000000001116"
SESSION_A = "7a1c0de5-0000-4000-8000-0000000000a1"
SESSION_B = "7a1c0de5-0000-4000-8000-0000000000b2"

# The statements of backend/shared/tenant_sessions.py (AA-1116 code PR), written out here so this migration test does
# not depend on the code that ships after it (MIG-4); backend/tests/test_aa1116_tenant_sessions_pg.py runs the code's
# own statements on this table.
_MONTH = "date_trunc('month', now() AT TIME ZONE 'UTC')::date"
LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('tp_ai_session_month:' || $1, 0))"
COUNT = f"SELECT count(*) AS n FROM {TABLE} WHERE scope = $1 AND month = {_MONTH}"
ADD = (f"INSERT INTO {TABLE} (scope, month, session_id) VALUES ($1, {_MONTH}, $2::uuid)"
       " ON CONFLICT (scope, month, session_id) DO NOTHING")
COUNTED = f"SELECT 1 AS counted FROM {TABLE} WHERE scope = $1 AND month = {_MONTH} AND session_id = $2::uuid"


class _Rollback(Exception):
    def __init__(self, value):
        super().__init__("rollback")
        self.value = value


@pytest.fixture(scope="module")
def db(pg_dsn):  # noqa: F811
    ctx, error = erasure_database(pg_dsn, seed=False)
    try:
        assert error is None, error

        async def apply():
            admin = await asyncpg.connect(ctx["admin_dsn"])
            try:
                await _as_owner(admin, TP032)
                await _as_owner(admin, TP032)                        # re-runnable
            finally:
                await admin.close()

        run(apply())
        yield ctx
    finally:
        _drop(pg_dsn, ctx["name"])


def test_file_is_an_expand_file_in_one_transaction_block():
    text = TP032.read_text(encoding="utf-8")
    assert text.splitlines()[0] == "-- migrate: expand"
    code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("--"))
    assert code.count("BEGIN;") == 1 and code.count("COMMIT;") == 1
    assert "DO $$" not in code and "REVOKE" not in code and "ALTER TABLE" not in code and "tenant_id" not in code


def test_table_shape_owner_comment_and_registration(db):
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            registered = await su.fetchval("SELECT count(*) AS n FROM shared.schema_versions WHERE version = 'tp-032'")
            cols = await su.fetch(
                "SELECT column_name, data_type, is_nullable FROM information_schema.columns"
                " WHERE table_schema = 'tripplanner' AND table_name = 'ai_session_month' ORDER BY ordinal_position")
            pk = await su.fetchval(
                "SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint WHERE conname = 'ai_session_month_pkey'")
            checks = await su.fetch(
                f"SELECT conname FROM pg_constraint WHERE conrelid = '{TABLE}'::regclass AND contype = 'c'"
                " ORDER BY conname")
            owner = await su.fetchval(f"SELECT pg_get_userbyid(relowner) AS o FROM pg_class WHERE oid = '{TABLE}'::regclass")
            comment = await su.fetchval(f"SELECT obj_description('{TABLE}'::regclass, 'pg_class') AS c")
            rls = await su.fetchval(f"SELECT relrowsecurity AS r FROM pg_class WHERE oid = '{TABLE}'::regclass")
            cis = await su.fetchval(f"SELECT has_table_privilege('cis_app', '{TABLE}', 'SELECT') AS s")
            return registered, [tuple(r) for r in cols], pk, [r["conname"] for r in checks], owner, comment, rls, cis
        finally:
            await su.close()

    registered, cols, pk, checks, owner, comment, rls, cis = run(check())
    assert registered == 1
    assert cols == [("scope", "text", "NO"), ("month", "date", "NO"), ("session_id", "uuid", "NO"),
                    ("first_ai_at", "timestamp with time zone", "NO")]
    assert pk == "PRIMARY KEY (scope, month, session_id)"
    assert checks == ["ai_session_month_month_chk", "ai_session_month_scope_chk"]
    assert owner == "tp_owner" and comment.startswith("tp-032 (AA-1116)")
    assert rls is False and cis is False


def test_runtime_role_runs_the_code_statements(db):
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            async with su.transaction():
                await su.execute("SET LOCAL ROLE tripplanner")
                scope = f"tenant:{TENANT}"
                await su.execute(LOCK, scope)
                await su.execute(ADD, scope, SESSION_A)
                await su.execute(ADD, scope, SESSION_A)                 # once per month
                await su.execute(ADD, scope, SESSION_B)
                n = await su.fetchval(COUNT, scope)
                counted = await su.fetchval(COUNTED, scope, SESSION_A)
                other = await su.fetchval(COUNT, "tenant:00000000-0000-4000-8000-000000000000")
                raise _Rollback((n, counted, other))
        except _Rollback as r:
            return r.value
        finally:
            await su.close()

    assert run(check()) == (2, 1, 0)


@pytest.mark.parametrize("scope,month", [
    ("tenant:not-a-uuid", "date_trunc('month', now())::date"),
    ("app", "date_trunc('month', now())::date"),
    (f"tenant:{TENANT}", "(date_trunc('month', now()) + interval '3 days')::date"),
])
def test_checks_refuse_bad_rows(db, scope, month):
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            async with su.transaction():
                await su.execute("SET LOCAL ROLE tripplanner")
                with pytest.raises(asyncpg.CheckViolationError):
                    await su.execute(f"INSERT INTO {TABLE} (scope, month, session_id) VALUES ($1, {month}, $2::uuid)",
                                     scope, SESSION_A)
                raise _Rollback(True)
        except _Rollback as r:
            return r.value
        finally:
            await su.close()

    assert run(check())
