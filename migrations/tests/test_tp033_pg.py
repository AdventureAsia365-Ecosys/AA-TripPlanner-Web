"""tp-033 replay (AA-1116, review fix): tripplanner.erase_tenant() (tenant.erasure.v1, tp-031) also deletes the
tenant's rows of tripplanner.ai_session_month (tp-032), applied the way Migration Apply applies it (as tp_owner, after
tp-013 … tp-032; tp-033 twice).

Runs only with ``--pg-dsn`` (a SUPERUSER DSN of a throwaway LOCAL Postgres). The database is tp-031's
(``test_tp031_pg.erasure_database``: TARGET (erased) and OTHER (must stay intact) seeded), then tp-032 and tp-033 as
tp_owner (twice), then invented ai_session_month rows: two sessions of TARGET (two months) and one of OTHER.

Checks: the contract shape of the file (no directive line, one BEGIN … COMMIT, no SECURITY DEFINER, no DROP /
TRUNCATE / DELETE outside the function body, the tenant setting read through NULLIF); the function keeps tp-031's
signature, SECURITY INVOKER, pinned settings and EXECUTE to tripplanner only, and the runtime role may delete from the
table; it refuses to run before tp-032; as the runtime role under the system scope: the DRY RUN carries a delete entry
for ai_session_month with the contract md5 digest recomputed here and the contract plan hash; one more session row of
the tenant changes the hash (an older plan is refused plan_changed, nothing deleted); an apply with the approved plan
deletes the tenant's ai_session_month rows only (OTHER's stay) with the rest of tp-031's erase; a re-run is a no-op.
Names are invented.
"""
from __future__ import annotations

import re

import pytest

from test_tp013_pg import pg_dsn  # noqa: F401  (fixture of the tp-013 replay: --pg-dsn, local only)
from test_tp013_pg import MIGRATIONS, run
from test_tp025_pg import _drop, _in_rollback
from test_tp030_pg import OTHER, OTHER_ROWS, SYSTEM, TABLES, TARGET, TARGET_ROWS
from test_tp031_pg import (EMPTY_MD5, PLAN_TABLES, _code, _erase, _owner_file, _tenant_counts, contract_digest,
                           contract_plan_hash, erasure_database)

asyncpg = pytest.importorskip("asyncpg")

TP032 = MIGRATIONS / "032_ai_session_month.sql"
TP033 = MIGRATIONS / "033_erase_tenant_ai_session_month.sql"
TABLE = "tripplanner.ai_session_month"
PLAN_TABLES_033 = sorted([*PLAN_TABLES, TABLE])
# Invented planner sessions counted for the quota: two months of TARGET, one session of OTHER.
SESSION_ROWS = [
    (f"tenant:{TARGET}", "2026-10-01", "c3c3c3c3-0000-4000-8000-0000000000a1"),
    (f"tenant:{TARGET}", "2026-11-01", "c3c3c3c3-0000-4000-8000-0000000000a2"),
    (f"tenant:{OTHER}", "2026-11-01", "c3c3c3c3-0000-4000-8000-0000000000b1"),
]
ADD_SQL = f"INSERT INTO {TABLE} (scope, month, session_id) VALUES ($1, $2::text::date, $3::uuid)"


def _database(pg_dsn, *, with_032: bool = True):  # noqa: F811
    """tp-031's seeded database, then tp-032 (unless ``with_032`` is false) and tp-033 twice as tp_owner, then the
    session rows. Returns (context, the first error or None); the caller drops ``context["name"]``."""
    ctx, error = erasure_database(pg_dsn)
    if error:
        return ctx, error

    async def apply():
        admin = await asyncpg.connect(ctx["admin_dsn"])
        try:
            for f in ((TP032, TP032) if with_032 else ()) + (TP033, TP033):
                await _owner_file(admin, f)
        finally:
            await admin.close()
        su = await asyncpg.connect(ctx["su_dsn"])
        try:
            for row in SESSION_ROWS:
                await su.execute(ADD_SQL, *row)
        finally:
            await su.close()

    try:
        run(apply())
    except asyncpg.PostgresError as e:
        return ctx, f"{type(e).__name__}: {e}"
    return ctx, None


@pytest.fixture(scope="module")
def db(pg_dsn):  # noqa: F811
    ctx, error = _database(pg_dsn)
    try:
        assert error is None, error
        yield ctx
    finally:
        _drop(pg_dsn, ctx["name"])


async def _runtime(db, body):
    tp = await asyncpg.connect(db["tp_dsn"])
    try:
        return await _in_rollback(tp, body)
    finally:
        await tp.close()


async def _sessions(conn, tenant) -> int:
    return await conn.fetchval(f"SELECT count(*) AS n FROM {TABLE} WHERE scope = $1", f"tenant:{tenant}")


# ── the file ─────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_file_is_a_contract_file_in_one_transaction_block():
    text = TP033.read_text(encoding="utf-8")
    assert not [ln for ln in text.splitlines() if re.match(r"^\s*--\s*migrate:", ln)]
    code = _code(text)
    assert code.count("BEGIN;") == 1 and code.count("COMMIT;") == 1
    assert "ON CONFLICT (version) DO NOTHING" in code and "'tp-033'" in code
    assert not re.search(r"\bSECURITY\s+DEFINER\b", code)
    assert not re.search(r"\bDROP\s+(TABLE|SCHEMA|COLUMN|MATERIALIZED|FUNCTION)\b", code, re.I)
    assert not re.search(r"\bTRUNCATE\b", code, re.I)
    outside = code.split("$fn$")[0] + code.split("$fn$")[-1]                        # the file minus the function body
    assert not re.search(r"\bDELETE\s+FROM\b", outside, re.I)
    assert not re.search(r"\bTO\s+PUBLIC\b", code.upper())
    settings = re.findall(r"current_setting\s*\(\s*'app\.tenant_id'", code)
    wrapped = re.findall(r"NULLIF\s*\(\s*current_setting\s*\(\s*'app\.tenant_id'\s*,\s*true\s*\)\s*,\s*''\s*\)", code)
    assert settings and len(settings) == len(wrapped)


def test_function_shape_grants_and_registration(db):
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            fns = [r["sig"] for r in await su.fetch(
                "SELECT p.oid::regprocedure::text AS sig FROM pg_proc p "
                "WHERE p.pronamespace = 'tripplanner'::regnamespace AND p.proname = 'erase_tenant'")]
            fn = await su.fetchrow("SELECT p.prosecdef AS definer, p.proconfig AS config, "
                                   "has_function_privilege('tripplanner', p.oid, 'EXECUTE') AS tp, "
                                   "has_function_privilege('cis_app', p.oid, 'EXECUTE') AS cis, "
                                   "obj_description(p.oid, 'pg_proc') AS comment "
                                   "FROM pg_proc p WHERE p.oid = "
                                   "'tripplanner.erase_tenant(uuid, integer, boolean, text)'::regprocedure")
            delete = await su.fetchval(f"SELECT has_table_privilege('tripplanner', '{TABLE}', 'DELETE') AS ok")
            registered = await su.fetchval("SELECT count(*) AS n FROM shared.schema_versions WHERE version = 'tp-033'")
            return fns, fn, delete, registered
        finally:
            await su.close()

    fns, fn, delete, registered = run(check())
    assert fns == ["tripplanner.erase_tenant(uuid,integer,boolean,text)"]
    assert (fn["definer"], fn["tp"], fn["cis"]) == (False, True, False)
    assert sorted(fn["config"]) == sorted(["search_path=pg_catalog, pg_temp", "TimeZone=UTC", "DateStyle=ISO, YMD",
                                           "IntervalStyle=postgres", "extra_float_digits=3", "bytea_output=hex"])
    assert "ai_session_month" in fn["comment"] and fn["comment"].startswith("tp-031, tp-033")
    assert delete is True and registered == 1


def test_file_refuses_to_run_before_tp032(pg_dsn):  # noqa: F811
    ctx, error = _database(pg_dsn, with_032=False)
    try:
        assert error is not None and "tp-032" in error

        async def state():
            su = await asyncpg.connect(ctx["su_dsn"])
            try:
                return await su.fetchval("SELECT count(*) AS n FROM shared.schema_versions WHERE version = 'tp-033'")
            finally:
                await su.close()

        assert run(state()) == 0
    finally:
        _drop(pg_dsn, ctx["name"])


# ── the function, as the runtime role ────────────────────────────────────────────────────────────────────────────────

def test_dry_run_plans_the_tenant_session_rows_with_the_contract_digest(db):
    async def body(conn):
        await conn.execute(SYSTEM)
        plan = await _erase(conn, TARGET, 1, True)
        lines = [r["l"] for r in await conn.fetch(
            f"SELECT scope || ',' || to_char(month, 'YYYY-MM-DD') || ',' || session_id::text || '|' AS l "
            f"FROM {TABLE} WHERE scope = $1", f"tenant:{TARGET}")]
        return plan, lines, await _sessions(conn, TARGET)

    plan, lines, left = run(_runtime(db, body))
    assert left == 2 and len(lines) == 2
    assert [t["table"] for t in plan["tables"]] == PLAN_TABLES_033                 # sorted by table
    entry = next(t for t in plan["tables"] if t["table"] == TABLE)
    assert (entry["action"], entry["rows"]) == ("delete", 2)
    assert entry["digest"] == contract_digest(lines) != EMPTY_MD5
    assert plan["plan_hash"] == contract_plan_hash(TARGET, 1, plan["tables"])
    other = next(t for t in run(_runtime(db, _dry(OTHER)))["tables"] if t["table"] == TABLE)
    assert other["rows"] == 1


def _dry(tenant):
    async def body(conn):
        await conn.execute(SYSTEM)
        return await _erase(conn, tenant, 1, True)
    return body


def test_a_new_session_row_changes_the_plan_and_an_older_plan_deletes_nothing(db):
    async def body(conn):
        await conn.execute(SYSTEM)
        before = (await _erase(conn, TARGET, 1, True))["plan_hash"]
        await conn.execute(ADD_SQL, f"tenant:{TARGET}", "2026-11-01", "c3c3c3c3-0000-4000-8000-0000000000a3")
        stale = await _erase(conn, TARGET, 1, False, before)
        return before, stale, await _sessions(conn, TARGET), await _tenant_counts(conn, TARGET)

    before, stale, left, counts = run(_runtime(db, body))
    assert (stale["status"], stale["error_code"]) == ("refused", "plan_changed") and stale["plan_hash"] != before
    assert left == 3 and counts == {**TARGET_ROWS, "spend": 2}


def test_apply_with_the_approved_plan_deletes_the_tenant_sessions_only(db):
    async def body(conn):
        await conn.execute(SYSTEM)
        plan = await _erase(conn, TARGET, 1, True)
        result = await _erase(conn, TARGET, 1, False, plan["plan_hash"])
        verify = await _erase(conn, TARGET, 1, True)
        return (plan, result, verify, await _sessions(conn, TARGET), await _sessions(conn, OTHER),
                await _tenant_counts(conn, TARGET), await _tenant_counts(conn, OTHER))

    plan, result, verify, target_left, other_left, target_after, other_after = run(_runtime(db, body))
    assert (result["status"], result["error_code"], result["plan_hash"]) == ("ok", None, plan["plan_hash"])
    assert result["tables"] == plan["tables"]                    # each statement removed exactly its planned rows
    assert (target_left, other_left) == (0, 1)
    assert target_after == dict.fromkeys([*TABLES, "spend"], 0)
    assert other_after == {**OTHER_ROWS, "spend": 1}
    entry = next(t for t in verify["tables"] if t["table"] == TABLE)
    assert (entry["rows"], entry["digest"]) == (0, EMPTY_MD5)


def test_rerun_as_tp_owner_is_a_no_op(db):
    async def check():
        admin = await asyncpg.connect(db["admin_dsn"])
        try:
            first = await admin.fetchval("SELECT applied_at FROM shared.schema_versions WHERE version = 'tp-033'")
            src = await admin.fetchval("SELECT prosrc FROM pg_proc WHERE oid = "
                                       "'tripplanner.erase_tenant(uuid, integer, boolean, text)'::regprocedure")
            await _owner_file(admin, TP033)
            return (first, src,
                    await admin.fetchval("SELECT applied_at FROM shared.schema_versions WHERE version = 'tp-033'"),
                    await admin.fetchval("SELECT prosrc FROM pg_proc WHERE oid = "
                                         "'tripplanner.erase_tenant(uuid, integer, boolean, text)'::regprocedure"))
        finally:
            await admin.close()

    first, src, after, src_after = run(check())
    assert after == first and src_after == src and "ai_session_month" in src
