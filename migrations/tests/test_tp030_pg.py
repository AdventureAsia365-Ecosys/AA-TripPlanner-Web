"""tp-030 replay (AA-1093 lane L4): the tenant erasure log, tripplanner.erase_tenant() and the CIS report view, applied
the way Migration Apply applies them (as tp_owner, after tp-013 … tp-029; tp-030 twice).

Runs only with ``--pg-dsn`` (a SUPERUSER DSN of a throwaway LOCAL Postgres). The database is tp-028's forced database
(``test_tp028_pg.forced_database``: invented demo rows from before tp-025), then tp-029 and tp-030 as tp_owner, then
invented rows of two more tenants: TARGET (the one erased) and OTHER (must stay intact).

Checks: the contract shape (no directive line, one BEGIN … COMMIT, no SECURITY DEFINER); the log (columns, key, FORCE
row-level security, exactly its two policies, the runtime role cannot delete), the function (SECURITY INVOKER,
executable by tripplanner only, the session settings that shape row text pinned), the view (columns, SELECT to cis_app
only, the runtime role cannot read it); as the runtime role: the function refuses outside the system scope and under a
bound tenant; the DRY RUN plan hash is stable, equal under two session TimeZone / DateStyle settings, ignores other
tenants and changes when the tenant's data changes; an apply without a plan or with a stale plan deletes nothing; an
apply with the approved plan removes every row of the tenant and the customers only its sessions link (a customer
another tenant's session links stays), leaves the other tenants intact and merges the spend rows into the app scope;
cis_app reads the report view and not the table; a re-run is a no-op.
"""
from __future__ import annotations

import json
import re
import uuid

import pytest

from test_tp013_pg import pg_dsn  # noqa: F401  (fixture of the tp-013 replay: --pg-dsn, local only)
from test_tp013_pg import MIGRATIONS, run
from test_tp025_pg import _as_owner, _drop, _in_rollback
from test_tp028_pg import forced_database

asyncpg = pytest.importorskip("asyncpg")

TP029 = MIGRATIONS / "029_ai_spend_month.sql"
TP030 = MIGRATIONS / "030_tenant_erasure.sql"
TARGET = "a7a7a7a7-0000-4000-8000-0000000000e1"        # invented tenant that is erased
OTHER = "b8b8b8b8-0000-4000-8000-0000000000e2"         # invented tenant that must stay intact
TABLES = ("sessions", "trip_drafts", "trip_events", "outbox")
TARGET_ROWS = {"sessions": 2, "trip_drafts": 2, "trip_events": 3, "outbox": 1}
OTHER_ROWS = {"sessions": 2, "trip_drafts": 1, "trip_events": 1, "outbox": 1}
SYSTEM = "SELECT set_config('app.tenant_scope', 'system', false)"
# Invented customers: ONLY_TARGET is linked by a TARGET session only (deleted with the tenant), SHARED by a TARGET
# session and an OTHER session (kept), ONLY_OTHER by an OTHER session only (kept).
ONLY_TARGET = "d1d1d1d1-0000-4000-8000-0000000000c1"
SHARED = "d2d2d2d2-0000-4000-8000-0000000000c2"
ONLY_OTHER = "d3d3d3d3-0000-4000-8000-0000000000c3"
CUSTOMERS = {ONLY_TARGET: "Ilse Vandermeer", SHARED: "Tomas Okonkwo-Lind", ONLY_OTHER: "Priya Halvorsen"}
PLAN_KEYS = ["tripplanner.ai_spend_month", "tripplanner.customers", "tripplanner.outbox", "tripplanner.sessions",
             "tripplanner.traveller_accounts", "tripplanner.trip_drafts", "tripplanner.trip_events"]


def _code(sql: str) -> str:
    return "\n".join(ln for ln in sql.splitlines() if not ln.lstrip().startswith("--"))


async def seed_tenant(su, tenant: str, rows: dict, *, spend_usd: float = 0.0,
                      customers: tuple = ()) -> None:
    """Invented rows of one tenant in the four tables (as the superuser, which bypasses row security), plus its spend
    row and the matching app row of this month when spend_usd > 0. ``customers``: customer ids linked by the tenant's
    sessions, in order (one per session; the rest of the sessions link none)."""
    sessions = [uuid.uuid4() for _ in range(rows["sessions"])]
    for i, s in enumerate(sessions):
        await su.execute("INSERT INTO tripplanner.sessions (id, tenant_id, customer_id) VALUES ($1, $2::uuid, $3::uuid)",
                         s, tenant, customers[i] if i < len(customers) else None)
    trips = [uuid.uuid4() for _ in range(rows["trip_drafts"])]
    for i, t in enumerate(trips):
        await su.execute("INSERT INTO tripplanner.trip_drafts (id, session_id, tenant_id, itinerary) "
                         "VALUES ($1, $2, $3::uuid, $4::jsonb)", t, sessions[i % len(sessions)], tenant,
                         json.dumps({"days": [{"day": 1, "name": "Lantern Bay"}]}))
    for i in range(rows["trip_events"]):
        await su.execute("INSERT INTO tripplanner.trip_events (trip_id, session_id, event_type, payload, tenant_id) "
                         "VALUES ($1, $2, 'add_day', '{}', $3::uuid)", trips[i % len(trips)], sessions[0], tenant)
    for i in range(rows["outbox"]):
        await su.execute("INSERT INTO tripplanner.outbox (event, schema_version, idempotency_key, aggregate_id, "
                         "payload, tenant_id) VALUES ('inquiry.submitted', 1, $1, $2, '{}', $3::uuid)",
                         f"{trips[0]}:erase-{tenant[:4]}-{i}", trips[0], tenant)
    if spend_usd:
        await su.execute(
            "INSERT INTO tripplanner.ai_spend_month (stage, scope, month, cost_usd, calls) VALUES "
            "('tp_plan_chat', $1, date_trunc('month', now() AT TIME ZONE 'UTC')::date, $2, 2), "
            "('tp_plan_chat', 'app', date_trunc('month', now() AT TIME ZONE 'UTC')::date, $2, 2) "
            "ON CONFLICT (stage, scope, month) DO UPDATE SET cost_usd = tripplanner.ai_spend_month.cost_usd + "
            "EXCLUDED.cost_usd, calls = tripplanner.ai_spend_month.calls + EXCLUDED.calls",
            f"tenant:{tenant}", spend_usd)


def erasure_database(pg_dsn, *, seed: bool = True):  # noqa: F811
    """tp-028's forced database, then tp-029 and tp-030 (twice) as tp_owner, then TARGET and OTHER rows. Returns
    (context, the first error or None); the caller drops ``context["name"]``. Used by the backend jobs tests too."""
    ctx, error = forced_database(pg_dsn)
    if error:
        return ctx, error

    async def apply():
        admin = await asyncpg.connect(ctx["admin_dsn"])
        try:
            for f in (TP029, TP030, TP030):
                await _as_owner(admin, f)
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
                # a spend row of the target in a past month with no app row: the merge must add one
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
    ctx, error = erasure_database(pg_dsn)
    try:
        assert error is None, error
        yield ctx
    finally:
        _drop(pg_dsn, ctx["name"])


async def _runtime(db, body):
    """``body(conn)`` on the runtime role's own login, in a transaction that is always rolled back."""
    tp = await asyncpg.connect(db["tp_dsn"])
    try:
        return await _in_rollback(tp, body)
    finally:
        await tp.close()


async def _erase(conn, tenant, dry_run, expect=None) -> dict:
    return json.loads(await conn.fetchval("SELECT tripplanner.erase_tenant($1::uuid, $2, $3)::text AS r",
                                          tenant, dry_run, expect))


async def _tenant_counts(conn, tenant) -> dict:
    out = {t: await conn.fetchval(f"SELECT count(*) AS n FROM tripplanner.{t} WHERE tenant_id = $1::uuid", tenant)
           for t in TABLES}
    out["spend"] = await conn.fetchval("SELECT count(*) AS n FROM tripplanner.ai_spend_month WHERE scope = $1",
                                       f"tenant:{tenant}")
    return out


# ── the file ─────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_file_is_a_contract_file_in_one_transaction_block():
    text = TP030.read_text(encoding="utf-8")
    assert not [ln for ln in text.splitlines() if re.match(r"^\s*--\s*migrate:", ln)]
    code = _code(text)
    assert code.count("BEGIN;") == 1 and code.count("COMMIT;") == 1
    assert "ON CONFLICT (version) DO NOTHING" in code and "'tp-030'" in code
    assert not re.search(r"\bSECURITY\s+DEFINER\b", code)
    assert not re.search(r"\bDROP\s+(TABLE|VIEW|SCHEMA|COLUMN|FUNCTION)\b", code, re.I), "contract, not destructive"
    assert not re.search(r"\bTO\s+PUBLIC\b", code.upper())
    # rule d of the RLS catalogue guard: every current_setting('app.tenant_id') inside NULLIF(..., '')
    settings = re.findall(r"current_setting\s*\(\s*'app\.tenant_id'", code)
    wrapped = re.findall(r"NULLIF\s*\(\s*current_setting\s*\(\s*'app\.tenant_id'\s*,\s*true\s*\)\s*,\s*''\s*\)", code)
    assert settings and len(settings) == len(wrapped)


def test_log_function_and_view_shape(db):
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            cols = await su.fetch("SELECT column_name, data_type, is_nullable FROM information_schema.columns "
                                  "WHERE table_schema = 'tripplanner' AND table_name = 'tenant_erasure_log' "
                                  "ORDER BY ordinal_position")
            rls = await su.fetchrow("SELECT relrowsecurity AS r, relforcerowsecurity AS f, "
                                    "pg_get_userbyid(relowner) AS o FROM pg_class "
                                    "WHERE oid = 'tripplanner.tenant_erasure_log'::regclass")
            pk = await su.fetchval("SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint "
                                   "WHERE conname = 'tenant_erasure_log_pkey'")
            policies = await su.fetch("SELECT policyname, cmd, roles::text[] AS roles FROM pg_policies "
                                      "WHERE schemaname = 'tripplanner' AND tablename = 'tenant_erasure_log' "
                                      "ORDER BY policyname")
            fn = await su.fetchrow("SELECT p.prosecdef AS definer, pg_get_userbyid(p.proowner) AS owner, "
                                   "p.proconfig AS config, "
                                   "has_function_privilege('tripplanner', p.oid, 'EXECUTE') AS tp, "
                                   "has_function_privilege('cis_app', p.oid, 'EXECUTE') AS cis, "
                                   "has_function_privilege('booking', p.oid, 'EXECUTE') AS bk "
                                   "FROM pg_proc p WHERE p.oid = "
                                   "'tripplanner.erase_tenant(uuid, boolean, text)'::regprocedure")
            view_cols = await su.fetch("SELECT column_name FROM information_schema.columns WHERE table_schema = "
                                       "'tripplanner' AND table_name = 'v_tenant_erasure_report_v1' "
                                       "ORDER BY ordinal_position")
            privs = {}
            for role in ("tripplanner", "cis_app", "booking"):
                for obj in ("tripplanner.tenant_erasure_log", "tripplanner.v_tenant_erasure_report_v1"):
                    for p in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                        privs[(role, obj.split(".")[1], p)] = await su.fetchval(
                            "SELECT has_table_privilege($1, $2, $3) AS ok", role, obj, p)
            registered = await su.fetchval("SELECT count(*) AS n FROM shared.schema_versions WHERE version = 'tp-030'")
            return cols, rls, pk, policies, fn, view_cols, privs, registered
        finally:
            await su.close()

    cols, rls, pk, policies, fn, view_cols, privs, registered = run(check())
    assert [(r["column_name"], r["data_type"], r["is_nullable"]) for r in cols] == [
        ("request_id", "uuid", "NO"), ("tenant_id", "uuid", "NO"), ("mode", "text", "NO"), ("plan_hash", "text", "YES"),
        ("counts", "jsonb", "NO"), ("status", "text", "NO"), ("reason", "text", "YES"), ("job_id", "uuid", "YES"),
        ("created_at", "timestamp with time zone", "NO"), ("updated_at", "timestamp with time zone", "NO")]
    assert (rls["r"], rls["f"], rls["o"]) == (True, True, "tp_owner")
    assert pk == "PRIMARY KEY (request_id, mode)"
    assert [(p["policyname"], p["cmd"], sorted(p["roles"])) for p in policies] == [
        ("tenant_erasure_log_owner_read", "SELECT", ["tp_owner"]),
        ("tenant_erasure_log_system", "ALL", ["tp_owner", "tripplanner"])]
    assert (fn["definer"], fn["owner"], fn["tp"], fn["cis"], fn["bk"]) == (False, "tp_owner", True, False, False)
    assert sorted(fn["config"]) == sorted(["search_path=pg_catalog, pg_temp", "TimeZone=UTC", "DateStyle=ISO, YMD",
                                           "IntervalStyle=postgres", "extra_float_digits=3", "bytea_output=hex"])
    assert [r["column_name"] for r in view_cols] == ["request_id", "tenant_id", "mode", "status", "reason", "plan_hash",
                                                     "counts", "job_id", "created_at", "updated_at"]
    assert {k: v for k, v in privs.items() if v} == {
        ("tripplanner", "tenant_erasure_log", "SELECT"): True, ("tripplanner", "tenant_erasure_log", "INSERT"): True,
        ("tripplanner", "tenant_erasure_log", "UPDATE"): True,
        ("cis_app", "v_tenant_erasure_report_v1", "SELECT"): True}     # the runtime role cannot read the view
    assert registered == 1


# ── the function, as the runtime role ────────────────────────────────────────────────────────────────────────────────

def test_function_refuses_outside_the_system_scope_and_under_a_bound_tenant(db):
    async def body(conn):
        out = []
        for setup in ("", f"SELECT set_config('app.tenant_id', '{TARGET}', false)"):
            if setup:
                await conn.execute(setup)
            try:
                async with conn.transaction():
                    if setup:
                        await conn.execute(SYSTEM)
                    await _erase(conn, TARGET, True)
                out.append("ran")
            except asyncpg.InsufficientPrivilegeError:
                out.append("refused")
        return out

    assert run(_runtime(db, body)) == ["refused", "refused"]


def test_dry_run_plan_is_stable_counts_the_tenant_and_writes_nothing(db):
    async def body(conn):
        await conn.execute(SYSTEM)
        before = await _tenant_counts(conn, TARGET)
        first = await _erase(conn, TARGET, True)
        second = await _erase(conn, TARGET, True)
        return before, first, second, await _tenant_counts(conn, TARGET)

    before, first, second, after = run(_runtime(db, body))
    assert before == {**TARGET_ROWS, "spend": 2} and after == before
    assert first == second
    assert first["status"] == "planned" and first["dry_run"] is True and re.fullmatch(r"[0-9a-f]{64}", first["plan_hash"])
    assert sorted(first["tables"]) == PLAN_KEYS
    assert {k: v["rows"] for k, v in first["tables"].items()} == {
        "tripplanner.sessions": 2, "tripplanner.trip_drafts": 2, "tripplanner.trip_events": 3, "tripplanner.outbox": 1,
        "tripplanner.ai_spend_month": 2, "tripplanner.customers": 1, "tripplanner.traveller_accounts": 0}
    assert {k: v["action"] for k, v in first["tables"].items()} == {
        **dict.fromkeys(["tripplanner.sessions", "tripplanner.trip_drafts", "tripplanner.trip_events",
                         "tripplanner.outbox", "tripplanner.customers"], "delete"),
        "tripplanner.ai_spend_month": "merge_into_app", "tripplanner.traveller_accounts": "keep"}
    assert first["tables"]["tripplanner.customers"]["fingerprint"] != first["tables"]["tripplanner.outbox"]["fingerprint"]
    # no row value in the plan: only counts, actions and hex fingerprints
    for v in first["tables"].values():
        assert set(v) == {"action", "rows", "fingerprint"}
        assert v["fingerprint"] is None or re.fullmatch(r"[0-9a-f]{64}", v["fingerprint"])
    assert "Lantern" not in json.dumps(first)
    assert not [n for n in CUSTOMERS.values() if n in json.dumps(first)] and "example.test" not in json.dumps(first)


@pytest.mark.parametrize("change", [
    "UPDATE tripplanner.trip_drafts SET updated_at = updated_at + interval '1 second' WHERE tenant_id = '{t}'",
    "UPDATE tripplanner.trip_drafts SET itinerary = '{{}}'::jsonb WHERE tenant_id = '{t}'",
    "INSERT INTO tripplanner.trip_events (trip_id, session_id, event_type, payload, tenant_id) "
    "SELECT trip_id, session_id, 'reorder', '{{}}', tenant_id FROM tripplanner.trip_events WHERE tenant_id = '{t}' LIMIT 1",
    "UPDATE tripplanner.ai_spend_month SET calls = calls + 1 WHERE scope = 'tenant:{t}'",
    f"UPDATE tripplanner.customers SET contact_consent = true WHERE id = '{ONLY_TARGET}'",
])
def test_plan_hash_changes_when_the_tenant_data_changes(db, change):
    async def body(conn):
        await conn.execute(SYSTEM)
        before = (await _erase(conn, TARGET, True))["plan_hash"]
        await conn.execute(change.format(t=TARGET))
        return before, (await _erase(conn, TARGET, True))["plan_hash"]

    before, after = run(_runtime(db, body))
    assert before != after


def test_plan_hash_ignores_other_tenants(db):
    async def body(conn):
        await conn.execute(SYSTEM)
        before = (await _erase(conn, TARGET, True))["plan_hash"]
        await conn.execute("UPDATE tripplanner.trip_drafts SET itinerary = '{}'::jsonb WHERE tenant_id = $1::uuid", OTHER)
        await conn.execute("UPDATE tripplanner.ai_spend_month SET calls = calls + 5 WHERE scope = 'app'")
        return before, (await _erase(conn, TARGET, True))["plan_hash"], (await _erase(conn, OTHER, True))["plan_hash"]

    before, after, other = run(_runtime(db, body))
    assert before == after and other != before


def test_plan_hash_is_the_same_under_other_session_settings(db):
    """The fingerprints hash row text, which depends on the session's TimeZone / DateStyle / extra_float_digits /
    IntervalStyle / bytea_output: the function pins them, so the operator's DRY RUN and the jobs Lambda's apply agree."""
    async def body(conn):
        await conn.execute(SYSTEM)
        hashes = []
        for tz, style, digits, byt in (("UTC", "ISO, MDY", "1", "hex"),
                                       ("Asia/Ho_Chi_Minh", "SQL, DMY", "0", "escape"),
                                       ("America/Los_Angeles", "German", "-15", "escape")):
            await conn.execute(f"SET TimeZone = '{tz}'")
            await conn.execute(f"SET DateStyle = '{style}'")
            await conn.execute(f"SET extra_float_digits = {digits}")
            await conn.execute(f"SET bytea_output = '{byt}'")
            await conn.execute("SET IntervalStyle = 'iso_8601'" if tz != "UTC" else "SET IntervalStyle = 'postgres'")
            plan = await _erase(conn, TARGET, True)
            hashes.append((plan["plan_hash"], {k: v["fingerprint"] for k, v in plan["tables"].items()},
                           await conn.fetchval("SHOW TimeZone")))
        # a stamped row of the target: its text under the caller's own settings differs between the two zones
        texts = []
        for tz in ("UTC", "Asia/Ho_Chi_Minh"):
            await conn.execute(f"SET TimeZone = '{tz}'")
            texts.append(await conn.fetchval("SELECT t::text FROM tripplanner.sessions t WHERE t.tenant_id = $1::uuid "
                                             "ORDER BY t.id LIMIT 1", TARGET))
        return hashes, texts

    hashes, texts = run(_runtime(db, body))
    assert texts[0] != texts[1]                                   # the settings do move the row text …
    assert hashes[0][0] == hashes[1][0] == hashes[2][0]           # … but not the plan hash
    assert hashes[0][1] == hashes[1][1] == hashes[2][1]
    assert hashes[1][2] == "Asia/Ho_Chi_Minh"                     # the function's SET did not leak into the session


def test_apply_needs_a_plan_and_refuses_a_stale_one(db):
    async def body(conn):
        await conn.execute(SYSTEM)
        try:
            async with conn.transaction():
                await _erase(conn, TARGET, False)
            missing = "ran"
        except asyncpg.InvalidParameterValueError:
            missing = "refused"
        stale = (await _erase(conn, TARGET, True))["plan_hash"]
        await conn.execute("UPDATE tripplanner.trip_drafts SET status = 'sent' WHERE tenant_id = $1::uuid", TARGET)
        refused = await _erase(conn, TARGET, False, stale)
        return missing, refused, await _tenant_counts(conn, TARGET)

    missing, refused, counts = run(_runtime(db, body))
    assert missing == "refused"
    assert (refused["status"], refused["reason"], refused["deleted"]) == ("refused", "plan_changed", {})
    assert counts == {**TARGET_ROWS, "spend": 2}


def test_apply_with_the_approved_plan_erases_the_tenant_only_and_merges_its_spend(db):
    async def body(conn):
        await conn.execute(SYSTEM)
        other_before = await _tenant_counts(conn, OTHER)
        app_before = {r["month"]: (float(r["cost_usd"]), r["calls"]) for r in await conn.fetch(
            "SELECT month, cost_usd, calls FROM tripplanner.ai_spend_month WHERE scope = 'app'")}
        demo_before = await conn.fetchval("SELECT count(*) AS n FROM tripplanner.sessions WHERE tenant_id <> ALL($1::uuid[])",
                                          [TARGET, OTHER])
        plan = (await _erase(conn, TARGET, True))["plan_hash"]
        result = await _erase(conn, TARGET, False, plan)
        app_after = {r["month"]: (float(r["cost_usd"]), r["calls"]) for r in await conn.fetch(
            "SELECT month, cost_usd, calls FROM tripplanner.ai_spend_month WHERE scope = 'app'")}
        verify = await _erase(conn, TARGET, True)
        demo_after = await conn.fetchval("SELECT count(*) AS n FROM tripplanner.sessions WHERE tenant_id <> ALL($1::uuid[])",
                                         [TARGET, OTHER])
        customers_after = [r["id"] for r in await conn.fetch(
            "SELECT id::text AS id FROM tripplanner.customers WHERE id = ANY($1::uuid[]) ORDER BY id", list(CUSTOMERS))]
        return (other_before, app_before, result, await _tenant_counts(conn, TARGET), await _tenant_counts(conn, OTHER),
                app_after, verify, demo_before, demo_after, customers_after)

    (other_before, app_before, result, target_after, other_after, app_after, verify, demo_before, demo_after,
     customers_after) = run(_runtime(db, body))
    assert result["status"] == "applied" and result["reason"] is None
    assert result["deleted"] == {"tripplanner.sessions": 2, "tripplanner.trip_drafts": 2, "tripplanner.trip_events": 3,
                                 "tripplanner.outbox": 1, "tripplanner.customers": 1, "tripplanner.ai_spend_month": 2}
    assert customers_after == sorted([SHARED, ONLY_OTHER])     # only the customer no other tenant links is gone
    assert result["app_rows_added"] == 1                     # September had no app row; this month's already counts it
    assert target_after == dict.fromkeys([*TABLES, "spend"], 0)
    assert other_after == other_before == {**OTHER_ROWS, "spend": 1}
    assert demo_after == demo_before > 0
    sept = [m for m in app_after if m.isoformat() == "2026-09-01"]
    assert len(sept) == 1 and app_after[sept[0]] == (0.1, 1)
    assert {m: v for m, v in app_after.items() if m not in sept} == app_before   # this month's app sum unchanged
    assert {v["rows"] for v in verify["tables"].values()} == {0}


def test_runtime_cannot_delete_log_rows_and_cis_app_reads_the_view_only(db):
    request = "c9c9c9c9-0000-4000-8000-0000000000e3"

    async def body(conn):
        await conn.execute(SYSTEM)
        await conn.execute("INSERT INTO tripplanner.tenant_erasure_log (request_id, tenant_id, mode, plan_hash, counts, "
                           "status) VALUES ($1::uuid, $2::uuid, 'dry_run', $3, '{}', 'planned')", request, TARGET, "a" * 64)
        try:
            async with conn.transaction():
                await conn.execute("DELETE FROM tripplanner.tenant_erasure_log WHERE request_id = $1::uuid", request)
            deleted = "ran"
        except asyncpg.InsufficientPrivilegeError:
            deleted = "refused"
        try:
            async with conn.transaction():
                await conn.execute("INSERT INTO tripplanner.tenant_erasure_log (request_id, tenant_id, mode, counts, "
                                   "status) VALUES ($1::uuid, $2::uuid, 'apply', '{}', 'refused')", request, TARGET)
            no_reason = "ran"
        except asyncpg.CheckViolationError:
            no_reason = "refused"
        try:
            async with conn.transaction():
                await conn.fetch("SELECT 1 FROM tripplanner.v_tenant_erasure_report_v1")
            view = "read"
        except asyncpg.InsufficientPrivilegeError:
            view = "refused"
        return deleted, no_reason, view

    deleted, no_reason, view = run(_runtime(db, body))
    assert (deleted, no_reason, view) == ("refused", "refused", "refused")   # the report view is CIS's, not the runtime's

    async def as_cis():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            async with su.transaction():
                await su.execute(SYSTEM)
                await su.execute("INSERT INTO tripplanner.tenant_erasure_log (request_id, tenant_id, mode, plan_hash, "
                                 "counts, status) VALUES ($1::uuid, $2::uuid, 'dry_run', $3, '{}', 'planned')",
                                 request, TARGET, "b" * 64)
                await su.execute("RESET app.tenant_scope")
                await su.execute("SET LOCAL ROLE cis_app")
                rows = await su.fetch("SELECT request_id::text AS r, mode, status, plan_hash "
                                      "FROM tripplanner.v_tenant_erasure_report_v1")
                try:
                    await su.fetch("SELECT 1 FROM tripplanner.tenant_erasure_log")
                    table = "read"
                except asyncpg.InsufficientPrivilegeError:
                    table = "refused"
                raise _Done((rows, table))
        except _Done as d:
            return d.value
        finally:
            await su.close()

    rows, table = run(as_cis())
    assert [(r["r"], r["mode"], r["status"], r["plan_hash"]) for r in rows] == [(request, "dry_run", "planned", "b" * 64)]
    assert table == "refused"


class _Done(Exception):
    def __init__(self, value):
        super().__init__("rollback")
        self.value = value


def test_rerun_as_tp_owner_is_a_no_op(db):
    async def check():
        admin = await asyncpg.connect(db["admin_dsn"])
        try:
            first = await admin.fetchval("SELECT applied_at FROM shared.schema_versions WHERE version = 'tp-030'")
            await _as_owner(admin, TP030)
            rows = await admin.fetch("SELECT applied_at FROM shared.schema_versions WHERE version = 'tp-030'")
            policies = await admin.fetchval("SELECT count(*) AS n FROM pg_policies WHERE schemaname = 'tripplanner' "
                                            "AND tablename = 'tenant_erasure_log'")
            return first, [r["applied_at"] for r in rows], policies
        finally:
            await admin.close()

    first, after, policies = run(check())
    assert after == [first] and policies == 2
