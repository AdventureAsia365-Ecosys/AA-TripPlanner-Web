"""tp-028 replay (AA-1038 R5, the FORCE slice): enforcing tenant row-level security on tripplanner.sessions,
trip_drafts, trip_events and outbox, applied the way Migration Apply / DB Migrate applies it (as tp_owner, after
tp-013 … tp-027).

Runs only with ``--pg-dsn`` (a SUPERUSER DSN of a throwaway LOCAL Postgres), on the tp-025 stand-in
(test_tp025_pg._database: tp-001 … tp-025 with demo rows seeded before tp-025), then tp-026, tp-027 and tp-028 (twice:
the re-run is a no-op) as tp_owner, then invented rows of a second tenant written by the superuser.

Checks: the contract shape (no directive line, one BEGIN … COMMIT, FORCE, no policy TO PUBLIC, no bare true, every
current_setting('app.tenant_id') inside NULLIF); the catalog (forced, exactly three policies of their shape, the
default without the demo fallback, the invoker view, the out-of-scope tables untouched); as the runtime role
``tripplanner`` through its own login: no tenant context (unset, '', after RESET) and a forged one see no row, tenant
A sees only A's rows and tenant B only B's, an INSERT without a context fails, an INSERT / UPDATE that names the other
tenant fails WITH CHECK, an UPDATE / DELETE reaches only the bound tenant's rows, a context that is not a uuid fails
closed, the retired inquiry view answers with the caller's policies; the system scope (the jobs Lambda) reads and
writes every row and nothing else spelled like it does; the owner tp_owner reads every row without a bound tenant and
writes only under the system scope; cis_app keeps every row of the ops views; the guards refuse another role, a
policy added by hand and a runtime role that bypasses row security; registration once. Invented values only.

``forced_database`` is the stand-in the backend integration tests (backend/tests/*_pg.py) run the code on.
"""
from __future__ import annotations

import re
import uuid

import pytest

from test_tp013_pg import pg_dsn  # noqa: F401  (fixture of the tp-013 replay: --pg-dsn, local only)
from test_tp013_pg import MIGRATIONS, run
from test_tp025_pg import DEMO, SEEDED, TABLES, UNTOUCHED, _as_owner, _database, _drop, _in_rollback

asyncpg = pytest.importorskip("asyncpg")

TP026 = MIGRATIONS / "026_lead_outbox_delivery.sql"
TP027 = MIGRATIONS / "027_revoke_booking_inquiry_grant.sql"
TP028 = MIGRATIONS / "028_force_tenant_rls.sql"
OTHER = "5e1f0a7e-0000-4000-8000-0000000000b2"            # an invented second tenant
FORGED = "7e57c0de-0000-4000-8000-00000000f028"
OTHER_ROWS = {"sessions": 1, "trip_drafts": 1, "trip_events": 2, "outbox": 1}
ALL_ROWS = {t: SEEDED[t] + OTHER_ROWS[t] for t in TABLES}
NONE = dict.fromkeys(TABLES, 0)
TENANT_EXPR = "(tenant_id = (NULLIF(current_setting('app.tenant_id'::text, true), ''::text))::uuid)"
SYSTEM_EXPR = "(NULLIF(current_setting('app.tenant_scope'::text, true), ''::text) = 'system'::text)"
OWNER_EXPR = ("((NULLIF(current_setting('app.tenant_id'::text, true), ''::text) IS NULL) OR "
              "(tenant_id = (NULLIF(current_setting('app.tenant_id'::text, true), ''::text))::uuid))")
DEFAULT_EXPR = "(NULLIF(current_setting('app.tenant_id'::text, true), ''::text))::uuid"


def _code(sql: str) -> str:
    return "\n".join(ln for ln in sql.splitlines() if not ln.lstrip().startswith("--"))


def _body(path) -> str:
    """The file without comments and without its BEGIN / COMMIT, to run inside a test's own transaction."""
    return _code(path.read_text(encoding="utf-8")).replace("BEGIN;", "").replace("COMMIT;", "")


async def _seed_other(su) -> dict:
    """Rows of the second tenant (written by the superuser, which bypasses row security)."""
    s, t = uuid.uuid4(), uuid.uuid4()
    await su.execute("INSERT INTO tripplanner.sessions (id, tenant_id) VALUES ($1, $2::uuid)", s, OTHER)
    await su.execute("INSERT INTO tripplanner.trip_drafts (id, session_id, tenant_id) VALUES ($1, $2, $3::uuid)",
                     t, s, OTHER)
    await su.execute("INSERT INTO tripplanner.trip_events (trip_id, session_id, event_type, payload, tenant_id) "
                     "VALUES ($1, $2, 'add_day', '{}', $3::uuid), ($1, $2, 'reorder', '{}', $3::uuid)", t, s, OTHER)
    await su.execute("INSERT INTO tripplanner.outbox (event, schema_version, idempotency_key, aggregate_id, payload, "
                     "tenant_id) VALUES ('inquiry.submitted', 1, $1, $2, '{}', $3::uuid)", f"{t}:b1", t, OTHER)
    return {"other_session": s, "other_trip": t}


def forced_database(pg_dsn, *, extra_sql: str = ""):  # noqa: F811
    """tp-025 stand-in (+ `extra_sql` before tp-025), then tp-026, tp-027 and tp-028 as tp_owner (tp-028 twice).
    Returns (context, the first error or None); the caller drops ``context["name"]``."""
    ctx, error = _database(pg_dsn, extra_sql=extra_sql)
    if error:
        return ctx, error

    async def apply():
        admin = await asyncpg.connect(ctx["admin_dsn"])
        try:
            for f in (TP026, TP027, TP028, TP028):
                await _as_owner(admin, f)
        finally:
            await admin.close()

    try:
        run(apply())
    except asyncpg.PostgresError as e:
        return ctx, f"{type(e).__name__}: {e}"
    return ctx, None


@pytest.fixture(scope="module")
def db(pg_dsn):  # noqa: F811
    ctx, error = forced_database(pg_dsn)
    try:
        assert error is None, error

        async def seed():
            su = await asyncpg.connect(ctx["su_dsn"])
            try:
                ctx.update(await _seed_other(su))
            finally:
                await su.close()
        run(seed())
        yield ctx
    finally:
        _drop(pg_dsn, ctx["name"])


async def _counts(conn) -> dict:
    return {t: await conn.fetchval(f"SELECT count(*) AS n FROM tripplanner.{t}") for t in TABLES}


async def _bind(conn, tenant) -> None:
    await conn.execute("SELECT set_config('app.tenant_id', $1, false)", tenant)


async def _runtime(db, body):
    """``body(conn)`` on the runtime role's own login, in a transaction that is always rolled back."""
    tp = await asyncpg.connect(db["tp_dsn"])
    try:
        return await _in_rollback(tp, body)
    finally:
        await tp.close()


# ── the file ─────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_file_is_a_contract_file_in_one_transaction_block():
    text = TP028.read_text(encoding="utf-8")
    assert not [ln for ln in text.splitlines() if re.match(r"^\s*--\s*migrate:", ln)]
    code = _code(text)
    assert code.count("BEGIN;") == 1 and code.count("COMMIT;") == 1
    assert "ON CONFLICT (version) DO NOTHING" in code and "'tp-028'" in code
    assert not re.search(r"\bDROP\s+(TABLE|VIEW|SCHEMA|COLUMN)\b", code, re.I), "contract, not destructive"
    for t in TABLES:
        assert f"ALTER TABLE tripplanner.{t:<11} FORCE ROW LEVEL SECURITY;" in code, t


def test_policy_text_follows_the_rls_catalogue_rules():
    """Rules b (never TO PUBLIC), d (every current_setting('app.tenant_id') inside NULLIF(..., '')), t (no bare
    true); the tenant policy has no unset branch."""
    code = _code(TP028.read_text(encoding="utf-8"))
    upper = code.upper()
    assert not re.search(r"\bTO\s+PUBLIC\b", upper)
    assert not re.search(r"\b(USING|CHECK)\s*\(\s*TRUE\s*\)", upper)
    code = re.sub(r"LIKE\s+'(?:[^']|'')*'", "LIKE ''", code)
    # plain SQL ('app.tenant_id', '') and the format() strings of the DO block (''app.tenant_id'', '''')
    settings = re.findall(r"current_setting\s*\(\s*'{1,2}app\.tenant_id", code)
    wrapped = re.findall(r"NULLIF\s*\(\s*current_setting\s*\(\s*'{1,2}app\.tenant_id'{1,2},\s*true\s*\)\s*,\s*'{2,4}\s*\)",
                         code)
    assert settings and len(settings) == len(wrapped), (len(settings), len(wrapped))


def test_out_of_scope_tables_are_not_named():
    code = _code(TP028.read_text(encoding="utf-8"))
    for t in UNTOUCHED:
        assert not re.search(rf"\btripplanner\.{t}\b", code), t


# ── the catalog after tp-028 ─────────────────────────────────────────────────────────────────────────────────────────

def test_forced_policies_default_and_view(db):
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            rel = await su.fetch(
                "SELECT c.relname::text AS t, c.relrowsecurity AS rls, c.relforcerowsecurity AS forced, "
                "pg_get_expr(d.adbin, d.adrelid) AS dflt, a.attnotnull AS notnull, col_description(c.oid, a.attnum) AS c "
                "FROM pg_class c JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'tenant_id' "
                "LEFT JOIN pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum "
                "WHERE c.relnamespace = 'tripplanner'::regnamespace AND c.relkind IN ('r', 'p')")
            pols = await su.fetch(
                "SELECT tablename::text AS t, policyname::text AS p, permissive, cmd, roles::text[] AS roles, "
                "qual, with_check FROM pg_policies WHERE schemaname = 'tripplanner'")
            view = await su.fetchval("SELECT reloptions::text[] AS o FROM pg_class "
                                     "WHERE oid = 'tripplanner.v_inquiry_outbox'::regclass")
            untouched = await su.fetch(
                "SELECT c.relname::text AS t, c.relrowsecurity AS rls FROM pg_class c "
                "WHERE c.relnamespace = 'tripplanner'::regnamespace AND c.relname = ANY($1::text[])", list(UNTOUCHED))
            return rel, pols, view, untouched
        finally:
            await su.close()

    rel, pols, view, untouched = run(check())
    assert sorted(r["t"] for r in rel) == sorted(TABLES)
    for r in rel:
        assert (r["rls"], r["forced"], r["notnull"]) == (True, True, True), dict(r)
        assert r["dflt"] == DEFAULT_EXPR and DEMO not in r["dflt"], r["dflt"]
        assert "tp-028" in r["c"] and f"{r['t']}_system" in r["c"], r["c"]
    got = {(p["t"], p["p"]): (p["permissive"], p["cmd"], sorted(p["roles"]), p["qual"], p["with_check"]) for p in pols}
    want = {}
    for t in TABLES:
        want[(t, f"{t}_tenant")] = ("PERMISSIVE", "ALL", ["tripplanner"], TENANT_EXPR, TENANT_EXPR)
        want[(t, f"{t}_system")] = ("PERMISSIVE", "ALL", ["tp_owner", "tripplanner"], SYSTEM_EXPR, SYSTEM_EXPR)
        want[(t, f"{t}_owner_read")] = ("PERMISSIVE", "SELECT", ["tp_owner"], OWNER_EXPR, None)
    assert got == want
    assert view == ["security_invoker=true"]
    assert untouched and all(r["rls"] is False for r in untouched), [dict(r) for r in untouched]


def test_registered_once(db):
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            return await su.fetchval("SELECT count(*) AS n FROM shared.schema_versions WHERE version = 'tp-028'")
        finally:
            await su.close()

    assert run(check()) == 1


# ── the runtime role ─────────────────────────────────────────────────────────────────────────────────────────────────

def test_runtime_sees_nothing_without_a_tenant_and_only_its_tenant_with_one(db):
    async def body(conn):
        seen = {"bypass": await conn.fetchval("SELECT rolbypassrls OR rolsuper AS b FROM pg_roles "
                                              "WHERE rolname = current_user"),
                "unset": await _counts(conn)}
        await conn.execute("SET app.tenant_id = ''")
        seen["empty"] = await _counts(conn)
        await _bind(conn, FORGED)
        seen["forged"] = await _counts(conn)
        await _bind(conn, DEMO)
        seen["demo"] = await _counts(conn)
        seen["demo_foreign"] = await conn.fetchval(
            "SELECT (SELECT count(*) FROM tripplanner.sessions WHERE tenant_id <> $1::uuid) "
            "+ (SELECT count(*) FROM tripplanner.trip_events WHERE tenant_id <> $1::uuid) AS n", DEMO)
        await _bind(conn, OTHER)
        seen["other"] = await _counts(conn)
        await conn.execute("RESET app.tenant_id")                         # what tenant.unbind does
        seen["reset"] = await _counts(conn)
        return seen

    seen = run(_runtime(db, body))
    assert seen.pop("bypass") is False
    assert seen == {"unset": NONE, "empty": NONE, "forged": NONE, "demo": SEEDED, "demo_foreign": 0,
                    "other": OTHER_ROWS, "reset": NONE}


def test_runtime_insert_needs_a_tenant_context(db):
    """No context: the default is NULL, and the row fails the policy's WITH CHECK (checked before NOT NULL)."""
    async def body(conn):
        try:
            await conn.execute("INSERT INTO tripplanner.sessions (id) VALUES (gen_random_uuid())")
        except (asyncpg.InsufficientPrivilegeError, asyncpg.NotNullViolationError) as e:
            return str(e)
        return None

    error = run(_runtime(db, body))
    assert error and ("row-level security policy" in error or "tenant_id" in error), error


def test_runtime_insert_without_tenant_id_takes_the_bound_tenant(db):
    async def body(conn):
        await _bind(conn, OTHER)
        sid = await conn.fetchval("INSERT INTO tripplanner.sessions (id) VALUES (gen_random_uuid()) RETURNING id")
        tid = await conn.fetchval("INSERT INTO tripplanner.trip_drafts (session_id) VALUES ($1) RETURNING id", sid)
        await conn.execute("INSERT INTO tripplanner.trip_events (trip_id, session_id, event_type, payload) "
                           "VALUES ($1, $2, 'add_day', '{}')", tid, sid)
        await conn.execute("INSERT INTO tripplanner.outbox (event, schema_version, idempotency_key, aggregate_id, "
                           "payload) VALUES ('inquiry.submitted', 1, $1, $2, '{}')", f"{tid}:x", tid)
        return {t: sorted({str(r["tenant_id"]) for r in await conn.fetch(f"SELECT tenant_id FROM tripplanner.{t}")})
                for t in TABLES}

    assert run(_runtime(db, body)) == {t: [OTHER] for t in TABLES}


@pytest.mark.parametrize("sql", [
    "INSERT INTO tripplanner.sessions (id, tenant_id) VALUES (gen_random_uuid(), $1::uuid)",
    "INSERT INTO tripplanner.outbox (event, schema_version, idempotency_key, aggregate_id, payload, tenant_id) "
    "VALUES ('inquiry.submitted', 1, gen_random_uuid()::text, gen_random_uuid(), '{}', $1::uuid)",
    "UPDATE tripplanner.trip_drafts SET tenant_id = $1::uuid",
])
def test_runtime_cannot_write_a_row_of_another_tenant(db, sql):
    async def body(conn):
        await _bind(conn, DEMO)
        try:
            await conn.execute(sql, OTHER)
        except asyncpg.InsufficientPrivilegeError as e:
            return str(e)
        return None

    error = run(_runtime(db, body))
    assert error and "row-level security policy" in error, error


def test_runtime_update_and_delete_reach_only_the_bound_tenant(db):
    async def body(conn):
        await _bind(conn, OTHER)
        out = [await conn.execute("UPDATE tripplanner.trip_drafts SET status = 'draft'"),
               await conn.execute("DELETE FROM tripplanner.trip_events"),
               await conn.execute("UPDATE tripplanner.outbox SET redacted_at = now()"),
               await conn.execute("DELETE FROM tripplanner.sessions WHERE id = $1", db["session"])]   # a demo session
        await conn.execute("RESET app.tenant_id")
        out.append(await conn.execute("UPDATE tripplanner.outbox SET redacted_at = now()"))       # no context
        return out

    assert run(_runtime(db, body)) == ["UPDATE 1", "DELETE 2", "UPDATE 1", "DELETE 0", "UPDATE 0"]


def test_a_context_that_is_not_a_uuid_fails_closed(db):
    async def body(conn):
        await _bind(conn, "not-a-tenant")
        try:
            await conn.fetchval("SELECT count(*) AS n FROM tripplanner.trip_drafts")
        except asyncpg.InvalidTextRepresentationError:
            return "refused"
        return "read"

    assert run(_runtime(db, body)) == "refused"


def test_the_retired_inquiry_view_answers_with_the_callers_policies(db):
    async def body(conn):
        seen = {"unset": await conn.fetchval("SELECT count(*) AS n FROM tripplanner.v_inquiry_outbox")}
        await _bind(conn, OTHER)
        seen["other"] = await conn.fetchval("SELECT count(*) AS n FROM tripplanner.v_inquiry_outbox")
        await _bind(conn, DEMO)
        seen["demo"] = await conn.fetchval("SELECT count(*) AS n FROM tripplanner.v_inquiry_outbox")
        return seen

    assert run(_runtime(db, body)) == {"unset": 0, "other": OTHER_ROWS["outbox"], "demo": SEEDED["outbox"]}


# ── the system scope (the jobs Lambda) ───────────────────────────────────────────────────────────────────────────────

def test_system_scope_reads_and_writes_every_tenant(db):
    async def body(conn):
        await conn.execute("SELECT set_config('app.tenant_scope', 'system', false)")
        seen = {"system": await _counts(conn),
                "update": await conn.execute("UPDATE tripplanner.outbox SET redacted_at = now()")}
        await conn.execute("RESET app.tenant_scope")
        seen["reset"] = await _counts(conn)
        return seen

    assert run(_runtime(db, body)) == {"system": ALL_ROWS, "update": f"UPDATE {ALL_ROWS['outbox']}", "reset": NONE}


@pytest.mark.parametrize("value", ["", "System", " system", "system ", "tenant", DEMO])
def test_nothing_else_opens_the_system_scope(db, value):
    async def body(conn):
        await conn.execute("SELECT set_config('app.tenant_scope', $1, false)", value)
        return await _counts(conn)

    assert run(_runtime(db, body)) == NONE


# ── the owner and the owner-owned views ──────────────────────────────────────────────────────────────────────────────

def test_owner_reads_every_row_without_a_tenant_and_writes_only_under_the_system_scope(db):
    async def check():
        admin = await asyncpg.connect(db["admin_dsn"])
        try:
            async def body(conn):
                await conn.execute("SET LOCAL ROLE tp_owner")
                seen = {"read": await _counts(conn),
                        "update": await conn.execute("UPDATE tripplanner.trip_drafts SET status = 'draft'")}
                await conn.execute("SELECT set_config('app.tenant_id', $1, true)", OTHER)
                seen["bound"] = await _counts(conn)
                await conn.execute("SELECT set_config('app.tenant_id', '', true)")
                await conn.execute("SELECT set_config('app.tenant_scope', 'system', true)")
                seen["system_update"] = await conn.execute("UPDATE tripplanner.trip_drafts SET status = 'draft'")
                return seen
            return await _in_rollback(admin, body)
        finally:
            await admin.close()

    assert run(check()) == {"read": ALL_ROWS, "update": "UPDATE 0", "bound": OTHER_ROWS,
                            "system_update": f"UPDATE {ALL_ROWS['trip_drafts']}"}


def test_cis_app_keeps_every_row_of_the_ops_views(db):
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            async def body(conn):
                await conn.execute("SET LOCAL ROLE cis_app")
                totals = {r["metric"]: r["n"] for r in await conn.fetch(
                    "SELECT metric, n FROM tripplanner.v_ops_data_totals")}
                keys = await conn.fetchval("SELECT count(*) AS n FROM tripplanner.v_ops_inquiry_keys")
                return totals, keys
            return await _in_rollback(su, body)
        finally:
            await su.close()

    totals, keys = run(check())
    assert totals["sessions"] == ALL_ROWS["sessions"] and totals["trip_drafts"] == ALL_ROWS["trip_drafts"], totals
    assert keys == ALL_ROWS["outbox"]


# ── the guards ───────────────────────────────────────────────────────────────────────────────────────────────────────

def test_refuses_to_run_as_another_role(db):
    async def check():
        admin = await asyncpg.connect(db["admin_dsn"])
        try:
            async def body(conn):
                with pytest.raises(asyncpg.RaiseError, match="expected to run as tp_owner"):
                    await conn.execute(_body(TP028))
            await _in_rollback(admin, body)
        finally:
            await admin.close()
    run(check())


def test_refuses_a_policy_added_by_hand(db):
    async def check():
        admin = await asyncpg.connect(db["admin_dsn"])
        try:
            async def body(conn):
                await conn.execute("SET LOCAL ROLE tp_owner")
                await conn.execute("CREATE POLICY trip_events_extra ON tripplanner.trip_events FOR SELECT "
                                   "TO tripplanner USING (tenant_id IS NOT NULL)")
                with pytest.raises(asyncpg.RaiseError, match="trip_events"):
                    await conn.execute(_body(TP028))
            await _in_rollback(admin, body)
        finally:
            await admin.close()
    run(check())


def test_refuses_a_runtime_role_that_bypasses_row_security(db):
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            async def body(conn):
                await conn.execute("ALTER ROLE tripplanner BYPASSRLS")
                await conn.execute("SET LOCAL ROLE tp_owner")
                with pytest.raises(asyncpg.RaiseError, match="bypasses row-level security"):
                    await conn.execute(_body(TP028))
            await _in_rollback(su, body)
        finally:
            await su.close()
    run(check())
