"""tp-027 replay (AA-1046 / AA-1038): role booking loses the tp-009 grants of the retired inquiry.submitted contract,
applied the way Migration Apply / DB Migrate applies it (as tp_owner, after tp-013 … tp-026).

Runs only with ``--pg-dsn`` (a SUPERUSER DSN of a throwaway LOCAL Postgres), on the tp-025 stand-in
(test_tp025_pg._database: tp-001 … tp-025 with seeded inquiry rows), then tp-026 and tp-027 as tp_owner.

Checks: the contract shape (no directive line, one BEGIN … COMMIT, no DROP); booking reads the view before the file
and, through its own login, gets 42501 on the view, the schema and the table after it; no privilege of booking is left
on the schema, its relations or sequences, nor in a default privilege; the view, its rows and its other readers stay
(tripplanner, cis_app on the ops view); the guards refuse a run as another role and a booking grant left behind;
registration once and a re-run as tp_owner being a no-op. Invented values only.
"""
from __future__ import annotations

import re

import pytest

from test_tp013_pg import pg_dsn  # noqa: F401  (fixture of the tp-013 replay: --pg-dsn, local only)
from test_tp013_pg import MIGRATIONS, run
from test_tp025_pg import _as_owner, _database, _drop, _in_rollback

asyncpg = pytest.importorskip("asyncpg")

TP026 = MIGRATIONS / "026_lead_outbox_delivery.sql"
TP027 = MIGRATIONS / "027_revoke_booking_inquiry_grant.sql"
VIEW_COLUMNS = ["id", "schema_version", "idempotency_key", "trip_id", "payload", "created_at"]


def _code(sql: str) -> str:
    return "\n".join(ln for ln in sql.splitlines() if not ln.lstrip().startswith("--"))


async def _booking_reads_view(dsn: str) -> int:
    bk = await asyncpg.connect(dsn)
    try:
        return await bk.fetchval("SELECT count(*) AS n FROM tripplanner.v_inquiry_outbox")
    finally:
        await bk.close()


@pytest.fixture(scope="module")
def db(pg_dsn):  # noqa: F811
    ctx, error = _database(pg_dsn)
    try:
        assert error is None, error

        async def apply():
            admin = await asyncpg.connect(ctx["admin_dsn"])
            try:
                await _as_owner(admin, TP026)
                ctx["rows_before"] = await _booking_reads_view(ctx["bk_dsn"])   # tp-009 grant still in force
                await _as_owner(admin, TP027)
                await _as_owner(admin, TP027)            # re-run: a no-op
            finally:
                await admin.close()
        run(apply())
        yield ctx
    finally:
        _drop(pg_dsn, ctx["name"])


def test_contract_shape():
    sql = TP027.read_text(encoding="utf-8")
    assert not re.search(r"^--\s*migrate:", sql, re.M), "a contract file has no directive line"
    code = _code(sql)
    assert code.count("BEGIN;") == 1 and code.count("COMMIT;") == 1
    assert not re.search(r"\bDROP\s+(VIEW|TABLE|SCHEMA)\b", code), "the view drop is a later destructive file"
    assert not re.search(r"\bGRANT\b", code), "tp-027 only revokes"
    assert "REVOKE ALL ON tripplanner.v_inquiry_outbox FROM booking;" in code
    assert "REVOKE USAGE ON SCHEMA tripplanner FROM booking;" in code


def test_booking_read_the_view_before(db):
    assert db["rows_before"] == 2                    # the two seeded inquiry rows of the tp-025 stand-in


def test_booking_is_refused_through_its_own_login(db):
    async def body():
        bk = await asyncpg.connect(db["bk_dsn"])
        try:
            for sql in ("SELECT 1 FROM tripplanner.v_inquiry_outbox LIMIT 1",
                        "SELECT 1 FROM tripplanner.outbox LIMIT 1",
                        "SELECT 1 FROM tripplanner.sessions LIMIT 1"):
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await bk.fetch(sql)
        finally:
            await bk.close()
    run(body())


def test_no_privilege_of_booking_left_in_the_schema(db):
    async def body():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            row = await su.fetchrow(
                "SELECT has_schema_privilege('booking', 'tripplanner', 'USAGE') AS usage, "
                "       has_schema_privilege('booking', 'tripplanner', 'CREATE') AS create_")
            assert (row["usage"], row["create_"]) == (False, False)
            rels = await su.fetch(
                "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'tripplanner' AND c.relkind IN ('r', 'p', 'v', 'm', 'f') "
                "AND has_table_privilege('booking', c.oid, 'SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, "
                "TRIGGER')")
            assert [r["relname"] for r in rels] == []
            acl = await su.fetchval(
                "SELECT count(*) AS n FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "CROSS JOIN LATERAL aclexplode(c.relacl) a "
                "WHERE n.nspname = 'tripplanner' AND a.grantee = 'booking'::regrole")
            assert acl == 0
            schema_acl = await su.fetchval(
                "SELECT count(*) AS n FROM pg_namespace n CROSS JOIN LATERAL aclexplode(n.nspacl) a "
                "WHERE n.nspname = 'tripplanner' AND a.grantee = 'booking'::regrole")
            assert schema_acl == 0
        finally:
            await su.close()
    run(body())


def test_view_rows_and_other_readers_stay(db):
    async def body():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            cols = [r["attname"] for r in await su.fetch(
                "SELECT attname FROM pg_attribute WHERE attrelid = 'tripplanner.v_inquiry_outbox'::regclass "
                "AND attnum > 0 AND NOT attisdropped ORDER BY attnum")]
            assert cols == VIEW_COLUMNS
            assert await su.fetchval("SELECT count(*) AS n FROM tripplanner.v_inquiry_outbox") == db["rows_before"]
            comment = await su.fetchval("SELECT obj_description('tripplanner.v_inquiry_outbox'::regclass, 'pg_class')")
            assert "tp-027" in comment and "no consumer" in comment
            row = await su.fetchrow(
                "SELECT has_table_privilege('tripplanner', 'tripplanner.v_inquiry_outbox', 'SELECT') AS tp, "
                "       has_schema_privilege('cis_app', 'tripplanner', 'USAGE') AS cis_usage, "
                "       has_table_privilege('cis_app', 'tripplanner.v_ops_traffic_daily', 'SELECT') AS cis_view")
            assert (row["tp"], row["cis_usage"], row["cis_view"]) == (True, True, True)
        finally:
            await su.close()
        tp = await asyncpg.connect(db["tp_dsn"])
        try:
            assert await tp.fetchval("SELECT count(*) AS n FROM tripplanner.v_inquiry_outbox") == db["rows_before"]
        finally:
            await tp.close()
    run(body())


def test_registered_once(db):
    async def body():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            assert await su.fetchval("SELECT count(*) AS n FROM shared.schema_versions WHERE version = 'tp-027'") == 1
        finally:
            await su.close()
    run(body())


def test_refuses_to_run_as_another_role(db):
    async def body():
        admin = await asyncpg.connect(db["admin_dsn"])
        try:
            async def attempt(conn):
                with pytest.raises(asyncpg.RaiseError, match="expected to run as tp_owner"):
                    await conn.execute(_code(TP027.read_text(encoding="utf-8")).replace("BEGIN;", "")
                                       .replace("COMMIT;", ""))
            await _in_rollback(admin, attempt)
        finally:
            await admin.close()
    run(body())


def test_guard_refuses_a_booking_grant_left_on_another_relation(db):
    """A grant tp-027 does not revoke (another relation, given by hand) fails the file instead of passing silently."""
    async def body():
        admin = await asyncpg.connect(db["admin_dsn"])
        try:
            async def attempt(conn):
                await conn.execute("SET LOCAL ROLE tp_owner")
                await conn.execute("GRANT SELECT ON tripplanner.v_ops_traffic_daily TO booking")
                with pytest.raises(asyncpg.RaiseError, match="v_ops_traffic_daily"):
                    await conn.execute(_code(TP027.read_text(encoding="utf-8")).replace("BEGIN;", "")
                                       .replace("COMMIT;", ""))
            await _in_rollback(admin, attempt)
        finally:
            await admin.close()
    run(body())
