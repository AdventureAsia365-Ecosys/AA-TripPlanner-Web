"""tp-026 replay (AA-1038 R5 slice 3): tripplanner.outbox carries lead.submitted rows and their delivery state,
applied the way Migration Apply / DB Migrate applies it (as tp_owner, after tp-013 … tp-025).

Runs only with ``--pg-dsn`` (a SUPERUSER DSN of a throwaway LOCAL Postgres), on the tp-025 stand-in
(test_tp025_pg._database: tp-001 … tp-025 with seeded inquiry rows), then tp-026 as tp_owner.

Checks: the contract shape (no directive line, one BEGIN … COMMIT); the event CHECK allows lead.submitted and still
refuses anything else; a lead row needs a channel and another row may not have one; the delivery columns and their
CHECKs (code-only error, attempts range); the pending index; the inquiry rows untouched and v_inquiry_outbox unchanged
(booking never sees a lead); as the runtime role tripplanner under the tp-025 shadow policy: a bound tenant writes and
reads its own lead, updates its delivery state, and sees no other tenant's lead; registration once and a re-run as
tp_owner being a no-op. Invented values only.
"""
from __future__ import annotations

import json
import re
import uuid

import pytest

from test_tp013_pg import pg_dsn  # noqa: F401  (fixture of the tp-013 replay: --pg-dsn, local only)
from test_tp013_pg import MIGRATIONS, run
from test_tp025_pg import _as_owner, _database, _drop, _in_rollback

asyncpg = pytest.importorskip("asyncpg")

TP026 = MIGRATIONS / "026_lead_outbox_delivery.sql"
DEMO = "00000000-0000-0000-0000-000000000002"
TENANT_A = "3e4f5a6b-7c8d-4e9f-a0b1-c2d3e4f5a6b7"
TENANT_B = "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d"
VIEW_COLUMNS = ["id", "schema_version", "idempotency_key", "trip_id", "payload", "created_at"]
LEAD_SQL = ("INSERT INTO tripplanner.outbox (event, schema_version, idempotency_key, aggregate_id, payload, "
            "delivery_channel) VALUES ('lead.submitted', 1, $1, $2, $3::jsonb, $4) RETURNING id")


def _code(sql: str) -> str:
    return "\n".join(ln for ln in sql.splitlines() if not ln.lstrip().startswith("--"))


@pytest.fixture(scope="module")
def db(pg_dsn):  # noqa: F811
    ctx, error = _database(pg_dsn)
    try:
        assert error is None, error

        async def apply():
            admin = await asyncpg.connect(ctx["admin_dsn"])
            try:
                await _as_owner(admin, TP026)
                await _as_owner(admin, TP026)            # re-run: a no-op
            finally:
                await admin.close()
        run(apply())
        yield ctx
    finally:
        _drop(pg_dsn, ctx["name"])


def _lead(tenant: str) -> tuple[str, str, str]:
    lead_id = str(uuid.uuid4())
    payload = {"contract": "lead.submitted.v1", "lead_id": lead_id, "tenant": {"tenant_id": tenant},
               "traveller": {"name": "Ada Fernwick", "email": "ada@example.invalid"}}
    return f"tp-lead:{lead_id}", lead_id, json.dumps(payload)


def test_contract_shape():
    sql = TP026.read_text(encoding="utf-8")
    assert not re.search(r"^--\s*migrate:", sql, re.M), "a contract file has no directive line"
    code = _code(sql)
    assert code.count("BEGIN;") == 1 and code.count("COMMIT;") == 1
    assert "FORCE ROW LEVEL SECURITY" not in code and "TO PUBLIC" not in code.upper()


def test_registered_once_and_columns(db):
    async def body():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            assert await su.fetchval("SELECT count(*) FROM shared.schema_versions WHERE version = 'tp-026'") == 1
            cols = {r["attname"]: (r["t"], r["attnotnull"]) for r in await su.fetch(
                "SELECT attname, format_type(atttypid, atttypmod) AS t, attnotnull FROM pg_attribute "
                "WHERE attrelid = 'tripplanner.outbox'::regclass AND attnum > 0 AND NOT attisdropped")}
            assert cols["delivery_channel"] == ("text", False)
            assert cols["delivered_at"] == ("timestamp with time zone", False)
            assert cols["delivery_attempts"] == ("integer", True)
            assert cols["delivery_error"] == ("text", False)
            idx = await su.fetchval("SELECT indexdef FROM pg_indexes WHERE schemaname = 'tripplanner' "
                                    "AND indexname = 'outbox_lead_pending_idx'")
            assert "(tenant_id, id)" in idx and "lead.submitted" in idx and "delivered_at IS NULL" in idx
            view_cols = [r["attname"] for r in await su.fetch(
                "SELECT attname FROM pg_attribute WHERE attrelid = 'tripplanner.v_inquiry_outbox'::regclass "
                "AND attnum > 0 AND NOT attisdropped ORDER BY attnum")]
            assert view_cols == VIEW_COLUMNS
            # the seeded inquiry rows are untouched (no channel, no delivery state)
            rows = await su.fetch("SELECT delivery_channel, delivered_at, delivery_attempts FROM tripplanner.outbox")
            assert len(rows) == 2 and all(r["delivery_channel"] is None and r["delivery_attempts"] == 0 for r in rows)
        finally:
            await su.close()
    run(body())


@pytest.mark.parametrize("sql,args", [
    ("INSERT INTO tripplanner.outbox (event, schema_version, idempotency_key, aggregate_id, payload) "
     "VALUES ('lead.submitted', 1, 'tp-lead:x1', gen_random_uuid(), '{}')", ()),            # a lead without channel
    ("INSERT INTO tripplanner.outbox (event, schema_version, idempotency_key, aggregate_id, payload, "
     "delivery_channel) VALUES ('inquiry.submitted', 1, 'k:x2', gen_random_uuid(), '{}', 'crm')", ()),
    ("INSERT INTO tripplanner.outbox (event, schema_version, idempotency_key, aggregate_id, payload, "
     "delivery_channel) VALUES ('lead.submitted', 1, 'tp-lead:x3', gen_random_uuid(), '{}', 'sms')", ()),
    ("INSERT INTO tripplanner.outbox (event, schema_version, idempotency_key, aggregate_id, payload) "
     "VALUES ('trip.other', 1, 'k:x4', gen_random_uuid(), '{}')", ()),
    ("INSERT INTO tripplanner.outbox (event, schema_version, idempotency_key, aggregate_id, payload, "
     "delivery_channel, delivery_error) VALUES ('lead.submitted', 1, 'tp-lead:x5', gen_random_uuid(), '{}', "
     "'webhook', 'Connection refused to 10.0.0.1')", ()),
    ("INSERT INTO tripplanner.outbox (event, schema_version, idempotency_key, aggregate_id, payload, "
     "delivered_at) VALUES ('inquiry.submitted', 1, 'k:x6', gen_random_uuid(), '{}', now())", ()),
])
def test_checks_refuse(db, sql, args):
    async def body():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            with pytest.raises(asyncpg.CheckViolationError):
                await su.execute(sql, *args)
        finally:
            await su.close()
    run(body())


def test_runtime_role_writes_and_reads_its_own_leads_only(db):
    async def body():
        tp = await asyncpg.connect(db["tp_dsn"])
        try:
            async def scenario(conn):
                await conn.execute("SELECT set_config('app.tenant_id', $1, true)", TENANT_A)
                key_a, lead_a, payload_a = _lead(TENANT_A)
                row_a = await conn.fetchval(LEAD_SQL, key_a, uuid.uuid4(), payload_a, "crm")
                await conn.execute("SELECT set_config('app.tenant_id', $1, true)", TENANT_B)
                key_b, _, payload_b = _lead(TENANT_B)
                row_b = await conn.fetchval(LEAD_SQL, key_b, uuid.uuid4(), payload_b, "webhook")
                # B sees its own pending lead only
                seen_b = [r["id"] for r in await conn.fetch(
                    "SELECT id FROM tripplanner.outbox WHERE event = 'lead.submitted' AND delivered_at IS NULL "
                    "AND redacted_at IS NULL ORDER BY id")]
                # B cannot mark A's lead delivered (the shadow policy hides it)
                touched = await conn.execute("UPDATE tripplanner.outbox SET delivered_at = now() WHERE id = $1", row_a)
                # A's own delivery: delivered + payload redacted
                await conn.execute("SELECT set_config('app.tenant_id', $1, true)", TENANT_A)
                done = await conn.execute(
                    "UPDATE tripplanner.outbox SET delivered_at = now(), redacted_at = now(), "
                    "payload = jsonb_build_object('lead_id', payload->>'lead_id'), delivery_error = NULL "
                    "WHERE id = $1 AND delivered_at IS NULL", row_a)
                tenant_a = await conn.fetchval("SELECT tenant_id::text FROM tripplanner.outbox WHERE id = $1", row_a)
                payload_after = await conn.fetchval("SELECT payload FROM tripplanner.outbox WHERE id = $1", row_a)
                return seen_b, row_b, touched, done, tenant_a, json.loads(payload_after), lead_a
            seen_b, row_b, touched, done, tenant_a, payload_after, lead_a = await _in_rollback(tp, scenario)
            assert seen_b == [row_b]
            assert touched == "UPDATE 0" and done == "UPDATE 1"
            assert tenant_a == TENANT_A and payload_after == {"lead_id": lead_a}
        finally:
            await tp.close()
    run(body())


def test_booking_never_reads_a_lead(db):
    async def body():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            key, _, payload = _lead(TENANT_A)
            await su.execute(LEAD_SQL.replace(" RETURNING id", ""), key, uuid.uuid4(), payload, "crm")
        finally:
            await su.close()
        bk = await asyncpg.connect(db["bk_dsn"])
        try:
            keys = [r["idempotency_key"] for r in await bk.fetch("SELECT idempotency_key FROM tripplanner.v_inquiry_outbox")]
            assert keys and not any(k.startswith("tp-lead:") for k in keys)
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await bk.fetch("SELECT id FROM tripplanner.outbox")
        finally:
            await bk.close()
    run(body())
