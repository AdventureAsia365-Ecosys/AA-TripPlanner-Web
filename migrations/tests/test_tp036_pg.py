"""tp-036 / tp-037 replay (AA-1152, S285 B2 option C and PR-29 T6): tripplanner.agency_prices and the trip event types
add_addon / remove_addon, applied the way Migration Apply applies them (as tp_owner, after tp-013 … tp-031; each file
twice).

Runs only with ``--pg-dsn`` (a SUPERUSER DSN of a throwaway LOCAL Postgres). The database is tp-031's
(``test_tp031_pg.erasure_database``), with no seed rows, then tp-036 and tp-037 as tp_owner twice (they need nothing of
tp-032 … tp-035).

Checks: tp-036 is an expand file in one transaction block (no DO block, no ALTER / REVOKE, no tenant_id), tp-037 a
contract file (no directive); the table's columns, key, CHECKs, owner, comment, no row-level security, cis_app sees
nothing; the runtime role runs the code's statements (backend/assembly/agency_prices.py, written out here: the newest
computed_at wins) and the CHECKs refuse a bad scope, currency or list; the runtime role writes the two new event types
and the CHECK still refuses an unknown one; both files are registered.
"""
from __future__ import annotations

import datetime as dt
import json

import pytest

from test_tp013_pg import pg_dsn  # noqa: F401  (fixture of the tp-013 replay: --pg-dsn, local only)
from test_tp013_pg import MIGRATIONS, run
from test_tp025_pg import _as_owner, _drop
from test_tp031_pg import _owner_file, erasure_database

asyncpg = pytest.importorskip("asyncpg")

TP036 = MIGRATIONS / "036_agency_prices.sql"
TP037 = MIGRATIONS / "037_trip_events_addons.sql"
TABLE = "tripplanner.agency_prices"
TENANT = "7a1c0de5-0000-4000-8000-000000001152"
SV = "sv:7d9f8b00-0000-4000-8000-0000000000c2"
AT_8 = dt.datetime(2027, 2, 1, 8, tzinfo=dt.timezone.utc)

# The statements of backend/assembly/agency_prices.py (AA-1152 code PR), written out here so this migration test does
# not depend on the code that ships after it (MIG-4).
UPSERT = (f"INSERT INTO {TABLE} AS p (scope, show_prices, currency, tours, addons, computed_at) "
          "VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6::timestamptz) "
          "ON CONFLICT (scope) DO UPDATE SET show_prices = EXCLUDED.show_prices, currency = EXCLUDED.currency, "
          "tours = EXCLUDED.tours, addons = EXCLUDED.addons, computed_at = EXCLUDED.computed_at, received_at = now() "
          "WHERE p.computed_at <= EXCLUDED.computed_at RETURNING computed_at")
READ = f"SELECT show_prices, currency, tours, addons, computed_at, received_at FROM {TABLE} WHERE scope = $1"


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
                for f in (TP036, TP036):
                    await _as_owner(admin, f)
                for f in (TP037, TP037):
                    await _owner_file(admin, f)
                    await admin.execute("RESET ROLE")
            finally:
                await admin.close()

        run(apply())
        yield ctx
    finally:
        _drop(pg_dsn, ctx["name"])


def test_tp036_is_an_expand_file_and_tp037_a_contract_file():
    text = TP036.read_text(encoding="utf-8")
    assert text.splitlines()[0] == "-- migrate: expand"
    code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("--"))
    assert code.count("BEGIN;") == 1 and code.count("COMMIT;") == 1
    assert "DO $$" not in code and "REVOKE" not in code and "ALTER TABLE" not in code and "tenant_id" not in code
    assert not TP037.read_text(encoding="utf-8").startswith("-- migrate:")


def test_table_shape_owner_comment_and_registration(db):
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            registered = await su.fetchval(
                "SELECT count(*) AS n FROM shared.schema_versions WHERE version IN ('tp-036', 'tp-037')")
            cols = await su.fetch(
                "SELECT column_name, data_type, is_nullable FROM information_schema.columns"
                " WHERE table_schema = 'tripplanner' AND table_name = 'agency_prices' ORDER BY ordinal_position")
            checks = await su.fetch(
                f"SELECT conname FROM pg_constraint WHERE conrelid = '{TABLE}'::regclass AND contype = 'c'"
                " ORDER BY conname")
            owner = await su.fetchval(f"SELECT pg_get_userbyid(relowner) AS o FROM pg_class WHERE oid = '{TABLE}'::regclass")
            comment = await su.fetchval(f"SELECT obj_description('{TABLE}'::regclass, 'pg_class') AS c")
            rls = await su.fetchval(f"SELECT relrowsecurity AS r FROM pg_class WHERE oid = '{TABLE}'::regclass")
            cis = await su.fetchval(f"SELECT has_table_privilege('cis_app', '{TABLE}', 'SELECT') AS s")
            return registered, [tuple(r) for r in cols], [r["conname"] for r in checks], owner, comment, rls, cis
        finally:
            await su.close()

    registered, cols, checks, owner, comment, rls, cis = run(check())
    assert registered == 2
    assert cols == [("scope", "text", "NO"), ("show_prices", "boolean", "NO"), ("currency", "text", "NO"),
                    ("tours", "jsonb", "NO"), ("addons", "jsonb", "NO"),
                    ("computed_at", "timestamp with time zone", "NO"), ("received_at", "timestamp with time zone", "NO")]
    assert checks == ["agency_prices_currency_chk", "agency_prices_lists_chk", "agency_prices_scope_chk"]
    assert owner == "tp_owner" and comment.startswith("tp-036 (AA-1152")
    assert rls is False and cis is False


def test_runtime_role_runs_the_code_statements_and_the_newest_list_wins(db):
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            async with su.transaction():
                await su.execute("SET LOCAL ROLE tripplanner")
                scope = f"tenant:{TENANT}"
                tours = json.dumps({"SYN-TOUR-0042": "1235"})
                addons = json.dumps({SV: "42"})
                first = await su.fetchval(UPSERT, scope, True, "USD", tours, addons, AT_8)
                stale = await su.fetchval(UPSERT, scope, False, "USD", "{}", "{}", AT_8 - dt.timedelta(hours=1))
                row = await su.fetchrow(READ, scope)
                newer = await su.fetchval(UPSERT, scope, False, "USD", "{}", "{}", AT_8 + dt.timedelta(hours=1))
                off = await su.fetchrow(READ, scope)
                raise _Rollback((first is not None, stale, row["show_prices"], json.loads(row["addons"]),
                                 newer is not None, off["show_prices"], json.loads(off["tours"])))
        except _Rollback as r:
            return r.value
        finally:
            await su.close()

    assert run(check()) == (True, None, True, {SV: "42"}, True, False, {})


@pytest.mark.parametrize("scope,currency,tours", [
    ("tenant:not-a-uuid", "USD", "{}"),
    ("app", "USD", "{}"),
    (f"tenant:{TENANT}", "usd", "{}"),
    (f"tenant:{TENANT}", "USD", "[]"),
])
def test_checks_refuse_bad_rows(db, scope, currency, tours):
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            async with su.transaction():
                await su.execute("SET LOCAL ROLE tripplanner")
                with pytest.raises(asyncpg.CheckViolationError):
                    await su.execute(UPSERT, scope, True, currency, tours, "{}", AT_8)
                raise _Rollback(True)
        except _Rollback as r:
            return r.value
        finally:
            await su.close()

    assert run(check())


def test_the_trip_log_takes_the_add_on_events(db):
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            definition = await su.fetchval(
                "SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint WHERE conname = 'trip_events_type_chk'")
            return definition
        finally:
            await su.close()

    definition = run(check())
    for kind in ("add_component", "remove_day", "add_addon", "remove_addon"):
        assert f"'{kind}'" in definition
    assert "'narrate'" not in definition
