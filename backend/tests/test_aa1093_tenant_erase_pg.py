"""AA-1093 lane L4 integration — tp_tenant_erase (contract tenant.erasure.v1), tp_tenant_backup and
tp_tenant_restore_drill on a real Postgres, as the runtime role ``tripplanner`` through the jobs Lambda's own
``handler.serve``.

Runs only with ``pytest --pg-dsn postgresql://postgres@127.0.0.1:<port>/postgres`` (a SUPERUSER DSN of a throwaway
LOCAL Postgres). The database is the tp-031 replay (``migrations/tests/test_tp031_pg.erasure_database``: tp-028's
forced database with invented demo rows, tp-029, tp-030, tp-031, invented rows of the TARGET and OTHER tenants). Each
test builds its own database, so an apply in one never shows in another. A stand-in of the CIS request view
``catalog.v_tenant_erasure_v1`` (contract ErasureRequestRow columns: a table behind a view, SELECT to tripplanner) is
created only by the tests that need it.

Checks: an operator DRY RUN appends one ok report (cis_app reads exactly that row in the report view, valid against the
contract ErasureReportRow) and deletes nothing; an apply without markers, without an ok DRY RUN of that hash, with a
stale plan or of a tenant not marked synthetic is refused with its contract code and deletes nothing; the feed acts on
the TripPlanner rows only (newest per request, active, past not_before), runs dry run, apply and verify of the
synthetic TARGET once per round and phase, erases every row of it (and the customer only its sessions link) while
OTHER and the demo rows stay, the verification census reads every pii: column and finds 0; the pilot is refused; every
report row validates against the contract; a backup of TARGET round-trips through the restore drill with equal counts
and digests and the drill leaves every live row as it was; a changed backup file fails the drill; S3 refusing a put
fails the backup job with s3_access_denied; the daily tick backs up the tenants CIS marks is_pilot (no_feed and
nothing written before a marker view exists), and the drill restores that daily backup.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import urlparse

import pytest

asyncpg = pytest.importorskip("asyncpg")
jsonschema = pytest.importorskip("jsonschema")

from backend.jobs import handler, tenant_backup, tenant_erase, tenant_restore_drill, tenant_store  # noqa: E402

MIGRATION_TESTS = Path(__file__).resolve().parents[2] / "migrations" / "tests"
CONTRACT = json.loads((Path(__file__).parent / "contracts" / "tenant.erasure.v1.json").read_text())
ROW_SCHEMA = {**CONTRACT, "$ref": "#/$defs/ErasureReportRow"}
TARGET = "a7a7a7a7-0000-4000-8000-0000000000e1"     # the invented tenants of test_tp030_pg.py
OTHER = "b8b8b8b8-0000-4000-8000-0000000000e2"
PILOT = "c0c0c0c0-0000-4000-8000-0000000000e4"
REQUEST = "d1d1d1d1-0000-4000-8000-0000000000e5"
PILOT_REQUEST = "d2d2d2d2-0000-4000-8000-0000000000e6"
LATER_REQUEST = "d3d3d3d3-0000-4000-8000-0000000000e7"
DEV_ON = {"ENVIRONMENT": "dev", "TENANT_ERASE_APPLY_ENABLED": "true"}
TABLES = ("sessions", "trip_drafts", "trip_events", "outbox")
VIEW_COLUMNS = ("report_id, request_id, tenant_id, app, round, mode, status, error_code, plan_hash, tables, blockers, "
                "census, job_id, reported_at")

FEED_STANDIN = """
CREATE SCHEMA IF NOT EXISTS catalog;
CREATE TABLE catalog.erasure_feed_standin (
    request_id uuid, tenant_id uuid, phase text, round integer, opened_at timestamptz DEFAULT now() - interval '31 days',
    approval_expires_at timestamptz, is_pilot boolean, is_synthetic boolean, app text, step_status text DEFAULT 'active',
    not_before timestamptz DEFAULT now() - interval '1 day', approved_plan_hash text, updated_at timestamptz DEFAULT now());
CREATE VIEW catalog.v_tenant_erasure_v1 AS
SELECT request_id, tenant_id, phase, round, opened_at, approval_expires_at, is_pilot, is_synthetic, app, step_status,
       not_before, approved_plan_hash, updated_at FROM catalog.erasure_feed_standin;
GRANT USAGE ON SCHEMA catalog TO tripplanner;
GRANT SELECT ON catalog.v_tenant_erasure_v1 TO tripplanner;
"""
FEED_INSERT = """
INSERT INTO catalog.erasure_feed_standin (request_id, tenant_id, phase, round, app, is_pilot, is_synthetic,
                                          approved_plan_hash, approval_expires_at, not_before, updated_at)
VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8, now() + interval '1 day', now() + $9::text::interval,
        clock_timestamp())
"""


def _replay():
    if str(MIGRATION_TESTS) not in sys.path:
        sys.path.insert(0, str(MIGRATION_TESTS))
    import test_tp025_pg as replay  # noqa: E402  (the drop helper)
    import test_tp031_pg as tp031  # noqa: E402  (the tp-031 database)
    return replay, tp031


@pytest.fixture(scope="module")
def pg_dsn(request):
    dsn = request.config.getoption("--pg-dsn")
    if not dsn:
        pytest.skip("integration: pass --pg-dsn with a superuser DSN of a throwaway local Postgres")
    host = urlparse(dsn).hostname or "localhost"
    if host not in ("localhost", "127.0.0.1", "::1"):
        pytest.skip(f"integration tests only run against a local Postgres, not {host}")
    return dsn


@pytest.fixture
def db(pg_dsn):
    replay, tp031 = _replay()
    ctx, error = tp031.erasure_database(pg_dsn)
    try:
        assert error is None, error
        yield ctx
    finally:
        replay._drop(pg_dsn, ctx["name"])


@pytest.fixture
async def env(db):
    su = await asyncpg.connect(db["su_dsn"])
    pool = await asyncpg.create_pool(db["tp_dsn"], min_size=1, max_size=1)
    try:
        yield {"su": su, "pool": pool}
    finally:
        await pool.close()
        await su.close()


async def _serve(env, event):
    return await handler.serve(event, pool=env["pool"], worker="test-worker", time_left=lambda: 600.0)


def _only(out) -> dict:
    assert len(out["jobs"]) == 1, out
    return out["jobs"][0]


async def _counts(su, tenant) -> dict:
    out = {t: await su.fetchval(f"SELECT count(*) AS n FROM tripplanner.{t} WHERE tenant_id = $1::uuid", tenant)
           for t in TABLES}
    out["spend"] = await su.fetchval("SELECT count(*) AS n FROM tripplanner.ai_spend_month WHERE scope = $1",
                                     f"tenant:{tenant}")
    return out


async def _fingerprint(su) -> str:
    """Every live row of the four tables (as the superuser, which bypasses row security)."""
    parts = []
    for t in TABLES:
        parts.append(await su.fetchval(f"SELECT coalesce(string_agg(md5(x::text), ',' ORDER BY x.id), '') AS f "
                                       f"FROM tripplanner.{t} x"))
    return "|".join(parts)


async def _report_rows(su, where: str = "TRUE", *args) -> list[dict]:
    """The report view as cis_app reads it (CIS), as JSON-ready dicts in report order."""
    async with su.transaction():
        await su.execute("SET LOCAL ROLE cis_app")
        rows = await su.fetch(f"SELECT {VIEW_COLUMNS} FROM tripplanner.v_tenant_erasure_report_v1 WHERE {where} "
                              f"ORDER BY reported_at", *args)
    out = []
    for r in rows:
        d = dict(r)
        for k in ("report_id", "request_id", "tenant_id", "job_id"):
            d[k] = str(d[k]) if d[k] is not None else None
        for k in ("tables", "blockers", "census"):
            d[k] = json.loads(d[k]) if d[k] is not None else None
        d["reported_at"] = d["reported_at"].isoformat()
        jsonschema.validate(d, ROW_SCHEMA, format_checker=jsonschema.FormatChecker())
        out.append(d)
    return out


async def _enqueue(env, kind, payload):
    return _only(await _serve(env, {"op": "enqueue", "kind": kind, "payload": payload}))


async def _tick(env, kinds=(tenant_erase.KIND,)):
    await env["su"].execute("UPDATE tripplanner.job SET idempotency_key = NULL WHERE kind = ANY($1::text[])",
                            list(kinds))
    out = await _serve(env, {"op": "tick"})
    return [j for j in out["jobs"] if j["kind"] == tenant_erase.KIND][0]


async def test_operator_dry_run_appends_one_ok_report_and_deletes_nothing(env, monkeypatch):
    monkeypatch.setattr(tenant_erase.os, "environ", DEV_ON)
    before = await _counts(env["su"], TARGET)
    job = await _enqueue(env, tenant_erase.KIND, {"tenant_id": TARGET, "mode": "dry_run"})
    assert job["status"] == "succeeded", job
    entry = job["result"]["requests"][0]
    assert (entry["status"], entry["request_id"], entry["round"]) == \
        ("ok", tenant_erase.operator_request_id(TARGET), 1)
    by = {t["table"]: t for t in entry["tables"]}
    assert by["tripplanner.trip_events"]["rows"] == 3 and len(entry["plan_hash"]) == 64
    assert await _counts(env["su"], TARGET) == before
    rows = await _report_rows(env["su"], "tenant_id = $1::uuid", TARGET)
    assert [(r["mode"], r["status"], r["plan_hash"], r["job_id"]) for r in rows] == \
        [("dry_run", "ok", entry["plan_hash"], job["job_id"])]
    assert await env["su"].fetchval("SELECT source FROM tripplanner.tenant_erasure_log") == "operator"


async def test_apply_refusals_carry_contract_codes_and_delete_nothing(env, monkeypatch):
    monkeypatch.setattr(tenant_erase.os, "environ", DEV_ON)
    untouched = {"sessions": 2, "trip_drafts": 2, "trip_events": 3, "outbox": 1, "spend": 2}
    # no CIS view: the markers are unknown, so an apply is refused before the plan is even looked at
    job = await _enqueue(env, tenant_erase.KIND, {"tenant_id": TARGET, "mode": "apply", "expect_plan": "0" * 64})
    assert job["result"]["requests"][0]["error_code"] == "tenant_markers_unknown" and job["result"]["attention"]
    await env["su"].execute(FEED_STANDIN)
    await env["su"].execute(FEED_INSERT, REQUEST, TARGET, "dry_run", 1, "booking", False, True, None, "-1 day")

    # a hash no ok DRY RUN of this request and round carries (even the tenant's real current plan) is not approved
    async with env["pool"].acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_scope', 'system', false)")
        real = json.loads(await conn.fetchval("SELECT tripplanner.erase_tenant($1::uuid, 1, true)::text", TARGET))
        await conn.execute("RESET app.tenant_scope")
    for expect in ("0" * 64, real["plan_hash"]):
        job = await _enqueue(env, tenant_erase.KIND, {"tenant_id": TARGET, "mode": "apply", "expect_plan": expect,
                                                      "request_id": REQUEST})
        entry = job["result"]["requests"][0]
        assert (entry["status"], entry["error_code"]) == ("refused", "dry_run_missing"), expect
    assert await _counts(env["su"], TARGET) == untouched

    # the DRY RUN of this request, then the tenant writes: the approved hash is stale
    dry = await _enqueue(env, tenant_erase.KIND, {"tenant_id": TARGET, "mode": "dry_run", "request_id": REQUEST})
    plan = dry["result"]["requests"][0]["plan_hash"]
    assert plan == real["plan_hash"]
    # a DRY RUN of round 1 does not approve round 2
    job = await _enqueue(env, tenant_erase.KIND, {"tenant_id": TARGET, "mode": "apply", "expect_plan": plan,
                                                  "request_id": REQUEST, "round": 2})
    assert job["result"]["requests"][0]["error_code"] == "dry_run_missing"
    await env["su"].execute("UPDATE tripplanner.trip_drafts SET updated_at = now() WHERE tenant_id = $1::uuid", TARGET)
    job = await _enqueue(env, tenant_erase.KIND, {"tenant_id": TARGET, "mode": "apply", "expect_plan": plan,
                                                  "request_id": REQUEST})
    entry = job["result"]["requests"][0]
    assert (entry["status"], entry["error_code"]) == ("refused", "plan_changed") and entry["plan_hash"] != plan
    assert await _counts(env["su"], TARGET) == untouched
    await env["su"].execute("UPDATE catalog.erasure_feed_standin SET is_synthetic = false")
    job = await _enqueue(env, tenant_erase.KIND, {"tenant_id": TARGET, "mode": "apply", "expect_plan": plan,
                                                  "request_id": REQUEST})
    assert job["result"]["requests"][0]["error_code"] == "not_synthetic"
    codes = [r["error_code"] for r in await _report_rows(env["su"], "mode = 'apply'")]
    assert codes == ["tenant_markers_unknown", "dry_run_missing", "dry_run_missing", "dry_run_missing",
                     "plan_changed", "not_synthetic"]


async def test_feed_runs_dry_run_apply_verify_and_erases_the_tenant_only(env, monkeypatch, tmp_path):
    monkeypatch.setattr(tenant_erase.os, "environ", DEV_ON)
    # the feed marks PILOT is_pilot, so the same ticks also run the daily pilot backup: give it a store
    monkeypatch.setattr(tenant_store, "S3Store", lambda: tenant_store.DirStore(str(tmp_path)))
    su = env["su"]
    await su.execute(FEED_STANDIN)
    other_before = await _counts(su, OTHER)
    demo_before = await su.fetchval("SELECT count(*) AS n FROM tripplanner.sessions WHERE tenant_id <> ALL($1::uuid[])",
                                    [TARGET, OTHER])
    feed = [(REQUEST, TARGET, "dry_run", 1, "tripplanner", False, True, None, "-1 day"),
            (PILOT_REQUEST, PILOT, "dry_run", 1, "tripplanner", True, False, None, "-1 day"),
            (REQUEST, TARGET, "apply", 1, "booking", False, True, "e" * 64, "-1 day"),     # not ours
            (LATER_REQUEST, OTHER, "dry_run", 1, "tripplanner", False, True, None, "+5 days")]   # not due yet
    for row in feed:
        await su.execute(FEED_INSERT, *row)

    run = await _tick(env)
    assert run["result"]["mode"] == "feed"
    by_request = {e["request_id"]: e for e in run["result"]["requests"]}
    assert set(by_request) == {REQUEST, PILOT_REQUEST}
    assert by_request[REQUEST]["status"] == "ok" and by_request[REQUEST]["mode"] == "dry_run"
    assert (by_request[PILOT_REQUEST]["status"], by_request[PILOT_REQUEST]["error_code"]) == \
        ("refused", "pilot_refused")
    plan = by_request[REQUEST]["plan_hash"]

    # a second tick of the same round and phase writes nothing (one report per request, round and phase)
    run = await _tick(env, kinds=(tenant_erase.KIND, tenant_backup.KIND))
    assert run["result"]["requests"] == []

    # the tech lead approves: CIS moves the request to apply with the approved hash (a newer row of the request)
    await su.execute(FEED_INSERT, REQUEST, TARGET, "apply", 1, "tripplanner", False, True, plan, "-1 day")
    run = await _tick(env, kinds=(tenant_erase.KIND, tenant_backup.KIND))
    applied = {(e["request_id"], e["mode"]): e for e in run["result"]["requests"]}
    assert list(applied) == [(REQUEST, "apply")] and applied[(REQUEST, "apply")]["status"] == "ok"
    assert await _counts(su, TARGET) == dict.fromkeys([*TABLES, "spend"], 0)
    assert await _counts(su, OTHER) == other_before
    assert await su.fetchval("SELECT count(*) AS n FROM tripplanner.sessions WHERE tenant_id <> ALL($1::uuid[])",
                             [TARGET, OTHER]) == demo_before
    _, tp031 = _replay()
    assert {t["table"]: t["rows"] for t in applied[(REQUEST, "apply")]["tables"]}["tripplanner.customers"] == 1
    assert sorted(r["id"] for r in await su.fetch("SELECT id::text AS id FROM tripplanner.customers "
                                                  "WHERE id = ANY($1::uuid[])", list(tp031.CUSTOMERS))) == \
        sorted([tp031.SHARED, tp031.ONLY_OTHER])

    await su.execute(FEED_INSERT, REQUEST, TARGET, "verify", 1, "tripplanner", False, True, None, "-1 day")
    run = await _tick(env, kinds=(tenant_erase.KIND, tenant_backup.KIND))
    [verify] = run["result"]["requests"]
    assert (verify["mode"], verify["status"], verify["error_code"], verify["plan_hash"]) == ("verify", "ok", None, None)
    assert verify["census"] == {"pii_columns_checked": 8, "pii_rows_found": 0} and verify["blockers"] == []

    # cis_app reads one report per run, each valid against the contract row
    report = await _report_rows(su, "request_id = $1::uuid", REQUEST)
    assert [(r["mode"], r["status"], r["round"], r["app"]) for r in report] == [
        ("dry_run", "ok", 1, "tripplanner"), ("apply", "ok", 1, "tripplanner"), ("verify", "ok", 1, "tripplanner")]
    assert {r["plan_hash"] for r in report if r["mode"] != "verify"} == {plan}


async def test_backup_and_restore_drill_round_trip_without_touching_live_rows(env, monkeypatch, tmp_path):
    store = tenant_store.DirStore(str(tmp_path))
    monkeypatch.setattr(tenant_store, "S3Store", lambda: store)
    before = await _fingerprint(env["su"])

    backup = await _enqueue(env, tenant_backup.KIND, {"tenant_id": TARGET})
    assert backup["status"] == "succeeded", backup
    res = backup["result"]
    assert res["rows"] == {"tripplanner.sessions": 2, "tripplanner.trip_drafts": 2, "tripplanner.trip_events": 3,
                           "tripplanner.outbox": 1}
    assert res["prefix"] == f"exports/tenants/{TARGET}/tripplanner/{backup['job_id']}/"
    manifest = json.loads((tmp_path / res["prefix"] / "manifest.json").read_bytes())
    assert manifest["tenant_id"] == TARGET and all(
        OTHER not in (tmp_path / res["prefix"] / f"tables/tripplanner.{t}.jsonl").read_text() for t in TABLES)

    drill = _only(await _serve(env, {"op": "restore_drill", "payload": {"tenant_id": TARGET}}))
    assert drill["kind"] == tenant_restore_drill.KIND and drill["status"] == "succeeded", drill
    out = drill["result"]
    assert out["passed"] is True and out["backup_id"] == backup["job_id"] and out["checks"]["sha256sums_matches_job"]
    for t, row in out["tables"].items():
        assert row["manifest_rows"] == row["restored_rows"] == row["live_rows"] == row["live_found"] == \
            row["identical"], t
        assert row["digest_ok"], t
    assert await _fingerprint(env["su"]) == before                     # the drill wrote no live row
    assert await env["su"].fetchval("SELECT count(*) AS n FROM pg_class WHERE relname LIKE 'rd\\_%' "
                                    "AND relpersistence = 't'") == 0

    # a changed table file fails the drill (and asks for attention)
    path = tmp_path / res["prefix"] / "tables/tripplanner.trip_events.jsonl"
    path.write_bytes(b"\n".join(path.read_bytes().splitlines()[:-1]) + b"\n")
    drill = _only(await _serve(env, {"op": "restore_drill",
                                     "payload": {"tenant_id": TARGET, "backup_id": backup["job_id"]}}))
    assert drill["result"]["passed"] is False and drill["result"]["attention"] is True
    assert drill["result"]["checks"]["files"]["trip_events"] is False


async def test_backup_of_an_erased_tenant_restores_into_pg_temp_only(env, monkeypatch, tmp_path):
    """The point of the drill: after an erase, the backup still loads (into pg_temp) and live shows nothing."""
    store = tenant_store.DirStore(str(tmp_path))
    monkeypatch.setattr(tenant_store, "S3Store", lambda: store)
    backup = await _enqueue(env, tenant_backup.KIND, {"tenant_id": TARGET})
    async with env["pool"].acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_scope', 'system', false)")
        plan = json.loads(await conn.fetchval("SELECT tripplanner.erase_tenant($1::uuid, 1, true)::text", TARGET))
        await conn.fetchval("SELECT tripplanner.erase_tenant($1::uuid, 1, false, $2)::text", TARGET, plan["plan_hash"])
        await conn.execute("RESET app.tenant_scope")
    drill = _only(await _serve(env, {"op": "restore_drill",
                                     "payload": {"tenant_id": TARGET, "backup_id": backup["job_id"]}}))
    out = drill["result"]
    assert out["passed"] is True
    assert {t: (r["restored_rows"], r["live_rows"]) for t, r in out["tables"].items()} == {
        "sessions": (2, 0), "trip_drafts": (2, 0), "trip_events": (3, 0), "outbox": (1, 0)}
    assert await _counts(env["su"], TARGET) == dict.fromkeys([*TABLES, "spend"], 0)


class _DeniedS3:
    """An S3 client that refuses every call the way S3 does without the grant (botocore ClientError shape)."""

    class Error(Exception):
        def __init__(self):
            super().__init__("AccessDenied")
            self.response = {"Error": {"Code": "AccessDenied", "Message": "Access Denied"}}

    def put_object(self, **kw):
        raise self.Error()

    def get_object(self, **kw):
        raise self.Error()


async def test_s3_access_denied_fails_the_backup_job(env, monkeypatch):
    store = tenant_store.S3Store(bucket="denied-bucket", client=_DeniedS3())
    monkeypatch.setattr(tenant_store, "S3Store", lambda: store)
    job = await _enqueue(env, tenant_backup.KIND, {"tenant_id": TARGET})
    assert job["status"] == "failed", job
    assert job["error"].startswith(tenant_store.ACCESS_DENIED_REASON) and TARGET not in job["error"]
    drill = _only(await _serve(env, {"op": "restore_drill", "payload": {"tenant_id": TARGET, "backup_id": REQUEST}}))
    assert drill["status"] == "failed" and drill["error"].startswith(tenant_store.ACCESS_DENIED_REASON)


async def test_daily_tick_backs_up_the_pilot_tenants_once_a_marker_view_exists(env, monkeypatch, tmp_path):
    store = tenant_store.DirStore(str(tmp_path))
    monkeypatch.setattr(tenant_store, "S3Store", lambda: store)
    su = env["su"]

    # no CIS marker view: the run records no_feed and writes nothing (it does not even need the bucket)
    out = await _serve(env, {"op": "tick"})
    run = [j for j in out["jobs"] if j["kind"] == tenant_backup.KIND][0]
    assert run["status"] == "succeeded" and run["result"] == {"mode": "no_feed", "trigger": "schedule", "backups": []}
    assert not list(tmp_path.iterdir())

    # CIS marks TARGET as the pilot (a request row of another app, so the erase run leaves it alone)
    await su.execute(FEED_STANDIN)
    await su.execute(FEED_INSERT, PILOT_REQUEST, TARGET, "dry_run", 1, "booking", True, False, None, "-1 day")
    await su.execute(FEED_INSERT, REQUEST, OTHER, "dry_run", 1, "booking", False, True, None, "-1 day")
    await su.execute("UPDATE tripplanner.job SET idempotency_key = NULL WHERE kind = ANY($1::text[])",
                     [tenant_backup.KIND, tenant_erase.KIND])
    out = await _serve(env, {"op": "tick"})
    run = [j for j in out["jobs"] if j["kind"] == tenant_backup.KIND][0]
    assert run["status"] == "succeeded", run
    res = run["result"]
    assert (res["mode"], res["sources"], res["backup_id"]) == ("scheduled", ["catalog.v_tenant_erasure_v1"],
                                                               run["job_id"])
    assert [b["tenant_id"] for b in res["backups"]] == [TARGET]       # the pilot only, not OTHER
    assert res["backups"][0]["rows"]["tripplanner.trip_events"] == 3
    assert (tmp_path / res["backups"][0]["prefix"] / "SHA256SUMS").exists()

    # the drill finds the daily backup as the tenant's latest and restores it
    drill = _only(await _serve(env, {"op": "restore_drill", "payload": {"tenant_id": TARGET}}))
    assert drill["status"] == "succeeded", drill
    assert drill["result"]["passed"] is True and drill["result"]["backup_id"] == run["job_id"]
    assert drill["result"]["checks"]["sha256sums_matches_job"] is True
    drill = _only(await _serve(env, {"op": "restore_drill", "payload": {"tenant_id": OTHER, "backup_id": run["job_id"]}}))
    assert drill["status"] == "failed" and "another tenant" in drill["error"]
