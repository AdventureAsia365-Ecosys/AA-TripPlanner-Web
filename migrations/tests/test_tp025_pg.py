"""tp-025 replay (AA-1038 R5 slice 1): tenant_id and shadow row-level security on tripplanner.sessions, trip_drafts,
trip_events and outbox, applied the way Migration Apply / DB Migrate applies it (as tp_owner, after tp-013 … tp-024).

Runs only with ``--pg-dsn`` (a SUPERUSER DSN of a throwaway LOCAL Postgres), on the stand-in of test_tp013_pg.py:
tp-001…012 replayed as files, the CIS 243 grants, tp-013 as the master, tp-014 … tp-024 with ``SET ROLE tp_owner``,
rows seeded in the four tables, then tp-025 the same way. Each guard case runs in its own database.

Checks: the contract shape (no directive line, one BEGIN … COMMIT, no FORCE, no policy TO PUBLIC, every
current_setting inside NULLIF, the out-of-scope tables untouched); every row written before tp-025 has the demo
tenant; column, default, indexes, RLS flags, policies and comments; as the runtime role ``tripplanner`` (logged in, so
row security applies to it): no tenant context (unset or '') and the demo context see every row, a forged context sees
none, an INSERT without tenant_id takes the context's tenant, an INSERT / UPDATE that names another tenant fails WITH
CHECK, an UPDATE / DELETE under a forged context reaches nothing, a context that is not a uuid fails the statement; the
owner tp_owner and the owner-owned views read by booking (v_inquiry_outbox) and cis_app (v_ops_data_totals) keep every
row under a forged context; the guards refuse a forced table and a policy of another shape (nothing changes);
registration once and a re-run as tp_owner being a no-op.
"""
from __future__ import annotations

import re
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
TP025 = MIGRATIONS / "025_tenant_id_shadow_rls.sql"
DEMO = "00000000-0000-0000-0000-000000000002"
FORGED = str(uuid.UUID("7e57c0de-0000-4000-8000-00000000f00d"))
TABLES = ("sessions", "trip_drafts", "trip_events", "outbox")
UNTOUCHED = ("customers", "traveller_accounts", "narration_cache", "ai_rate_counter", "job", "component_id_map")
INDEXES = {
    "sessions_tenant_idx": "CREATE INDEX sessions_tenant_idx ON tripplanner.sessions USING btree (tenant_id)",
    "trip_drafts_tenant_idx":
        "CREATE INDEX trip_drafts_tenant_idx ON tripplanner.trip_drafts USING btree (tenant_id, session_id)",
    "trip_events_tenant_idx":
        "CREATE INDEX trip_events_tenant_idx ON tripplanner.trip_events USING btree (tenant_id, trip_id)",
    "outbox_tenant_idx": "CREATE INDEX outbox_tenant_idx ON tripplanner.outbox USING btree (tenant_id)",
}
SEEDED = {"sessions": 2, "trip_drafts": 3, "trip_events": 4, "outbox": 2}
# The policy expression as Postgres deparses it (pg_policies.qual / with_check).
POLICY_EXPR = ("((NULLIF(current_setting('app.tenant_id'::text, true), ''::text) IS NULL) OR "
               "(tenant_id = (NULLIF(current_setting('app.tenant_id'::text, true), ''::text))::uuid))")
VIEW_COLUMNS = ["id", "schema_version", "idempotency_key", "trip_id", "payload", "created_at"]


def _code(sql: str) -> str:
    """The file without its comment lines."""
    return "\n".join(ln for ln in sql.splitlines() if not ln.lstrip().startswith("--"))


async def _as_owner(admin, path) -> None:
    await admin.execute("SET ROLE tp_owner")
    try:
        await admin.execute(path.read_text(encoding="utf-8"))
    finally:
        await admin.execute("RESET ROLE")


class _Rollback(Exception):
    def __init__(self, value):
        super().__init__("rollback")
        self.value = value


async def _in_rollback(conn, body):
    """Run ``body(conn)`` in a transaction that is always rolled back; return its value."""
    try:
        async with conn.transaction():
            raise _Rollback(await body(conn))
    except _Rollback as r:
        return r.value


async def _seed(conn) -> dict:
    """Invented rows in the four tables, written before tp-025 (no tenant_id column yet)."""
    s1, s2 = uuid.uuid4(), uuid.uuid4()
    t1, t2, t3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await conn.execute("INSERT INTO tripplanner.sessions (id) VALUES ($1), ($2)", s1, s2)
    await conn.execute("INSERT INTO tripplanner.trip_drafts (id, session_id) VALUES ($1, $4), ($2, $4), ($3, $5)",
                       t1, t2, t3, s1, s2)
    await conn.execute(
        "INSERT INTO tripplanner.trip_events (trip_id, session_id, event_type, payload) VALUES "
        "($1, $3, 'add_component', '{\"component_id\": \"x\"}'), ($1, $3, 'add_day', '{}'), "
        "($2, $3, 'reorder', '{}'), ($2, $4, 'remove_day', '{}')", t1, t2, s1, s2)
    await conn.execute(
        "INSERT INTO tripplanner.outbox (event, schema_version, idempotency_key, aggregate_id, payload) VALUES "
        "('inquiry.submitted', 1, $1, $2, '{}'), ('inquiry.submitted', 1, $3, $4, '{}')",
        f"{t1}:h1", t1, f"{t3}:h2", t3)
    return {"session": s1, "trip": t1}


def _database(pg_dsn, *, extra_sql: str = ""):  # noqa: F811
    """Builds a database up to tp-024, seeds the four tables, runs `extra_sql` as the superuser, then tp-025 as
    tp_owner. Returns (context, the tp-025 error or None); the caller drops it."""
    name = f"tp_aa1038_{uuid.uuid4().hex[:8]}"
    password = secrets.token_urlsafe(16)
    runtime_password = secrets.token_urlsafe(16)
    booking_password = secrets.token_urlsafe(16)

    async def setup():
        su = await asyncpg.connect(pg_dsn)
        try:
            await su.execute(BOOTSTRAP_ROLES)
            await su.execute(CIS_READER)
            await su.execute(f"ALTER ROLE aa_cis_admin PASSWORD '{password}'")
            await su.execute(f"ALTER ROLE tripplanner LOGIN PASSWORD '{runtime_password}'")
            await su.execute(f"ALTER ROLE booking LOGIN PASSWORD '{booking_password}'")
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
            seeded = await _seed(su_db)
            if extra_sql:
                await su_db.execute(extra_sql)
        finally:
            await su_db.close()
        admin = await asyncpg.connect(admin_dsn)
        error = None
        try:
            await admin.execute("SET ROLE tp_owner")
            try:
                await admin.execute(TP025.read_text(encoding="utf-8"))
            except asyncpg.PostgresError as e:
                error = f"{type(e).__name__}: {e}"
                if admin.is_in_transaction():
                    await admin.execute("ROLLBACK")
        finally:
            await admin.close()
        return {
            "name": name,
            "admin_dsn": admin_dsn,
            "su_dsn": _dsn(pg_dsn, database=name),
            "tp_dsn": _dsn(pg_dsn, user="tripplanner", password=runtime_password, database=name),
            "bk_dsn": _dsn(pg_dsn, user="booking", password=booking_password, database=name),
            **seeded,
        }, error

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


async def _counts(conn) -> dict:
    return {t: await conn.fetchval(f"SELECT count(*) AS n FROM tripplanner.{t}") for t in TABLES}


async def _bind(conn, tenant) -> None:
    """The tenant context the assembly Lambda will bind (session level, as the shadow-deploy code PR does)."""
    await conn.execute("SELECT set_config('app.tenant_id', $1, false)", tenant)


# ── the file ─────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_files_before_tp025_run_from_tp014_without_a_gap():
    numbers = [int(f.name[:3]) for f in BEFORE_TP025]
    assert numbers == list(range(14, 25)), numbers


def test_file_is_a_contract_file_in_one_transaction_block():
    """Contract (root ADR 0017a): no directive line at all ('contract' is not a directive); one BEGIN … COMMIT."""
    text = TP025.read_text(encoding="utf-8")
    assert not [ln for ln in text.splitlines() if re.match(r"^\s*--\s*migrate:", ln)]
    code = _code(text)
    assert code.count("BEGIN;") == 1 and code.count("COMMIT;") == 1
    assert "ON CONFLICT (version) DO NOTHING" in code and "'tp-025'" in code


def test_policy_text_follows_the_rls_catalogue_rules():
    """Rules b (never TO PUBLIC), d (every current_setting inside NULLIF(..., '')), t (no bare true), and no FORCE
    (the shadow phase: the owner and its views keep every row)."""
    code = _code(TP025.read_text(encoding="utf-8"))
    upper = code.upper()
    assert "FORCE ROW LEVEL SECURITY" not in upper
    assert not re.search(r"\bTO\s+PUBLIC\b", upper)
    assert not re.search(r"\b(USING|CHECK)\s*\(\s*TRUE\s*\)", upper)
    code = re.sub(r"LIKE\s+'(?:[^']|'')*'", "LIKE ''", code)        # the guard's LIKE patterns are text, not calls
    settings = re.findall(r"current_setting\s*\(", code)
    wrapped = re.findall(r"NULLIF\s*\(\s*current_setting\s*\(\s*'app\.tenant_id',\s*true\s*\)\s*,\s*''\s*\)", code)
    assert settings and len(settings) == len(wrapped), (len(settings), len(wrapped))
    assert len(re.findall(r"\bCREATE POLICY (\w+)_tenant_shadow ON tripplanner\.\1 AS PERMISSIVE FOR ALL TO "
                          r"tripplanner\b", code)) == 4


def test_out_of_scope_tables_are_not_named():
    code = _code(TP025.read_text(encoding="utf-8"))
    for t in UNTOUCHED:
        assert not re.search(rf"\btripplanner\.{t}\b", code), t
    assert "outbox_event_chk" not in code


# ── the database after tp-025 ────────────────────────────────────────────────────────────────────────────────────────

def test_every_row_written_before_has_the_demo_tenant(db):
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            totals = await _counts(su)
            demo = {t: await su.fetchval(f"SELECT count(*) AS n FROM tripplanner.{t} WHERE tenant_id = $1::uuid", DEMO)
                    for t in TABLES}
            return totals, demo
        finally:
            await su.close()

    totals, demo = run(check())
    assert totals == SEEDED and demo == SEEDED


def test_column_default_indexes_rls_policies_and_comments(db):
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            cols = await su.fetch("""
                SELECT c.relname::text AS t, format_type(a.atttypid, a.atttypmod) AS type, a.attnotnull AS notnull,
                       pg_get_expr(d.adbin, d.adrelid) AS dflt, col_description(c.oid, a.attnum) AS comment,
                       c.relrowsecurity AS rls, c.relforcerowsecurity AS forced
                  FROM pg_class c JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'tenant_id'
                  LEFT JOIN pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum
                 WHERE c.relnamespace = 'tripplanner'::regnamespace AND c.relkind IN ('r', 'p')""")
            idx = {r["n"]: r["def"] for r in await su.fetch(
                "SELECT indexname::text AS n, indexdef AS def FROM pg_indexes "
                "WHERE schemaname = 'tripplanner' AND indexname LIKE '%tenant_idx'")}
            pols = await su.fetch(
                "SELECT tablename::text AS t, policyname::text AS p, permissive, cmd, roles::text[] AS roles, "
                "qual, with_check FROM pg_policies WHERE schemaname = 'tripplanner'")
            untouched = await su.fetch(
                "SELECT c.relname::text AS t, c.relrowsecurity AS rls FROM pg_class c "
                "WHERE c.relnamespace = 'tripplanner'::regnamespace AND c.relname = ANY($1::text[])", list(UNTOUCHED))
            return cols, idx, pols, untouched
        finally:
            await su.close()

    cols, idx, pols, untouched = run(check())
    by_table = {r["t"]: r for r in cols}
    assert set(by_table) == set(TABLES), sorted(by_table)      # no tenant_id anywhere else in tripplanner
    for t, r in by_table.items():
        assert r["type"] == "uuid" and r["notnull"], (t, r)
        assert "current_setting('app.tenant_id'::text, true)" in r["dflt"] and DEMO in r["dflt"], (t, r["dflt"])
        assert r["dflt"].startswith("COALESCE((NULLIF(current_setting('app.tenant_id'"), r["dflt"]
        assert r["rls"] is True and r["forced"] is False, t
        assert "tp-025" in r["comment"] and "shared.tenants.tenant_id" in r["comment"] and "Shadow" in r["comment"]
    assert idx == INDEXES
    assert sorted((p["t"], p["p"]) for p in pols) == sorted((t, f"{t}_tenant_shadow") for t in TABLES)
    for p in pols:
        assert p["permissive"] == "PERMISSIVE" and p["cmd"] == "ALL" and p["roles"] == ["tripplanner"], dict(p)
        for expr in (p["qual"], p["with_check"]):
            assert expr == POLICY_EXPR, expr
    assert untouched and all(r["rls"] is False for r in untouched), [dict(r) for r in untouched]


def test_v_inquiry_outbox_keeps_its_columns(db):
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            return [r["c"] for r in await su.fetch(
                "SELECT attname::text AS c FROM pg_attribute WHERE attrelid = 'tripplanner.v_inquiry_outbox'::regclass "
                "AND attnum > 0 ORDER BY attnum")]
        finally:
            await su.close()

    assert run(check()) == VIEW_COLUMNS


# ── the runtime role ─────────────────────────────────────────────────────────────────────────────────────────────────

def test_runtime_reads_every_row_without_a_context_or_with_the_demo_one_and_none_with_a_forged_one(db):
    async def check():
        tp = await asyncpg.connect(db["tp_dsn"])
        try:
            seen = {"unset": await _counts(tp)}
            await tp.execute("SET app.tenant_id = ''")
            seen["empty"] = await _counts(tp)
            await _bind(tp, DEMO)
            seen["demo"] = await _counts(tp)
            await _bind(tp, FORGED)
            seen["forged"] = await _counts(tp)
            await tp.execute("RESET app.tenant_id")                      # what unbind does: '' from here on
            seen["reset"] = await _counts(tp)
            seen["bypass"] = await tp.fetchval("SELECT rolbypassrls OR rolsuper AS b FROM pg_roles "
                                               "WHERE rolname = current_user")
            return seen
        finally:
            await tp.close()

    seen = run(check())
    assert seen.pop("bypass") is False                            # row security really applies to this login
    assert seen == {"unset": SEEDED, "empty": SEEDED, "demo": SEEDED, "forged": dict.fromkeys(TABLES, 0),
                    "reset": SEEDED}


def test_runtime_insert_without_tenant_id_takes_the_context(db):
    async def check():
        tp = await asyncpg.connect(db["tp_dsn"])
        try:
            async def body(conn):
                out = {}
                await _bind(conn, FORGED)
                sid = await conn.fetchval("INSERT INTO tripplanner.sessions (id) VALUES (gen_random_uuid()) "
                                          "RETURNING id")
                tid = await conn.fetchval("INSERT INTO tripplanner.trip_drafts (session_id) VALUES ($1) RETURNING id",
                                          sid)
                await conn.execute("INSERT INTO tripplanner.trip_events (trip_id, session_id, event_type, payload) "
                                   "VALUES ($1, $2, 'add_day', '{}')", tid, sid)
                await conn.execute("INSERT INTO tripplanner.outbox (event, schema_version, idempotency_key, "
                                   "aggregate_id, payload) VALUES ('inquiry.submitted', 1, $1, $2, '{}')",
                                   f"{tid}:h9", tid)
                out["forged_tenants"] = {t: [str(r["tenant_id"]) for r in await conn.fetch(
                    f"SELECT tenant_id FROM tripplanner.{t}")] for t in TABLES}
                await conn.execute("RESET app.tenant_id")
                no_ctx = await conn.fetchval("INSERT INTO tripplanner.sessions (id) VALUES (gen_random_uuid()) "
                                             "RETURNING tenant_id")
                out["no_context"] = str(no_ctx)
                return out
            return await _in_rollback(tp, body)
        finally:
            await tp.close()

    out = run(check())
    assert out["forged_tenants"] == {t: [FORGED] for t in TABLES}     # the forged context sees only its new rows
    assert out["no_context"] == DEMO


@pytest.mark.parametrize("table, insert, args", [
    ("sessions", "INSERT INTO tripplanner.sessions (id, tenant_id) VALUES (gen_random_uuid(), $1)", ()),
    ("trip_drafts", "INSERT INTO tripplanner.trip_drafts (session_id, tenant_id) VALUES ($2, $1)", ("session",)),
    ("trip_events", "INSERT INTO tripplanner.trip_events (trip_id, session_id, event_type, payload, tenant_id) "
                    "VALUES ($3, $2, 'add_day', '{}', $1)", ("session", "trip")),
    ("outbox", "INSERT INTO tripplanner.outbox (event, schema_version, idempotency_key, aggregate_id, payload, "
               "tenant_id) VALUES ('inquiry.submitted', 1, gen_random_uuid()::text, $2, '{}', $1)", ("trip",)),
])
def test_runtime_insert_naming_another_tenant_fails_with_check(db, table, insert, args):
    async def check():
        tp = await asyncpg.connect(db["tp_dsn"])
        try:
            await _bind(tp, DEMO)
            try:
                await tp.execute(insert, uuid.UUID(FORGED), *(db[a] for a in args))
            except asyncpg.InsufficientPrivilegeError as e:
                return str(e)
            return None
        finally:
            await tp.close()

    error = run(check())
    assert error and "row-level security policy" in error and table in error, error


def test_runtime_update_and_delete_under_a_forged_context_reach_nothing(db):
    async def check():
        tp = await asyncpg.connect(db["tp_dsn"])
        try:
            async def body(conn):
                await _bind(conn, FORGED)
                return [await conn.execute("UPDATE tripplanner.trip_drafts SET status = 'draft'"),
                        await conn.execute("DELETE FROM tripplanner.trip_events"),
                        await conn.execute("UPDATE tripplanner.outbox SET redacted_at = now()"),
                        await conn.execute("DELETE FROM tripplanner.sessions WHERE id = $1", db["session"])]
            return await _in_rollback(tp, body)
        finally:
            await tp.close()

    assert run(check()) == ["UPDATE 0", "DELETE 0", "UPDATE 0", "DELETE 0"]


def test_runtime_cannot_move_a_row_to_another_tenant(db):
    async def check():
        tp = await asyncpg.connect(db["tp_dsn"])
        try:
            await _bind(tp, DEMO)
            try:
                await tp.execute("UPDATE tripplanner.trip_drafts SET tenant_id = $1 WHERE id = $2",
                                 uuid.UUID(FORGED), db["trip"])
            except asyncpg.InsufficientPrivilegeError as e:
                return str(e)
            return None
        finally:
            await tp.close()

    error = run(check())
    assert error and "row-level security policy" in error, error


def test_runtime_without_a_context_reaches_every_row_for_the_jobs_path(db):
    """The jobs Lambda (role tripplanner, no tenant context) keeps its retention deletes / redaction updates."""
    async def check():
        tp = await asyncpg.connect(db["tp_dsn"])
        try:
            async def body(conn):
                return [await conn.execute("UPDATE tripplanner.outbox SET redacted_at = now()"),
                        await conn.execute("DELETE FROM tripplanner.trip_events")]
            return await _in_rollback(tp, body)
        finally:
            await tp.close()

    assert run(check()) == [f"UPDATE {SEEDED['outbox']}", f"DELETE {SEEDED['trip_events']}"]


def test_a_context_that_is_not_a_uuid_fails_closed(db):
    async def check():
        tp = await asyncpg.connect(db["tp_dsn"])
        try:
            await _bind(tp, "not-a-tenant")
            errors = {}
            for t in TABLES:
                try:
                    await tp.fetchval(f"SELECT count(*) AS n FROM tripplanner.{t}")
                    errors[t] = None
                except asyncpg.InvalidTextRepresentationError:
                    errors[t] = "InvalidTextRepresentation"
            return errors
        finally:
            await tp.close()

    assert run(check()) == dict.fromkeys(TABLES, "InvalidTextRepresentation")


# ── the owner and the views other apps read ──────────────────────────────────────────────────────────────────────────

def test_owner_and_owner_views_keep_every_row_under_a_forged_context(db):
    async def check():
        admin = await asyncpg.connect(db["admin_dsn"])
        try:
            await _bind(admin, FORGED)
            member = await _counts(admin)            # the master stand-in, an INHERIT member of tp_owner (rl-001)
            await admin.execute("SET ROLE tp_owner")
            owner = await _counts(admin)
        finally:
            await admin.close()
        bk = await asyncpg.connect(db["bk_dsn"])
        try:
            await _bind(bk, FORGED)
            booking = await bk.fetchval("SELECT count(*) AS n FROM tripplanner.v_inquiry_outbox")
        finally:
            await bk.close()
        su = await asyncpg.connect(db["su_dsn"])
        try:
            await su.execute("SET ROLE cis_app")
            await _bind(su, FORGED)
            totals = {r["metric"]: r["n"] for r in await su.fetch(
                "SELECT metric, n FROM tripplanner.v_ops_data_totals")}
        finally:
            await su.close()
        return member, owner, booking, totals

    member, owner, booking, totals = run(check())
    assert member == owner == SEEDED
    assert booking == SEEDED["outbox"]
    assert {k: totals[k] for k in ("sessions", "trip_drafts", "outbox_rows", "pin_events")} == {
        "sessions": SEEDED["sessions"], "trip_drafts": SEEDED["trip_drafts"], "outbox_rows": SEEDED["outbox"],
        "pin_events": 1}


def test_rerun_as_tp_owner_is_a_no_op(db):
    state_sql = """
        SELECT string_agg(x, E'\n' ORDER BY x) AS s FROM (
          SELECT 'col ' || attrelid::regclass::text || ' ' || attnotnull || ' '
                 || coalesce(pg_get_expr(d.adbin, d.adrelid), '-') || ' '
                 || coalesce(col_description(attrelid, attnum), '-') AS x
            FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
           WHERE attname = 'tenant_id' AND attrelid::regclass::text LIKE 'tripplanner.%'
          UNION ALL
          SELECT 'pol ' || polrelid::regclass::text || ' ' || polname || ' ' || p.oid::text
            FROM pg_policy p WHERE polrelid::regclass::text LIKE 'tripplanner.%'
          UNION ALL
          SELECT 'rls ' || oid::regclass::text || ' ' || relrowsecurity || ' ' || relforcerowsecurity
            FROM pg_class WHERE relnamespace = 'tripplanner'::regnamespace AND relkind = 'r'
          UNION ALL
          SELECT 'reg ' || version || ' ' || applied_at::text FROM shared.schema_versions WHERE version = 'tp-025'
        ) q"""

    async def check():
        admin = await asyncpg.connect(db["admin_dsn"])
        try:
            before = await admin.fetchval(state_sql)
            counts = await _counts(admin)
            await _as_owner(admin, TP025)
            return before, await admin.fetchval(state_sql), counts, await _counts(admin)
        finally:
            await admin.close()

    before, after, counts_before, counts_after = run(check())
    assert before == after                      # the policy oids too: the re-run did not recreate a policy
    assert counts_before == counts_after == SEEDED


# ── the guards ───────────────────────────────────────────────────────────────────────────────────────────────────────

def _refused(pg_dsn, extra_sql) -> tuple:  # noqa: F811
    ctx, error = _database(pg_dsn, extra_sql=extra_sql)
    try:
        async def state():
            su = await asyncpg.connect(ctx["su_dsn"])
            try:
                cols = await su.fetchval(
                    "SELECT count(*) AS n FROM pg_attribute WHERE attname = 'tenant_id' AND NOT attisdropped "
                    "AND attrelid::regclass::text LIKE 'tripplanner.%'")
                registered = await su.fetchval(
                    "SELECT count(*) AS n FROM shared.schema_versions WHERE version = 'tp-025'")
                return cols, registered
            finally:
                await su.close()
        cols, registered = run(state())
    finally:
        _drop(pg_dsn, ctx["name"])
    return error, cols, registered


def test_a_forced_table_refuses_and_changes_nothing(pg_dsn):  # noqa: F811
    error, cols, registered = _refused(pg_dsn, "ALTER TABLE tripplanner.outbox FORCE ROW LEVEL SECURITY")
    assert error and "enabled and not forced" in error and "outbox" in error, error
    assert cols == 0 and registered == 0


def test_a_shadow_policy_of_another_shape_refuses_and_changes_nothing(pg_dsn):  # noqa: F811
    wide = "CREATE POLICY sessions_tenant_shadow ON tripplanner.sessions FOR SELECT TO PUBLIC USING (true)"
    error, cols, registered = _refused(pg_dsn, wide)
    assert error and "another shape" in error and "sessions" in error, error
    assert cols == 0 and registered == 0
