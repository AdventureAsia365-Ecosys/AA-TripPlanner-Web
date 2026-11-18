"""tp-034 replay (AA-1126, S284 redesign of the AI notices): the two AI notice events in tripplanner.outbox and the
contract view tripplanner.v_ai_notices_v1 that CIS pulls, applied the way Migration Apply applies it (as tp_owner,
after tp-013 … tp-033; tp-034 twice).

Runs only with ``--pg-dsn`` (a SUPERUSER DSN of a throwaway LOCAL Postgres). The database is tp-033's
(``test_tp033_pg._database``: tp-028's forced database with the invented TARGET / OTHER rows, tp-029 … tp-033).

Checks: the contract shape of the file (no directive line, one BEGIN … COMMIT, self-registration); the CHECKs, the
view (columns, owner, comment) and its grants (SELECT to cis_app only, nothing to the runtime role, cis_app still
cannot read the outbox); the runtime role writes the code's statements (written out here: the TripPlanner code PR
ships after this file, MIG-4) under its tenant policy, once per key even across tenants; the CHECKs refuse a foreign
key shape or a payload with a customer; cis_app reads every tenant's ai.* rows through the view and no lead row; a
re-run is a no-op.
"""
from __future__ import annotations

import json
import re

import pytest

from test_tp013_pg import pg_dsn  # noqa: F401  (fixture of the tp-013 replay: --pg-dsn, local only)
from test_tp013_pg import MIGRATIONS, run
from test_tp025_pg import _drop, _in_rollback
from test_tp030_pg import OTHER, TARGET
from test_tp031_pg import _code, _owner_file
from test_tp033_pg import _database as tp033_database

asyncpg = pytest.importorskip("asyncpg")

TP034 = MIGRATIONS / "034_ai_notice_events.sql"
VIEW = "tripplanner.v_ai_notices_v1"
BIND = "SELECT set_config('app.tenant_id', $1, false)"
# The statement of backend/shared/tenant_sessions.py and spend_bell.py (AA-1126 code PR), written out here so this
# migration test does not depend on the code that ships after it (MIG-4).
WRITE = ("INSERT INTO tripplanner.outbox (tenant_id, event, schema_version, idempotency_key, aggregate_id, payload)"
         " VALUES ($1::uuid, $2, 1, $3, $1::uuid, $4::jsonb) ON CONFLICT (idempotency_key) DO NOTHING RETURNING id")
ALLOWANCE = ("ai.allowance_used", f"ai.allowance_used:{TARGET}:2026-11",
             {"kind": "tripplanner", "month": "2026-11", "resets_at": "2026-12-01"})
DEGRADED = ("ai.spend_degraded", "ai.spend_degraded:tenant_sessions:2026-11-18",
            {"check": "tenant_sessions", "stage": None, "day": "2026-11-18"})


@pytest.fixture(scope="module")
def db(pg_dsn):  # noqa: F811
    ctx, error = tp033_database(pg_dsn)
    try:
        assert error is None, error

        async def apply():
            admin = await asyncpg.connect(ctx["admin_dsn"])
            try:
                for f in (TP034, TP034):                                    # re-runnable
                    await _owner_file(admin, f)
            finally:
                await admin.close()

        run(apply())
        yield ctx
    finally:
        _drop(pg_dsn, ctx["name"])


async def _as_tenant(db, tenant, body):
    tp = await asyncpg.connect(db["tp_dsn"])
    try:
        await tp.execute(BIND, tenant)
        return await _in_rollback(tp, body)
    finally:
        await tp.close()


async def _write(conn, event, key, payload, tenant):
    return await conn.fetchval(WRITE, tenant, event, key, json.dumps(payload))


# ── the file ─────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_file_is_a_contract_file_in_one_transaction_block():
    text = TP034.read_text(encoding="utf-8")
    assert not [ln for ln in text.splitlines() if re.match(r"^\s*--\s*migrate:", ln)]
    code = _code(text)
    assert code.count("BEGIN;") == 1 and code.count("COMMIT;") == 1
    assert "ON CONFLICT (version) DO NOTHING" in code and "'tp-034'" in code
    assert not re.search(r"\bSECURITY\s+DEFINER\b", code)
    assert not re.search(r"\bDROP\s+(TABLE|SCHEMA|COLUMN|VIEW|FUNCTION)\b", code, re.I)
    assert not re.search(r"\bDELETE\s+FROM\b|\bTRUNCATE\b", code, re.I)
    assert "TO PUBLIC" not in code.upper()
    assert not re.search(r"\b(GRANT|INSERT\s+INTO|ALTER\s+TABLE)\b[^;]*\bshared\.notifications\b", code, re.I)


def test_view_shape_grants_checks_and_registration(db):
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            cols = await su.fetch("SELECT column_name, data_type FROM information_schema.columns "
                                  "WHERE table_schema = 'tripplanner' AND table_name = 'v_ai_notices_v1' "
                                  "ORDER BY ordinal_position")
            view = await su.fetchrow(f"SELECT pg_get_userbyid(c.relowner) AS owner, c.reloptions AS opts, "
                                     f"obj_description(c.oid, 'pg_class') AS comment FROM pg_class c "
                                     f"WHERE c.oid = '{VIEW}'::regclass")
            privs = {}
            for role in ("tripplanner", "cis_app"):
                for priv in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                    privs[(role, priv)] = await su.fetchval(
                        f"SELECT has_table_privilege('{role}', '{VIEW}', '{priv}') AS ok")
            outbox_cis = await su.fetchval("SELECT has_table_privilege('cis_app', 'tripplanner.outbox', 'SELECT') AS ok")
            checks = {r["conname"]: r["d"] for r in await su.fetch(
                "SELECT conname, pg_get_constraintdef(oid) AS d FROM pg_constraint "
                "WHERE conrelid = 'tripplanner.outbox'::regclass AND contype = 'c' AND convalidated")}
            registered = await su.fetchval("SELECT count(*) AS n FROM shared.schema_versions WHERE version = 'tp-034'")
            return [tuple(r) for r in cols], view, privs, outbox_cis, checks, registered
        finally:
            await su.close()

    cols, view, privs, outbox_cis, checks, registered = run(check())
    assert cols == [("id", "bigint"), ("tenant_id", "uuid"), ("event", "text"), ("notice_key", "text"),
                    ("payload", "jsonb"), ("created_at", "timestamp with time zone")]
    assert view["owner"] == "tp_owner" and view["opts"] is None                   # an owner view, not security_invoker
    assert view["comment"].startswith("tp-034 (AA-1126)")
    assert privs == {("tripplanner", p): False for p in ("SELECT", "INSERT", "UPDATE", "DELETE")} | {
        ("cis_app", "SELECT"): True, ("cis_app", "INSERT"): False, ("cis_app", "UPDATE"): False,
        ("cis_app", "DELETE"): False}
    assert outbox_cis is False
    assert "ai.allowance_used" in checks["outbox_event_chk"] and "lead.submitted" in checks["outbox_event_chk"]
    assert "outbox_ai_notice_chk" in checks and "outbox_lead_channel_chk" in checks
    assert registered == 1


# ── the runtime role writes, CIS reads ───────────────────────────────────────────────────────────────────────────────

def test_runtime_writes_each_key_once_even_from_another_tenant(db):
    async def as_target(conn):
        first = await _write(conn, *ALLOWANCE, TARGET)
        again = await _write(conn, *ALLOWANCE, TARGET)
        degraded = await _write(conn, *DEGRADED, TARGET)
        return first, again, degraded

    first, again, degraded = run(_as_tenant(db, TARGET, as_target))
    assert first is not None and again is None and degraded is not None

    async def commit_then_other():
        tp = await asyncpg.connect(db["tp_dsn"])
        try:
            await tp.execute(BIND, TARGET)
            async with tp.transaction():
                await _write(tp, *DEGRADED, TARGET)
            await tp.execute(BIND, OTHER)

            async def body(conn):
                same_day = await _write(conn, *DEGRADED, OTHER)                  # another tenant, same check and day
                seen = await conn.fetchval("SELECT count(*) AS n FROM tripplanner.outbox WHERE event LIKE 'ai.%'")
                return same_day, seen
            return await _in_rollback(tp, body)
        finally:
            await tp.close()

    try:
        same_day, seen = run(commit_then_other())
        assert same_day is None and seen == 0                     # ON CONFLICT DO NOTHING; OTHER sees no TARGET row
    finally:
        async def cleanup():
            su = await asyncpg.connect(db["su_dsn"])
            try:
                await su.execute("DELETE FROM tripplanner.outbox WHERE event LIKE 'ai.%'")
            finally:
                await su.close()
        run(cleanup())


def test_the_tenant_policy_refuses_an_event_for_another_tenant(db):
    async def body(conn):
        with pytest.raises(asyncpg.InsufficientPrivilegeError):                  # tp-028 outbox_tenant WITH CHECK
            await _write(conn, "ai.allowance_used", f"ai.allowance_used:{OTHER}:2026-11", ALLOWANCE[2], OTHER)
        return True

    assert run(_as_tenant(db, TARGET, body))


@pytest.mark.parametrize("event,key,payload", [
    ("ai.allowance_used", "ai.spend_degraded:tenant_sessions:2026-11-18", {"month": "2026-11"}),   # another event's key
    ("ai.allowance_used", "ai.allowance_used:Has Spaces", {"month": "2026-11"}),
    ("ai.spend_degraded", "ai.spend_degraded:tenant_sessions:2026-11-18", {"customer": {"name": "Invented Person"}}),
    ("ai.spend_degraded", "ai.spend_degraded:tenant_sessions:2026-11-19", ["not", "an", "object"]),
    ("ai.unknown_event", "ai.unknown_event:x", {}),
])
def test_checks_refuse_bad_ai_rows(db, event, key, payload):
    async def body(conn):
        with pytest.raises(asyncpg.CheckViolationError):
            await _write(conn, event, key, payload, TARGET)
        return True

    assert run(_as_tenant(db, TARGET, body))


def test_cis_app_reads_every_tenants_ai_rows_and_no_lead_row(db):
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            async with su.transaction():
                for tenant, key in ((TARGET, ALLOWANCE[1]), (OTHER, f"ai.allowance_used:{OTHER}:2026-11")):
                    await su.execute(
                        "INSERT INTO tripplanner.outbox (tenant_id, event, schema_version, idempotency_key, "
                        "aggregate_id, payload) VALUES ($1::uuid, 'ai.allowance_used', 1, $2, $1::uuid, $3::jsonb)",
                        tenant, key, json.dumps(ALLOWANCE[2]))
                leads = await su.fetchval("SELECT count(*) AS n FROM tripplanner.outbox WHERE event NOT LIKE 'ai.%'")
                await su.execute("SET LOCAL ROLE cis_app")
                rows = await su.fetch(f"SELECT tenant_id::text AS tenant_id, event, notice_key, payload "
                                      f"FROM {VIEW} ORDER BY tenant_id")
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await su.execute("SELECT 1 AS ok FROM tripplanner.outbox LIMIT 1")
                raise _Done((leads, [(r["tenant_id"], r["event"], r["notice_key"]) for r in rows]))
        except _Done as d:
            return d.value
        finally:
            await su.close()

    leads, rows = run(check())
    assert leads >= 1                                                     # the seed has lead / inquiry rows: not shown
    assert rows == sorted([(TARGET, "ai.allowance_used", ALLOWANCE[1]),
                           (OTHER, "ai.allowance_used", f"ai.allowance_used:{OTHER}:2026-11")])


class _Done(Exception):
    def __init__(self, value):
        super().__init__("done")
        self.value = value
