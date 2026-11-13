"""AA-1093 lane L4 integration — tp_tenant_erase, tp_tenant_backup and tp_tenant_restore_drill on a real Postgres, as
the runtime role ``tripplanner`` through the jobs Lambda's own ``handler.serve``.

Runs only with ``pytest --pg-dsn postgresql://postgres@127.0.0.1:<port>/postgres`` (a SUPERUSER DSN of a throwaway
LOCAL Postgres). The database is the tp-030 replay (``migrations/tests/test_tp030_pg.erasure_database``: tp-028's
forced database with invented demo rows, tp-029, tp-030, invented rows of the TARGET and OTHER tenants). Each test
builds its own database, so an apply in one never shows in another. A stand-in of the CIS request view
``catalog.v_tenant_erasure_v1`` (a table behind a view, SELECT to tripplanner) is created only by the feed tests.

Checks: an operator DRY RUN logs the plan and deletes nothing; an apply of a hash no logged DRY RUN of that request
carries is refused (no_approved_plan); an apply with a stale plan is refused and logged; an apply of a tenant CIS does
not mark synthetic is refused on Dev; the feed runs dry run, apply and verify of the synthetic TARGET and erases every
row of it (and the customer only its sessions link) while OTHER and the demo rows stay; the pilot is refused; cis_app
reads the report; a backup of TARGET round-trips through the restore drill with equal counts and digests and the drill
leaves every live row as it was; a changed backup file fails the drill; S3 refusing a put fails the backup job with
s3_access_denied; the daily tick backs up the tenants CIS marks is_pilot (no_feed and nothing written before a marker
view exists), and the drill restores that daily backup.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import urlparse

import pytest

asyncpg = pytest.importorskip("asyncpg")

from backend.jobs import handler, tenant_backup, tenant_erase, tenant_restore_drill, tenant_store  # noqa: E402

MIGRATION_TESTS = Path(__file__).resolve().parents[2] / "migrations" / "tests"
TARGET = "a7a7a7a7-0000-4000-8000-0000000000e1"     # the invented tenants of test_tp030_pg.py
OTHER = "b8b8b8b8-0000-4000-8000-0000000000e2"
PILOT = "c0c0c0c0-0000-4000-8000-0000000000e4"
REQUEST = "d1d1d1d1-0000-4000-8000-0000000000e5"
PILOT_REQUEST = "d2d2d2d2-0000-4000-8000-0000000000e6"
DEV_ON = {"ENVIRONMENT": "dev", "TENANT_ERASE_APPLY_ENABLED": "true"}
TABLES = ("sessions", "trip_drafts", "trip_events", "outbox")

FEED_STANDIN = """
CREATE SCHEMA IF NOT EXISTS catalog;
CREATE TABLE catalog.erasure_feed_standin (request_id uuid, tenant_id uuid, app text, mode text,
                                           tripplanner_plan_hash text, is_pilot boolean, is_synthetic boolean);
CREATE VIEW catalog.v_tenant_erasure_v1 AS SELECT * FROM catalog.erasure_feed_standin;
GRANT USAGE ON SCHEMA catalog TO tripplanner;
GRANT SELECT ON catalog.v_tenant_erasure_v1 TO tripplanner;
"""


def _replay():
    if str(MIGRATION_TESTS) not in sys.path:
        sys.path.insert(0, str(MIGRATION_TESTS))
    import test_tp025_pg as replay  # noqa: E402  (the drop helper)
    import test_tp030_pg as tp030  # noqa: E402  (the tp-030 database)
    return replay, tp030


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
    replay, tp030 = _replay()
    ctx, error = tp030.erasure_database(pg_dsn)
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


async def _log(su) -> list[dict]:
    return [dict(r) for r in await su.fetch(
        "SELECT request_id::text AS request_id, tenant_id::text AS tenant_id, mode, status, reason, plan_hash "
        "FROM tripplanner.tenant_erasure_log ORDER BY created_at, mode")]


async def _enqueue(env, kind, payload):
    return _only(await _serve(env, {"op": "enqueue", "kind": kind, "payload": payload}))


async def test_operator_dry_run_logs_the_plan_and_deletes_nothing(env, monkeypatch):
    monkeypatch.setattr(tenant_erase.os, "environ", DEV_ON)
    before = await _counts(env["su"], TARGET)
    job = await _enqueue(env, tenant_erase.KIND, {"tenant_id": TARGET, "mode": "dry_run"})
    assert job["status"] == "succeeded", job
    entry = job["result"]["requests"][0]
    assert (entry["status"], entry["request_id"]) == ("planned", job["job_id"])
    assert entry["rows"]["tripplanner.trip_events"] == 3 and len(entry["plan_hash"]) == 64
    assert await _counts(env["su"], TARGET) == before
    assert [(r["mode"], r["status"], r["plan_hash"]) for r in await _log(env["su"])] == \
        [("dry_run", "planned", entry["plan_hash"])]


async def test_apply_refuses_a_stale_plan_and_a_tenant_without_markers(env, monkeypatch):
    monkeypatch.setattr(tenant_erase.os, "environ", DEV_ON)
    untouched = {"sessions": 2, "trip_drafts": 2, "trip_events": 3, "outbox": 1, "spend": 2}
    # no CIS view: the markers are unknown, so an apply is refused before the plan is even looked at
    job = await _enqueue(env, tenant_erase.KIND, {"tenant_id": TARGET, "mode": "apply", "expect_plan": "0" * 64})
    assert job["result"]["requests"][0]["reason"] == "tenant_markers_unknown" and job["result"]["attention"]
    await env["su"].execute(FEED_STANDIN)
    await env["su"].execute("INSERT INTO catalog.erasure_feed_standin (request_id, tenant_id, app, mode, is_pilot, "
                            "is_synthetic) VALUES ($1::uuid, $2::uuid, 'booking', 'dry_run', false, true)",
                            REQUEST, TARGET)

    # a hash no DRY RUN of this request logged (even the tenant's real current plan) is not an approved plan
    async with env["pool"].acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_scope', 'system', false)")
        real = json.loads(await conn.fetchval("SELECT tripplanner.erase_tenant($1::uuid, true)::text", TARGET))
        await conn.execute("RESET app.tenant_scope")
    for expect in ("0" * 64, real["plan_hash"]):
        job = await _enqueue(env, tenant_erase.KIND, {"tenant_id": TARGET, "mode": "apply", "expect_plan": expect,
                                                      "request_id": REQUEST})
        entry = job["result"]["requests"][0]
        assert (entry["status"], entry["reason"], entry["deleted"]) == ("refused", "no_approved_plan", {}), expect
        assert job["result"]["attention"]
    assert await _counts(env["su"], TARGET) == untouched

    # the DRY RUN of this request is logged, then the tenant writes: the approved hash is stale
    dry = await _enqueue(env, tenant_erase.KIND, {"tenant_id": TARGET, "mode": "dry_run", "request_id": REQUEST})
    plan = dry["result"]["requests"][0]["plan_hash"]
    assert plan == real["plan_hash"]
    await env["su"].execute("UPDATE tripplanner.trip_drafts SET status = 'sent' WHERE tenant_id = $1::uuid", TARGET)
    job = await _enqueue(env, tenant_erase.KIND, {"tenant_id": TARGET, "mode": "apply", "expect_plan": plan,
                                                  "request_id": REQUEST})
    entry = job["result"]["requests"][0]
    assert (entry["status"], entry["reason"], entry["deleted"]) == ("refused", "plan_changed", {})
    assert await _counts(env["su"], TARGET) == untouched
    # a tenant CIS does not mark synthetic is refused on Dev
    await env["su"].execute("UPDATE catalog.erasure_feed_standin SET is_synthetic = false")
    job = await _enqueue(env, tenant_erase.KIND, {"tenant_id": TARGET, "mode": "apply", "expect_plan": plan,
                                                  "request_id": REQUEST})
    assert job["result"]["requests"][0]["reason"] == "not_synthetic"


async def test_feed_runs_dry_run_apply_verify_and_erases_the_tenant_only(env, monkeypatch, tmp_path):
    monkeypatch.setattr(tenant_erase.os, "environ", DEV_ON)
    # the feed marks PILOT is_pilot, so the same ticks also run the daily pilot backup: give it a store
    monkeypatch.setattr(tenant_store, "S3Store", lambda: tenant_store.DirStore(str(tmp_path)))
    su = env["su"]
    await su.execute(FEED_STANDIN)
    other_before = await _counts(su, OTHER)
    demo_before = await su.fetchval("SELECT count(*) AS n FROM tripplanner.sessions WHERE tenant_id <> ALL($1::uuid[])",
                                    [TARGET, OTHER])
    await su.execute("INSERT INTO catalog.erasure_feed_standin VALUES ($1::uuid, $2::uuid, 'tripplanner', 'dry_run', "
                     "NULL, false, true), ($3::uuid, $4::uuid, 'tripplanner', 'dry_run', NULL, true, false), "
                     "($1::uuid, $2::uuid, 'booking', 'apply', NULL, false, true)",
                     REQUEST, TARGET, PILOT_REQUEST, PILOT)

    # the scheduled payload is reserved for the tick: run it the way the tick does
    out = await _serve(env, {"op": "tick"})
    run = [j for j in out["jobs"] if j["kind"] == tenant_erase.KIND][0]
    assert run["result"]["mode"] == "feed"
    by_request = {e["request_id"]: e for e in run["result"]["requests"]}
    assert set(by_request) == {REQUEST, PILOT_REQUEST}                    # the booking row is not ours
    assert by_request[REQUEST]["status"] == "planned"
    assert (by_request[PILOT_REQUEST]["status"], by_request[PILOT_REQUEST]["reason"]) == ("refused", "pilot_tenant")
    plan = by_request[REQUEST]["plan_hash"]

    # the tech lead approves the plan: CIS adds the apply and the verify rows with the approved hash
    await su.execute("INSERT INTO catalog.erasure_feed_standin VALUES ($1::uuid, $2::uuid, 'tripplanner', 'apply', $3, "
                     "false, true)", REQUEST, TARGET, plan)
    await su.execute("UPDATE tripplanner.job SET idempotency_key = NULL WHERE kind = $1", tenant_erase.KIND)
    out = await _serve(env, {"op": "tick"})
    run = [j for j in out["jobs"] if j["kind"] == tenant_erase.KIND][0]
    applied = {(e["request_id"], e["mode"]): e for e in run["result"]["requests"]}
    assert applied[(REQUEST, "apply")]["status"] == "applied"
    assert (REQUEST, "dry_run") not in applied                         # already planned: skipped
    assert (PILOT_REQUEST, "dry_run") not in applied                   # a final refusal is not retried
    assert await _counts(su, TARGET) == dict.fromkeys([*TABLES, "spend"], 0)
    assert await _counts(su, OTHER) == other_before
    assert await su.fetchval("SELECT count(*) AS n FROM tripplanner.sessions WHERE tenant_id <> ALL($1::uuid[])",
                             [TARGET, OTHER]) == demo_before
    _, tp030 = _replay()
    assert applied[(REQUEST, "apply")]["deleted"]["tripplanner.customers"] == 1
    assert sorted(r["id"] for r in await su.fetch("SELECT id::text AS id FROM tripplanner.customers "
                                                  "WHERE id = ANY($1::uuid[])", list(tp030.CUSTOMERS))) == \
        sorted([tp030.SHARED, tp030.ONLY_OTHER])                       # only the customer no other tenant links

    await su.execute("INSERT INTO catalog.erasure_feed_standin VALUES ($1::uuid, $2::uuid, 'tripplanner', 'verify', "
                     "NULL, false, true)", REQUEST, TARGET)
    await su.execute("UPDATE tripplanner.job SET idempotency_key = NULL WHERE kind = $1", tenant_erase.KIND)
    out = await _serve(env, {"op": "tick"})
    run = [j for j in out["jobs"] if j["kind"] == tenant_erase.KIND][0]
    assert [(e["mode"], e["status"]) for e in run["result"]["requests"]] == [("verify", "verified")]

    # cis_app reads the report of every mode (ids, counts, hashes)
    async with su.transaction():
        await su.execute("SET LOCAL ROLE cis_app")
        report = await su.fetch("SELECT mode, status, plan_hash FROM tripplanner.v_tenant_erasure_report_v1 "
                                "WHERE request_id = $1::uuid ORDER BY mode", REQUEST)
    assert [(r["mode"], r["status"]) for r in report] == [("apply", "applied"), ("dry_run", "planned"),
                                                          ("verify", "verified")]
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
        plan = json.loads(await conn.fetchval("SELECT tripplanner.erase_tenant($1::uuid, true)::text", TARGET))
        await conn.fetchval("SELECT tripplanner.erase_tenant($1::uuid, false, $2)::text", TARGET, plan["plan_hash"])
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
    await su.execute("INSERT INTO catalog.erasure_feed_standin VALUES ($1::uuid, $2::uuid, 'booking', 'dry_run', NULL, "
                     "true, false), ($3::uuid, $4::uuid, 'booking', 'dry_run', NULL, false, true)",
                     PILOT_REQUEST, TARGET, REQUEST, OTHER)
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
