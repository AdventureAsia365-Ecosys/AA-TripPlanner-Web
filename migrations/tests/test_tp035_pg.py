"""tp-035 replay (AA-1139, the TripPlanner module of the agency app): tripplanner.lead_webhook_key, applied the way
Migration Apply applies it (as tp_owner, after tp-013 … tp-031; tp-035 twice).

Runs only with ``--pg-dsn`` (a SUPERUSER DSN of a throwaway LOCAL Postgres). The database is tp-031's
(``test_tp031_pg.erasure_database``), with no seed rows, then tp-035 as tp_owner twice (tp-035 needs nothing of
tp-032 … tp-034).

Checks: the expand header (root ADR 0017 d3) and the Infra-safe shape (one BEGIN … COMMIT, no DO block, no ALTER /
REVOKE); the columns, key, CHECKs, owner, comment, no row-level security (no tenant_id column: tp-029's scope spelling)
and the registration; the runtime role runs the code's statements (backend/assembly/planner_admin.py: read, rotate,
written out here) and the CHECKs refuse a bad scope, version or actor; cis_app sees nothing; a re-run is a no-op.
"""
from __future__ import annotations

import pytest

from test_tp013_pg import pg_dsn  # noqa: F401  (fixture of the tp-013 replay: --pg-dsn, local only)
from test_tp013_pg import MIGRATIONS, run
from test_tp025_pg import _as_owner, _drop
from test_tp031_pg import erasure_database

asyncpg = pytest.importorskip("asyncpg")

TP035 = MIGRATIONS / "035_lead_webhook_key.sql"
TABLE = "tripplanner.lead_webhook_key"
TENANT = "7a1c0de5-0000-4000-8000-000000001139"
ACTOR = "crm-member:7a1c0de5-0000-4000-8000-0000000000aa"

# The statements of backend/assembly/planner_admin.py (AA-1139 code PR), written out here so this migration test does
# not depend on the code that ships after it (MIG-4).
READ = f"SELECT version, rotated_at FROM {TABLE} WHERE scope = $1"
ROTATE = (f"INSERT INTO {TABLE} AS k (scope, version, rotated_by) VALUES ($1, 1, $2) "
          "ON CONFLICT (scope) DO UPDATE SET version = k.version + 1, rotated_at = now(), "
          "rotated_by = EXCLUDED.rotated_by RETURNING version, rotated_at")


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
                await _as_owner(admin, TP035)
                await _as_owner(admin, TP035)                        # re-runnable
            finally:
                await admin.close()

        run(apply())
        yield ctx
    finally:
        _drop(pg_dsn, ctx["name"])


def test_file_is_an_expand_file_in_one_transaction_block():
    text = TP035.read_text(encoding="utf-8")
    assert text.splitlines()[0] == "-- migrate: expand"
    code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("--"))
    assert code.count("BEGIN;") == 1 and code.count("COMMIT;") == 1
    assert "DO $$" not in code and "REVOKE" not in code and "ALTER TABLE" not in code and "tenant_id" not in code


def test_table_shape_owner_comment_and_registration(db):
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            registered = await su.fetchval("SELECT count(*) AS n FROM shared.schema_versions WHERE version = 'tp-035'")
            cols = await su.fetch(
                "SELECT column_name, data_type, is_nullable FROM information_schema.columns"
                " WHERE table_schema = 'tripplanner' AND table_name = 'lead_webhook_key' ORDER BY ordinal_position")
            pk = await su.fetchval(
                "SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint WHERE conname = 'lead_webhook_key_pkey'")
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
    assert cols == [("scope", "text", "NO"), ("version", "integer", "NO"),
                    ("rotated_at", "timestamp with time zone", "NO"), ("rotated_by", "text", "NO")]
    assert pk == "PRIMARY KEY (scope)"
    assert checks == ["lead_webhook_key_actor_chk", "lead_webhook_key_scope_chk", "lead_webhook_key_version_chk"]
    assert owner == "tp_owner" and comment.startswith("tp-035 (AA-1139)")
    assert rls is False and cis is False


def test_runtime_role_runs_the_code_statements(db):
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            async with su.transaction():
                await su.execute("SET LOCAL ROLE tripplanner")
                scope = f"tenant:{TENANT}"
                before = await su.fetchrow(READ, scope)
                first = await su.fetchrow(ROTATE, scope, ACTOR)
                second = await su.fetchrow(ROTATE, scope, ACTOR)
                after = await su.fetchrow(READ, scope)
                other = await su.fetchrow(READ, "tenant:00000000-0000-4000-8000-000000000000")
                raise _Rollback((before, first["version"], second["version"], after["version"], other))
        except _Rollback as r:
            return r.value
        finally:
            await su.close()

    assert run(check()) == (None, 1, 2, 2, None)


@pytest.mark.parametrize("scope,version,actor", [
    ("tenant:not-a-uuid", 1, ACTOR),
    ("app", 1, ACTOR),
    (f"tenant:{TENANT}", 0, ACTOR),
    (f"tenant:{TENANT}", 1, "admin:someone"),
    (f"tenant:{TENANT}", 1, "crm-member:Jane Example"),
])
def test_checks_refuse_bad_rows(db, scope, version, actor):
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            async with su.transaction():
                await su.execute("SET LOCAL ROLE tripplanner")
                with pytest.raises(asyncpg.CheckViolationError):
                    await su.execute(f"INSERT INTO {TABLE} (scope, version, rotated_by) VALUES ($1, $2, $3)",
                                     scope, version, actor)
                raise _Rollback(True)
        except _Rollback as r:
            return r.value
        finally:
            await su.close()

    assert run(check())
