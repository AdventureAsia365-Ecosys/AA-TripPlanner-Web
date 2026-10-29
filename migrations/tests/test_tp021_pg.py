"""tp-021 replay (AA-861): tripplanner.job from the ADR 0003a job table template and the outbox redaction marker,
applied the way DB Migrate applies it (as tp_owner, after tp-013 … tp-020).

Runs only with ``--pg-dsn`` (a SUPERUSER DSN of a throwaway LOCAL Postgres), on the stand-in of test_tp013_pg.py:
tp-001…012 replayed as files, the CIS 243 grants, tp-013 as the master, tp-014 … tp-020 with ``SET ROLE tp_owner``,
then tp-021 the same way.

Checks: tripplanner.job conforms to the template (``job_template/``: byte copies of AA-CIS-App
shared/jobs/job_table_template.sql and job_table_shape.py, sha256-pinned below; columns, CHECKs, PK / FK, indexes by
definition, the updated_at trigger, reloptions), and the comparison is not vacuous; owner tp_owner, the runtime role
tripplanner may SELECT / INSERT / UPDATE / DELETE jobs (no TRUNCATE, no DDL), the trigger fires for it while its
function is not executable by PUBLIC; the kind and lease CHECKs; outbox.redacted_at is a nullable timestamptz with
its partial index, the runtime may set it, v_inquiry_outbox keeps its columns and AA-Booking still reads only the
view; the guard refuses a runner other than tp_owner; registration once and a re-run as tp_owner being a no-op.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from pathlib import Path

import pytest

from job_template.job_table_shape import TRIPPLANNER_KIND_PATTERN, compare, load_template, read_shape, render
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

BEFORE_TP021 = sorted(f for f in MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql") if 13 < int(f.name[:3]) < 21)
TP021 = MIGRATIONS / "021_job_table_outbox_redaction.sql"
TEMPLATE_DIR = Path(__file__).resolve().parent / "job_template"
# sha256 of the byte copies (AA-CIS-App shared/jobs/ at origin/main on 29/10/2026, identical to AA-Booking's
# backend/tests/job_template/). Change them only by copying the CIS files again and updating the pins.
COPY_PINS = {
    "job_table_template.sql": "8a62ad79a557b47b31c1b5b8aadd08d65848b83cd6db8976d958ac203bdff7c6",
    "job_table_shape.py": "bd58c127375ea0a761a3573f62259e02b985a3b6e199cc3704d55e7724bda6ba",
}
SCRATCH = "tp_job_tmpl"
PRIVS = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")
VIEW_COLUMNS = ["id", "schema_version", "idempotency_key", "trip_id", "payload", "created_at"]


async def _as_owner(admin, path) -> None:
    """DB Migrate's switch for a file after tp-013: SET ROLE to the live owner of schema tripplanner."""
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


@pytest.fixture(scope="module")
def db(pg_dsn):  # noqa: F811
    name = f"tp_aa861_{uuid.uuid4().hex[:8]}"
    password = secrets.token_urlsafe(16)
    runtime_password = secrets.token_urlsafe(16)

    async def setup() -> dict:
        su = await asyncpg.connect(pg_dsn)
        try:
            await su.execute(BOOTSTRAP_ROLES)
            await su.execute(CIS_READER)
            await su.execute(f"ALTER ROLE aa_cis_admin PASSWORD '{password}'")
            await su.execute(f"ALTER ROLE tripplanner LOGIN PASSWORD '{runtime_password}'")
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
            for f in BEFORE_TP021:
                await _as_owner(admin, f)
            await _as_owner(admin, TP021)
        finally:
            await admin.close()
        return {
            "admin_dsn": admin_dsn,
            "su_dsn": _dsn(pg_dsn, database=name),
            "tp_dsn": _dsn(pg_dsn, user="tripplanner", password=runtime_password, database=name),
        }

    async def teardown() -> None:
        su = await asyncpg.connect(pg_dsn)
        try:
            await su.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        finally:
            await su.close()

    try:
        yield run(setup())
    finally:
        run(teardown())


def test_files_before_tp021_are_tp014_to_tp020():
    assert [f.name[:3] for f in BEFORE_TP021] == ["014", "015", "016", "017", "018", "019", "020"]


def test_template_copies_are_the_pinned_bytes():
    for name, pin in COPY_PINS.items():
        assert hashlib.sha256((TEMPLATE_DIR / name).read_bytes()).hexdigest() == pin, (
            f"job_template/{name} differs from the pinned AA-CIS-App copy: copy shared/jobs/{name} again, "
            "update the pin, and re-check tripplanner.job against it")


def _rendered() -> str:
    return render(load_template(), schema=SCRATCH, kind_pattern=TRIPPLANNER_KIND_PATTERN, extra_statuses="",
                  max_attempts_default=5)


def test_job_table_matches_the_template(db):
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            async def body(conn):
                await conn.execute(f"CREATE SCHEMA {SCRATCH}")
                await conn.execute(_rendered())
                return compare(await read_shape(conn, SCRATCH), await read_shape(conn, "tripplanner"))
            return await _in_rollback(su, body)
        finally:
            await su.close()

    diffs = run(check())
    assert diffs == [], "\n".join(diffs)


def test_the_template_comparison_reports_drift(db):
    """Not vacuous: a missing template index, a missing reloption and an extra index all show up."""
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            async def body(conn):
                await conn.execute(f"CREATE SCHEMA {SCRATCH}")
                await conn.execute(_rendered())
                await conn.execute("DROP INDEX tripplanner.job_running_idx")
                await conn.execute("ALTER TABLE tripplanner.job RESET (autovacuum_vacuum_threshold)")
                await conn.execute("CREATE INDEX zz_tp_extra ON tripplanner.job (created_by)")
                return "\n".join(compare(await read_shape(conn, SCRATCH), await read_shape(conn, "tripplanner")))
            return await _in_rollback(su, body)
        finally:
            await su.close()

    diffs = run(check())
    assert "index missing: USING btree (kind, locked_until) WHERE (status = 'running'::text)" in diffs
    assert "reloption missing: autovacuum_vacuum_threshold=50" in diffs
    assert "index not in template: USING btree (created_by)" in diffs


def test_job_is_tp_owners_and_the_runtime_has_dml_only(db):
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            owner = await su.fetchval(
                "SELECT tableowner FROM pg_tables WHERE schemaname = 'tripplanner' AND tablename = 'job'")
            privs = {p: await su.fetchval("SELECT has_table_privilege('tripplanner', 'tripplanner.job', $1)", p)
                     for p in PRIVS}
            fn_public = await su.fetchval(
                "SELECT has_function_privilege('public', 'tripplanner.job_set_updated_at()', 'EXECUTE')")
            fn_runtime = await su.fetchval(
                "SELECT has_function_privilege('tripplanner', 'tripplanner.job_set_updated_at()', 'EXECUTE')")
            create = await su.fetchval("SELECT has_schema_privilege('tripplanner', 'tripplanner', 'CREATE')")
            return owner, privs, fn_public, fn_runtime, create
        finally:
            await su.close()

    owner, privs, fn_public, fn_runtime, create = run(check())
    assert owner == "tp_owner"
    assert privs == {"SELECT": True, "INSERT": True, "UPDATE": True, "DELETE": True, "TRUNCATE": False}
    assert fn_public is False and fn_runtime is False
    assert create is False


def test_runtime_can_queue_claim_and_finish_a_job_and_the_trigger_fires(db):
    async def check():
        tp = await asyncpg.connect(db["tp_dsn"])
        try:
            async def body(conn):
                job_id = await conn.fetchval(
                    "INSERT INTO tripplanner.job (kind, payload, idempotency_key, max_attempts, created_by) "
                    "VALUES ('tp_probe', '{\"dry_run\": true}', 'tp_probe:1', 1, 'test') RETURNING id")
                # The BEFORE UPDATE trigger overwrites any updated_at the statement sets.
                await conn.execute(
                    "UPDATE tripplanner.job SET status = 'running', attempt = attempt + 1, locked_by = 'test', "
                    "locked_until = now() + interval '5 minutes', started_at = now(), updated_at = '2000-01-01' "
                    "WHERE id = $1", job_id)
                after = await conn.fetchval("SELECT updated_at FROM tripplanner.job WHERE id = $1", job_id)
                await conn.execute(
                    "UPDATE tripplanner.job SET status = 'succeeded', locked_by = NULL, locked_until = NULL, "
                    "finished_at = now(), result = '{\"candidates_total\": 0}' WHERE id = $1", job_id)
                dup = await conn.fetchval(
                    "INSERT INTO tripplanner.job (kind, idempotency_key) VALUES ('tp_probe', 'tp_probe:1') "
                    "ON CONFLICT (idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING RETURNING id")
                status = await conn.fetchval("SELECT status FROM tripplanner.job WHERE id = $1", job_id)
                deleted = await conn.execute("DELETE FROM tripplanner.job WHERE id = $1", job_id)
                return after, dup, status, deleted
            return await _in_rollback(tp, body)
        finally:
            await tp.close()

    after, dup, status, deleted = run(check())
    assert after.year != 2000  # the trigger fired for the runtime role
    assert dup is None  # one job per idempotency key, ever
    assert status == "succeeded"
    assert deleted == "DELETE 1"


@pytest.mark.parametrize("sql, constraint", [
    ("INSERT INTO tripplanner.job (kind) VALUES ('bk_retention_job')", "job_kind_chk"),
    ("INSERT INTO tripplanner.job (kind) VALUES ('retention')", "job_kind_chk"),
    ("INSERT INTO tripplanner.job (kind, status) VALUES ('tp_probe', 'stopped_budget')", "job_status_chk"),
    ("INSERT INTO tripplanner.job (kind, status) VALUES ('tp_probe', 'running')", "job_lease_chk"),
    ("INSERT INTO tripplanner.job (kind, max_attempts) VALUES ('tp_probe', 0)", "job_attempts_chk"),
])
def test_job_checks_refuse_bad_rows(db, sql, constraint):
    async def check():
        tp = await asyncpg.connect(db["tp_dsn"])
        try:
            with pytest.raises(asyncpg.CheckViolationError) as e:
                await tp.execute(sql)
            return e.value.constraint_name
        finally:
            await tp.close()

    assert run(check()) == constraint


def test_outbox_redaction_marker_and_index(db):
    async def check():
        su = await asyncpg.connect(db["su_dsn"])
        try:
            col = await su.fetchrow(
                "SELECT format_type(a.atttypid, a.atttypmod) AS typ, a.attnotnull, pg_get_expr(d.adbin, d.adrelid) AS dflt "
                "FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum "
                "WHERE a.attrelid = 'tripplanner.outbox'::regclass AND a.attname = 'redacted_at' AND NOT a.attisdropped")
            idx = await su.fetchval(
                "SELECT pg_get_indexdef(indexrelid) FROM pg_index WHERE indexrelid = 'tripplanner.outbox_redact_due_idx'::regclass")
            view_cols = [r[0] for r in await su.fetch(
                "SELECT attname::text FROM pg_attribute WHERE attrelid = 'tripplanner.v_inquiry_outbox'::regclass "
                "AND attnum > 0 AND NOT attisdropped ORDER BY attnum")]
            booking_table = await su.fetchval("SELECT has_table_privilege('booking', 'tripplanner.outbox', 'SELECT')")
            booking_view = await su.fetchval(
                "SELECT has_table_privilege('booking', 'tripplanner.v_inquiry_outbox', 'SELECT')")
            comment = await su.fetchval("SELECT obj_description('tripplanner.outbox'::regclass, 'pg_class')")
            return col, idx, view_cols, booking_table, booking_view, comment
        finally:
            await su.close()

    col, idx, view_cols, booking_table, booking_view, comment = run(check())
    assert (col["typ"], col["attnotnull"], col["dflt"]) == ("timestamp with time zone", False, None)
    assert idx == ("CREATE INDEX outbox_redact_due_idx ON tripplanner.outbox USING btree (created_at, id) "
                   "WHERE (redacted_at IS NULL)")
    assert view_cols == VIEW_COLUMNS  # the new column does not reach AA-Booking
    assert booking_table is False and booking_view is True
    assert "tp_retention_outbox_redact" in comment


def test_runtime_can_redact_an_outbox_row_and_dedupe_still_holds(db):
    async def check():
        tp = await asyncpg.connect(db["tp_dsn"])
        try:
            async def body(conn):
                trip = str(uuid.uuid4())
                key = f"{trip}:h-1"
                payload = {"contract": "inquiry.submitted", "idempotency_key": key,
                           "customer": {"customer_id": None, "name": "Test Traveller", "email": "t@example.test"}}
                row_id = await conn.fetchval(
                    "INSERT INTO tripplanner.outbox (event, schema_version, idempotency_key, aggregate_id, payload, "
                    "created_at) VALUES ('inquiry.submitted', 1, $1, $2, $3::jsonb, now() - interval '120 days') "
                    "RETURNING id", key, trip, json.dumps(payload))
                due = await conn.fetchval(
                    "SELECT count(*) FROM tripplanner.outbox WHERE redacted_at IS NULL "
                    "AND created_at < now() - interval '90 days' AND id = $1", row_id)
                redacted = {**payload, "customer": {"customer_id": None, "name": None, "email": None}}
                await conn.execute("UPDATE tripplanner.outbox SET payload = $2::jsonb, redacted_at = now() WHERE id = $1",
                                   row_id, json.dumps(redacted))
                again = await conn.fetchval(
                    "INSERT INTO tripplanner.outbox (event, schema_version, idempotency_key, aggregate_id, payload) "
                    "VALUES ('inquiry.submitted', 1, $1, $2, '{}'::jsonb) ON CONFLICT (idempotency_key) DO NOTHING "
                    "RETURNING id", key, trip)
                view = await conn.fetchrow(
                    "SELECT idempotency_key, schema_version, payload FROM tripplanner.v_inquiry_outbox WHERE id = $1",
                    row_id)
                return due, again, view
            return await _in_rollback(tp, body)
        finally:
            await tp.close()

    due, again, view = run(check())
    assert due == 1
    assert again is None  # the idempotency key still refuses a second row
    assert view["schema_version"] == 1 and view["idempotency_key"].endswith(":h-1")
    assert json.loads(view["payload"])["customer"]["email"] is None


def test_guard_refuses_a_runner_other_than_tp_owner(db):
    async def check():
        admin = await asyncpg.connect(db["admin_dsn"])
        try:
            with pytest.raises(asyncpg.RaiseError) as e:
                await admin.execute(TP021.read_text(encoding="utf-8"))
            return str(e.value)
        finally:
            await admin.close()

    assert "expected to run as tp_owner" in run(check())


def test_registered_once_and_a_rerun_as_tp_owner_is_a_no_op(db):
    async def snapshot(conn):
        return (
            await conn.fetchval("SELECT count(*) FROM shared.schema_versions WHERE version = 'tp-021'"),
            await conn.fetchval("SELECT count(*) FROM pg_indexes WHERE schemaname = 'tripplanner' "
                                "AND tablename IN ('job', 'outbox')"),
            await conn.fetchval("SELECT count(*) FROM pg_trigger WHERE tgrelid = 'tripplanner.job'::regclass "
                                "AND NOT tgisinternal"),
            await conn.fetchval("SELECT array_to_string(reloptions, ',') FROM pg_class "
                                "WHERE oid = 'tripplanner.job'::regclass"),
        )

    async def check():
        admin = await asyncpg.connect(db["admin_dsn"])
        try:
            first = await snapshot(admin)
            await _as_owner(admin, TP021)
            second = await snapshot(admin)
            return first, second
        finally:
            await admin.close()

    first, second = run(check())
    assert first == second
    assert first[0] == 1 and first[2] == 1
    assert first[3] == "autovacuum_vacuum_scale_factor=0.02,autovacuum_vacuum_threshold=50"
