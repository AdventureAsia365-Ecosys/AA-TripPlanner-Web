"""tp-031 replay (AA-1093 lane L4 follow-up): the tenant erasure log, tripplanner.erase_tenant() and the CIS report view
brought to the contract tenant.erasure.v1, applied the way Migration Apply applies them (as tp_owner, after tp-013 …
tp-030; tp-031 twice).

Runs only with ``--pg-dsn`` (a SUPERUSER DSN of a throwaway LOCAL Postgres). The database is tp-030's
(``test_tp030_pg``: tp-028's forced database, tp-029, tp-030), optionally with tp-030-shaped log rows written before
tp-031, then tp-031 as tp_owner (twice), then the invented rows of TARGET (erased) and OTHER (must stay intact).

Checks: the contract shape of the file (no directive line, one BEGIN … COMMIT, no SECURITY DEFINER, no DROP TABLE /
DELETE: contract, not destructive); the log (columns, report_id key, the contract CHECKs, FORCE row-level security
and its two policies kept, the runtime role inserts and reads but never updates or deletes); the function (new
signature only, SECURITY INVOKER, settings pinned, executable by tripplanner only); the view (the exact contract
column order, SELECT to cis_app only); tp-030 rows carried over (a DRY RUN -> ok, a refusal kept, both source tp030 and
left out of the view) and the refusal when a tp-030 row of an erasure that ran exists; as the runtime role: refused
outside the system scope, the DRY RUN tables are contract entries with md5 digests recomputed here, the plan hash is
the contract formula recomputed here, binds the round, is stable under other session settings and ignores other
tenants; an apply with a stale plan is refused plan_changed and deletes nothing; an apply with the approved plan
erases the tenant only (and the customer only its sessions link) and merges the spend rows; a re-run is a no-op.
"""
from __future__ import annotations

import hashlib
import json
import re

import pytest

from test_tp013_pg import pg_dsn  # noqa: F401  (fixture of the tp-013 replay: --pg-dsn, local only)
from test_tp013_pg import MIGRATIONS, run
from test_tp025_pg import _as_owner, _drop, _in_rollback
from test_tp028_pg import forced_database
from test_tp030_pg import (CUSTOMERS, ONLY_OTHER, ONLY_TARGET, OTHER, OTHER_ROWS, SHARED, SYSTEM, TABLES, TARGET,
                           TARGET_ROWS, TP029, TP030, seed_tenant)

asyncpg = pytest.importorskip("asyncpg")

TP031 = MIGRATIONS / "031_tenant_erasure_contract.sql"
VIEW_COLUMNS = ["report_id", "request_id", "tenant_id", "app", "round", "mode", "status", "error_code", "plan_hash",
                "tables", "blockers", "census", "job_id", "reported_at"]
PLAN_TABLES = ["tripplanner.ai_spend_month", "tripplanner.customers", "tripplanner.outbox", "tripplanner.sessions",
               "tripplanner.traveller_accounts", "tripplanner.trip_drafts", "tripplanner.trip_events"]
EMPTY_MD5 = hashlib.md5(b"").hexdigest()
# Invented tp-030 log rows written before tp-031 (an operator DRY RUN and a refused apply of the same drill).
LEGACY_REQUEST = "e1e1e1e1-0000-4000-8000-0000000000f1"
LEGACY_SQL = f"""
INSERT INTO tripplanner.tenant_erasure_log (request_id, tenant_id, mode, plan_hash, counts, status, reason)
VALUES ('{LEGACY_REQUEST}', '{TARGET}', 'dry_run', '{"c" * 64}',
        '{{"tables": {{"tripplanner.sessions": {{"action": "delete", "rows": 2, "fingerprint": "{"d" * 64}"}},
                       "tripplanner.ai_spend_month": {{"action": "merge_into_app", "rows": 1, "fingerprint": null}}}},
          "deleted": {{}}, "app_rows_added": 0}}', 'planned', NULL),
       ('{LEGACY_REQUEST}', '{TARGET}', 'apply', NULL, '{{}}', 'refused', 'apply_disabled');
"""


def _code(sql: str) -> str:
    return "\n".join(ln for ln in sql.splitlines() if not ln.lstrip().startswith("--"))


async def _owner_file(admin, path) -> None:
    """``path`` as tp_owner; on an error the file's own transaction is rolled back first, so the error raised is the
    file's (``_as_owner``'s RESET ROLE would otherwise raise InFailedSQLTransactionError over it)."""
    await admin.execute("SET ROLE tp_owner")
    try:
        await admin.execute(path.read_text(encoding="utf-8"))
    except asyncpg.PostgresError:
        await admin.execute("ROLLBACK")
        raise
    finally:
        await admin.execute("RESET ROLE")


def erasure_database(pg_dsn, *, seed: bool = True, legacy_sql: str = ""):  # noqa: F811
    """tp-028's forced database, tp-029 and tp-030 as tp_owner, ``legacy_sql`` (tp-030-shaped log rows, as the
    superuser), tp-031 twice, then TARGET and OTHER rows. Returns (context, the first error or None); the caller drops
    ``context["name"]``. Used by the backend jobs tests too."""
    ctx, error = forced_database(pg_dsn)
    if error:
        return ctx, error

    async def apply():
        admin = await asyncpg.connect(ctx["admin_dsn"])
        try:
            for f in (TP029, TP030):
                await _as_owner(admin, f)
            if legacy_sql:
                su = await asyncpg.connect(ctx["su_dsn"])
                try:
                    await su.execute(legacy_sql)
                finally:
                    await su.close()
            for f in (TP031, TP031):
                await _owner_file(admin, f)
        finally:
            await admin.close()
        if seed:
            su = await asyncpg.connect(ctx["su_dsn"])
            try:
                for i, (cid, name) in enumerate(CUSTOMERS.items()):
                    await su.execute("INSERT INTO tripplanner.customers (id, name, email, created_at, contact_consent) "
                                     "VALUES ($1::uuid, $2, $3, now(), false)", cid, name, f"traveller{i}@example.test")
                await seed_tenant(su, TARGET, TARGET_ROWS, spend_usd=0.25, customers=(ONLY_TARGET, SHARED))
                await seed_tenant(su, OTHER, OTHER_ROWS, spend_usd=0.5, customers=(SHARED, ONLY_OTHER))
                await su.execute("INSERT INTO tripplanner.ai_spend_month (stage, scope, month, cost_usd, calls) "
                                 "VALUES ('tp_plan_chat', $1, DATE '2026-09-01', 0.1, 1)", f"tenant:{TARGET}")
            finally:
                await su.close()

    try:
        run(apply())
    except asyncpg.PostgresError as e:
        return ctx, f"{type(e).__name__}: {e}"
    return ctx, None


@pytest.fixture(scope="module")
def db(pg_dsn):  # noqa: F811
    ctx, error = erasure_database(pg_dsn, legacy_sql=LEGACY_SQL)
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


async def _erase(conn, tenant, round_, dry_run, expect=None) -> dict:
    return json.loads(await conn.fetchval("SELECT tripplanner.erase_tenant($1::uuid, $2, $3, $4)::text AS r",
                                          tenant, round_, dry_run, expect))


async def _tenant_counts(conn, tenant) -> dict:
    out = {t: await conn.fetchval(f"SELECT count(*) AS n FROM tripplanner.{t} WHERE tenant_id = $1::uuid", tenant)
           for t in TABLES}
    out["spend"] = await conn.fetchval("SELECT count(*) AS n FROM tripplanner.ai_spend_month WHERE scope = $1",
                                       f"tenant:{tenant}")
    return out


def contract_plan_hash(tenant: str, round_: int, tables: list) -> str:
    """The contract formula, written independently of the SQL (also Booking's plan_hash)."""
    lines = sorted(f"{t['table']}|{t['action']}|{t['rows']}|{t['digest'] or ''}" for t in tables)
    return hashlib.sha256("\n".join([f"tenant.erasure.v1|{tenant}|{round_}", *lines]).encode()).hexdigest()


def contract_digest(lines: list) -> str:
    return hashlib.md5("\n".join(sorted(lines)).encode()).hexdigest()


# ── the file ─────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_file_is_a_contract_file_in_one_transaction_block():
    text = TP031.read_text(encoding="utf-8")
    assert not [ln for ln in text.splitlines() if re.match(r"^\s*--\s*migrate:", ln)]
    code = _code(text)
    assert code.count("BEGIN;") == 1 and code.count("COMMIT;") == 1
    assert "ON CONFLICT (version) DO NOTHING" in code and "'tp-031'" in code
    assert not re.search(r"\bSECURITY\s+DEFINER\b", code)
    assert not re.search(r"\bDROP\s+(TABLE|SCHEMA|COLUMN|MATERIALIZED)\b", code, re.I), "contract, not destructive"
    assert not re.search(r"\bDELETE\s+FROM\s+tripplanner\.tenant_erasure_log\b", code, re.I)
    assert not re.search(r"\bTRUNCATE\s+(TABLE\s+)?tripplanner\.", code, re.I)
    assert not re.search(r"\bTO\s+PUBLIC\b", code.upper())
    settings = re.findall(r"current_setting\s*\(\s*'app\.tenant_id'", code)
    wrapped = re.findall(r"NULLIF\s*\(\s*current_setting\s*\(\s*'app\.tenant_id'\s*,\s*true\s*\)\s*,\s*''\s*\)", code)
    assert settings and len(settings) == len(wrapped)


def test_log_function_and_view_shape(db):
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            cols = {r["column_name"]: (r["data_type"], r["is_nullable"]) for r in await su.fetch(
                "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
                "WHERE table_schema = 'tripplanner' AND table_name = 'tenant_erasure_log'")}
            pk = await su.fetchval("SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint "
                                   "WHERE conname = 'tenant_erasure_log_pkey'")
            checks = sorted(r["conname"] for r in await su.fetch(
                "SELECT conname FROM pg_constraint WHERE conrelid = 'tripplanner.tenant_erasure_log'::regclass "
                "AND contype = 'c'"))
            rls = await su.fetchrow("SELECT relrowsecurity AS r, relforcerowsecurity AS f FROM pg_class "
                                    "WHERE oid = 'tripplanner.tenant_erasure_log'::regclass")
            policies = [r["policyname"] for r in await su.fetch(
                "SELECT policyname FROM pg_policies WHERE schemaname = 'tripplanner' "
                "AND tablename = 'tenant_erasure_log' ORDER BY policyname")]
            fns = [r["sig"] for r in await su.fetch(
                "SELECT p.oid::regprocedure::text AS sig FROM pg_proc p "
                "WHERE p.pronamespace = 'tripplanner'::regnamespace AND p.proname = 'erase_tenant'")]
            fn = await su.fetchrow("SELECT p.prosecdef AS definer, p.proconfig AS config, "
                                   "has_function_privilege('tripplanner', p.oid, 'EXECUTE') AS tp, "
                                   "has_function_privilege('cis_app', p.oid, 'EXECUTE') AS cis "
                                   "FROM pg_proc p WHERE p.oid = "
                                   "'tripplanner.erase_tenant(uuid, integer, boolean, text)'::regprocedure")
            view_cols = [r["attname"] for r in await su.fetch(
                "SELECT attname FROM pg_attribute WHERE attrelid = 'tripplanner.v_tenant_erasure_report_v1'::regclass "
                "AND attnum > 0 AND NOT attisdropped ORDER BY attnum")]
            privs = {}
            for role in ("tripplanner", "cis_app", "booking"):
                for obj in ("tripplanner.tenant_erasure_log", "tripplanner.v_tenant_erasure_report_v1"):
                    for p in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                        privs[(role, obj.split(".")[1], p)] = await su.fetchval(
                            "SELECT has_table_privilege($1, $2, $3) AS ok", role, obj, p)
            registered = await su.fetchval("SELECT count(*) AS n FROM shared.schema_versions WHERE version = 'tp-031'")
            return cols, pk, checks, rls, policies, fns, fn, view_cols, privs, registered
        finally:
            await su.close()

    cols, pk, checks, rls, policies, fns, fn, view_cols, privs, registered = run(check())
    assert {c: cols[c] for c in VIEW_COLUMNS} == {
        "report_id": ("uuid", "NO"), "request_id": ("uuid", "NO"), "tenant_id": ("uuid", "NO"), "app": ("text", "NO"),
        "round": ("integer", "NO"), "mode": ("text", "NO"), "status": ("text", "NO"), "error_code": ("text", "YES"),
        "plan_hash": ("text", "YES"), "tables": ("jsonb", "NO"), "blockers": ("jsonb", "NO"),
        "census": ("jsonb", "YES"), "job_id": ("uuid", "YES"), "reported_at": ("timestamp with time zone", "NO")}
    assert set(cols) == {*VIEW_COLUMNS, "source", "legacy_updated_at"}
    assert cols["source"] == ("text", "NO") and cols["legacy_updated_at"] == ("timestamp with time zone", "YES")
    assert pk == "PRIMARY KEY (report_id)"
    assert checks == sorted(f"tenant_erasure_log_{c}_chk" for c in
                            ("app", "round", "mode", "status", "code", "hash", "json", "census", "source"))
    assert (rls["r"], rls["f"]) == (True, True)
    assert policies == ["tenant_erasure_log_owner_read", "tenant_erasure_log_system"]
    assert fns == ["tripplanner.erase_tenant(uuid,integer,boolean,text)"]       # the tp-030 signature is gone
    assert (fn["definer"], fn["tp"], fn["cis"]) == (False, True, False)
    assert sorted(fn["config"]) == sorted(["search_path=pg_catalog, pg_temp", "TimeZone=UTC", "DateStyle=ISO, YMD",
                                           "IntervalStyle=postgres", "extra_float_digits=3", "bytea_output=hex"])
    assert view_cols == VIEW_COLUMNS
    assert {k: v for k, v in privs.items() if v} == {
        ("tripplanner", "tenant_erasure_log", "SELECT"): True, ("tripplanner", "tenant_erasure_log", "INSERT"): True,
        ("cis_app", "v_tenant_erasure_report_v1", "SELECT"): True}
    assert registered == 1


def test_tp030_rows_are_carried_over_and_kept_out_of_the_view(db):
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            rows = [dict(r) for r in await su.fetch(
                "SELECT mode, status, error_code, plan_hash, round, app, source, tables::text AS tables, "
                "blockers::text AS blockers, census, report_id IS NOT NULL AS has_id, legacy_updated_at IS NOT NULL "
                "AS has_legacy FROM tripplanner.tenant_erasure_log WHERE request_id = $1::uuid ORDER BY mode",
                LEGACY_REQUEST)]
            async with su.transaction():
                await su.execute("SET LOCAL ROLE cis_app")
                in_view = await su.fetchval("SELECT count(*) AS n FROM tripplanner.v_tenant_erasure_report_v1 "
                                            "WHERE request_id = $1::uuid", LEGACY_REQUEST)
            return rows, in_view
        finally:
            await su.close()

    rows, in_view = run(check())
    apply, dry = rows
    assert (dry["mode"], dry["status"], dry["error_code"], dry["plan_hash"]) == ("dry_run", "ok", None, "c" * 64)
    assert json.loads(dry["tables"]) == [
        {"table": "tripplanner.ai_spend_month", "action": "merge", "rows": 1, "digest": None},
        {"table": "tripplanner.sessions", "action": "delete", "rows": 2, "digest": None}]
    assert (apply["mode"], apply["status"], apply["error_code"], apply["plan_hash"]) == \
        ("apply", "refused", "apply_disabled", None)
    for r in rows:
        assert (r["round"], r["app"], r["source"], r["blockers"], r["census"], r["has_id"], r["has_legacy"]) == \
            (1, "tripplanner", "tp030", "[]", None, True, True)
    assert in_view == 0


def test_a_tp030_row_of_an_erasure_that_ran_stops_the_file(pg_dsn):  # noqa: F811
    applied = LEGACY_SQL.replace("'refused', 'apply_disabled'", "'applied', NULL")
    ctx, error = erasure_database(pg_dsn, seed=False, legacy_sql=applied)
    try:
        assert error is not None and "erasure that ran" in error

        async def state():
            su = await asyncpg.connect(ctx["su_dsn"])
            try:
                return (await su.fetchval("SELECT count(*) AS n FROM shared.schema_versions WHERE version = 'tp-031'"),
                        await su.fetchval("SELECT count(*) AS n FROM pg_attribute WHERE attrelid = "
                                          "'tripplanner.tenant_erasure_log'::regclass AND attname = 'counts'"))
            finally:
                await su.close()

        assert run(state()) == (0, 1)                                 # nothing changed, tp-031 not registered
    finally:
        _drop(pg_dsn, ctx["name"])


# ── the function, as the runtime role ────────────────────────────────────────────────────────────────────────────────

def test_function_refuses_outside_the_system_scope(db):
    async def body(conn):
        try:
            async with conn.transaction():
                await _erase(conn, TARGET, 1, True)
            return "ran"
        except asyncpg.InsufficientPrivilegeError:
            return "refused"

    assert run(_runtime(db, body)) == "refused"


def test_dry_run_is_contract_shaped_with_digests_and_the_hash_recomputed_here(db):
    async def body(conn):
        await conn.execute(SYSTEM)
        before = await _tenant_counts(conn, TARGET)
        plan = await _erase(conn, TARGET, 1, True)
        again = await _erase(conn, TARGET, 1, True)
        round2 = await _erase(conn, TARGET, 2, True)
        sessions = [r["l"] for r in await conn.fetch(
            "SELECT id::text || '|' AS l FROM tripplanner.sessions WHERE tenant_id = $1::uuid", TARGET)]
        drafts = [r["l"] for r in await conn.fetch(
            "SELECT id::text || '|' || to_char(updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"') AS l "
            "FROM tripplanner.trip_drafts WHERE tenant_id = $1::uuid", TARGET)]
        spend = [r["l"] for r in await conn.fetch(
            "SELECT stage || ',' || scope || ',' || to_char(month, 'YYYY-MM-DD') || '|' "
            "|| to_char(updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"') AS l "
            "FROM tripplanner.ai_spend_month WHERE scope = $1", f"tenant:{TARGET}")]
        return before, plan, again, round2, sessions, drafts, spend, await _tenant_counts(conn, TARGET)

    before, plan, again, round2, sessions, drafts, spend, after = run(_runtime(db, body))
    assert after == before == {**TARGET_ROWS, "spend": 2}
    assert plan == again and (plan["status"], plan["error_code"], plan["round"]) == ("ok", None, 1)
    assert [t["table"] for t in plan["tables"]] == PLAN_TABLES                     # sorted by table
    by = {t["table"]: t for t in plan["tables"]}
    assert {k: (v["action"], v["rows"]) for k, v in by.items()} == {
        "tripplanner.sessions": ("delete", 2), "tripplanner.trip_drafts": ("delete", 2),
        "tripplanner.trip_events": ("delete", 3), "tripplanner.outbox": ("delete", 1),
        "tripplanner.customers": ("delete", 1), "tripplanner.ai_spend_month": ("merge", 2),
        "tripplanner.traveller_accounts": ("keep", 0)}
    for t in plan["tables"]:
        assert set(t) == {"table", "action", "rows", "digest"}
        assert (t["digest"] is None) == (t["action"] == "keep")
        assert t["digest"] is None or re.fullmatch(r"[0-9a-f]{32}", t["digest"])
    assert by["tripplanner.sessions"]["digest"] == contract_digest(sessions)
    assert by["tripplanner.trip_drafts"]["digest"] == contract_digest(drafts)
    assert by["tripplanner.ai_spend_month"]["digest"] == contract_digest(spend)
    assert plan["plan_hash"] == contract_plan_hash(TARGET, 1, plan["tables"])
    assert round2["plan_hash"] == contract_plan_hash(TARGET, 2, round2["tables"]) != plan["plan_hash"]
    text = json.dumps(plan)
    assert "Lantern" not in text and "example.test" not in text and not [n for n in CUSTOMERS.values() if n in text]


@pytest.mark.parametrize("change", [
    "UPDATE tripplanner.trip_drafts SET updated_at = updated_at + interval '1 second' WHERE tenant_id = '{t}'",
    "INSERT INTO tripplanner.trip_events (trip_id, session_id, event_type, payload, tenant_id) "
    "SELECT trip_id, session_id, 'reorder', '{{}}', tenant_id FROM tripplanner.trip_events WHERE tenant_id = '{t}' LIMIT 1",
    "UPDATE tripplanner.ai_spend_month SET updated_at = updated_at + interval '1 second' WHERE scope = 'tenant:{t}'",
    f"UPDATE tripplanner.sessions SET customer_id = NULL WHERE customer_id = '{ONLY_TARGET}'",
])
def test_plan_hash_changes_when_the_planned_rows_change(db, change):
    async def body(conn):
        await conn.execute(SYSTEM)
        before = (await _erase(conn, TARGET, 1, True))["plan_hash"]
        await conn.execute(change.format(t=TARGET))
        return before, (await _erase(conn, TARGET, 1, True))["plan_hash"]

    before, after = run(_runtime(db, body))
    assert before != after


def test_plan_hash_ignores_other_tenants_and_session_settings(db):
    async def body(conn):
        await conn.execute(SYSTEM)
        before = (await _erase(conn, TARGET, 1, True))["plan_hash"]
        await conn.execute("UPDATE tripplanner.trip_drafts SET updated_at = now() WHERE tenant_id = $1::uuid", OTHER)
        await conn.execute("UPDATE tripplanner.ai_spend_month SET calls = calls + 5 WHERE scope = 'app'")
        await conn.execute("SET TimeZone = 'Asia/Ho_Chi_Minh'")
        await conn.execute("SET DateStyle = 'SQL, DMY'")
        after = (await _erase(conn, TARGET, 1, True))["plan_hash"]
        return before, after, (await _erase(conn, OTHER, 1, True))["plan_hash"], await conn.fetchval("SHOW TimeZone")

    before, after, other, tz = run(_runtime(db, body))
    assert before == after and other != before and tz == "Asia/Ho_Chi_Minh"


def test_apply_needs_a_plan_and_refuses_a_stale_or_other_round_one(db):
    async def body(conn):
        await conn.execute(SYSTEM)
        try:
            async with conn.transaction():
                await _erase(conn, TARGET, 1, False)
            missing = "ran"
        except asyncpg.InvalidParameterValueError:
            missing = "refused"
        round1 = (await _erase(conn, TARGET, 1, True))["plan_hash"]
        other_round = await _erase(conn, TARGET, 2, False, round1)          # the round-1 hash does not apply round 2
        await conn.execute("UPDATE tripplanner.trip_drafts SET updated_at = now() WHERE tenant_id = $1::uuid", TARGET)
        stale = await _erase(conn, TARGET, 1, False, round1)
        return missing, other_round, stale, await _tenant_counts(conn, TARGET)

    missing, other_round, stale, counts = run(_runtime(db, body))
    assert missing == "refused"
    for refused in (other_round, stale):
        assert (refused["status"], refused["error_code"]) == ("refused", "plan_changed")
    assert counts == {**TARGET_ROWS, "spend": 2}


def test_apply_with_the_approved_plan_erases_the_tenant_only_and_merges_its_spend(db):
    async def body(conn):
        await conn.execute(SYSTEM)
        other_before = await _tenant_counts(conn, OTHER)
        app_before = {r["month"]: (float(r["cost_usd"]), r["calls"]) for r in await conn.fetch(
            "SELECT month, cost_usd, calls FROM tripplanner.ai_spend_month WHERE scope = 'app'")}
        plan = await _erase(conn, TARGET, 1, True)
        result = await _erase(conn, TARGET, 1, False, plan["plan_hash"])
        app_after = {r["month"]: (float(r["cost_usd"]), r["calls"]) for r in await conn.fetch(
            "SELECT month, cost_usd, calls FROM tripplanner.ai_spend_month WHERE scope = 'app'")}
        verify = await _erase(conn, TARGET, 1, True)
        customers = [r["id"] for r in await conn.fetch(
            "SELECT id::text AS id FROM tripplanner.customers WHERE id = ANY($1::uuid[]) ORDER BY id", list(CUSTOMERS))]
        return (plan, result, await _tenant_counts(conn, TARGET), other_before, await _tenant_counts(conn, OTHER),
                app_before, app_after, verify, customers)

    plan, result, target_after, other_before, other_after, app_before, app_after, verify, customers = \
        run(_runtime(db, body))
    assert (result["status"], result["error_code"], result["plan_hash"]) == ("ok", None, plan["plan_hash"])
    assert result["tables"] == plan["tables"]                    # each statement removed exactly its planned rows
    assert result["app_rows_added"] == 1
    assert target_after == dict.fromkeys([*TABLES, "spend"], 0)
    assert other_after == other_before == {**OTHER_ROWS, "spend": 1}
    assert customers == sorted([SHARED, ONLY_OTHER])
    sept = [m for m in app_after if m.isoformat() == "2026-09-01"]
    assert len(sept) == 1 and app_after[sept[0]] == (0.1, 1)
    assert {m: v for m, v in app_after.items() if m not in sept} == app_before
    assert {t["rows"] for t in verify["tables"]} == {0}
    assert {t["digest"] for t in verify["tables"] if t["action"] != "keep"} == {EMPTY_MD5}


def test_runtime_inserts_reports_but_never_updates_or_deletes_them(db):
    async def body(conn):
        await conn.execute(SYSTEM)
        rid = await conn.fetchval(
            "INSERT INTO tripplanner.tenant_erasure_log (request_id, tenant_id, round, mode, status, plan_hash, "
            "tables, source) VALUES ($1::uuid, $2::uuid, 1, 'dry_run', 'ok', $3, '[]', 'operator') "
            "RETURNING report_id::text", LEGACY_REQUEST, TARGET, "a" * 64)
        out = {}
        for name, sql in (
                ("update", "UPDATE tripplanner.tenant_erasure_log SET status = 'failed' WHERE report_id = $1::uuid"),
                ("delete", "DELETE FROM tripplanner.tenant_erasure_log WHERE report_id = $1::uuid")):
            try:
                async with conn.transaction():
                    await conn.execute(sql, rid)
                out[name] = "ran"
            except asyncpg.InsufficientPrivilegeError:
                out[name] = "refused"
        for name, sql in (
                ("ok_with_code", "INSERT INTO tripplanner.tenant_erasure_log (request_id, tenant_id, round, mode, "
                                 "status, error_code, plan_hash, source) VALUES ($1::uuid, $2::uuid, 1, 'dry_run', "
                                 "'ok', 'x_code', '" + "a" * 64 + "', 'operator')"),
                ("verify_ok_without_census", "INSERT INTO tripplanner.tenant_erasure_log (request_id, tenant_id, round, "
                                             "mode, status, source) VALUES ($1::uuid, $2::uuid, 1, 'verify', 'ok', "
                                             "'cis')"),
                ("round_zero", "INSERT INTO tripplanner.tenant_erasure_log (request_id, tenant_id, round, mode, status, "
                               "error_code, source) VALUES ($1::uuid, $2::uuid, 0, 'apply', 'refused', 'x_code', "
                               "'cis')")):
            try:
                async with conn.transaction():
                    await conn.execute(sql, LEGACY_REQUEST, TARGET)
                out[name] = "ran"
            except asyncpg.CheckViolationError:
                out[name] = "refused"
        return out

    assert set(run(_runtime(db, body)).values()) == {"refused"}


def test_rerun_as_tp_owner_is_a_no_op(db):
    async def check():
        admin = await asyncpg.connect(db["admin_dsn"])
        try:
            first = await admin.fetchval("SELECT applied_at FROM shared.schema_versions WHERE version = 'tp-031'")
            rows = await admin.fetchval("SELECT count(*) AS n FROM tripplanner.tenant_erasure_log")
            await _as_owner(admin, TP031)
            return (first, rows, await admin.fetchval("SELECT applied_at FROM shared.schema_versions "
                                                      "WHERE version = 'tp-031'"),
                    await admin.fetchval("SELECT count(*) AS n FROM tripplanner.tenant_erasure_log"))
        finally:
            await admin.close()

    first, rows, after, rows_after = run(check())
    assert after == first and rows_after == rows == 2
