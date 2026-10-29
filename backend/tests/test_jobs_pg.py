"""AA-861 integration — the jobs Lambda on a real Postgres: tripplanner.job claim / lease / retry and the
tp_retention_outbox_redact kind end to end (DRY RUN, override, live, schedule, HELD), as the runtime role.

Runs only with ``pytest --pg-dsn postgresql://postgres@127.0.0.1:<port>/postgres`` against a throwaway LOCAL Postgres
(non-local hosts are refused); each test creates and drops its own ``tp_aa861_*`` database. The schema is the REAL
migration migrations/021_job_table_outbox_redaction.sql, run as tp_owner over a stand-in of what it needs (schema
tripplanner owned by tp_owner with the tp-013 default privileges, tripplanner.outbox in its tp-008 shape,
shared.schema_versions); the code then connects as the runtime role ``tripplanner``, so the grants are tested too.
Roles are cluster-wide: ``tp_owner`` / ``tripplanner`` are created when missing and ``tripplanner`` gets LOGIN.

Outbox rows are synthetic (example.test addresses, a 555 phone number) and backdated with ``created_at``.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import pytest

asyncpg = pytest.importorskip("asyncpg")

from backend.jobs import handler, outbox_redact as kind, queue, runner  # noqa: E402
from backend.jobs.queue import Job  # noqa: E402

TP021 = Path(__file__).resolve().parents[2] / "migrations" / "021_job_table_outbox_redaction.sql"
STANDIN = """
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tp_owner') THEN CREATE ROLE tp_owner NOLOGIN; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tripplanner') THEN CREATE ROLE tripplanner LOGIN; END IF;
END $$;
ALTER ROLE tripplanner LOGIN;
CREATE SCHEMA shared;
CREATE TABLE shared.schema_versions (version text PRIMARY KEY, applied_at timestamptz, description text);
GRANT USAGE ON SCHEMA shared TO tp_owner;
GRANT SELECT, INSERT ON shared.schema_versions TO tp_owner;
CREATE SCHEMA tripplanner AUTHORIZATION tp_owner;
GRANT USAGE ON SCHEMA tripplanner TO tripplanner;
ALTER DEFAULT PRIVILEGES FOR ROLE tp_owner IN SCHEMA tripplanner GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO tripplanner;
ALTER DEFAULT PRIVILEGES FOR ROLE tp_owner IN SCHEMA tripplanner GRANT SELECT, USAGE ON SEQUENCES TO tripplanner;
SET ROLE tp_owner;
CREATE TABLE tripplanner.outbox (
    id BIGSERIAL PRIMARY KEY, event TEXT NOT NULL, schema_version INT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE,
    aggregate_id UUID NOT NULL, payload JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT outbox_event_chk CHECK (event IN ('inquiry.submitted')));
CREATE VIEW tripplanner.v_inquiry_outbox AS
SELECT id, schema_version, idempotency_key, aggregate_id AS trip_id, payload, created_at
FROM tripplanner.outbox WHERE event = 'inquiry.submitted';
RESET ROLE;
"""


def _contact_payload(key: str, trip: str, n: int) -> dict:
    return {"contract": "inquiry.submitted", "version": 1.4, "idempotency_key": key, "source": "tripplanner",
            "trip_id": trip, "handoff_id": f"h-{n}", "submitted_at": "2026-07-01T00:00:00+00:00",
            "customer": {"customer_id": None, "name": f"Test Traveller {n}", "phone": "+1 555 0100",
                         "email": f"traveller{n}@example.test", "consent": True, "consent_at": None,
                         "verified": False, "identity": None},
            "itinerary": {"legs": [], "days": [{"day": 1, "component_id": "c-1", "name": "Old Quarter"}],
                          "custom_days": [{"day_id": "x", "kind": "free", "after_component_id": None,
                                           "note": "free text"}]}}


@pytest.fixture(scope="module")
def pg_dsn(request):
    dsn = request.config.getoption("--pg-dsn")
    if not dsn:
        pytest.skip("integration: pass --pg-dsn for a throwaway local Postgres")
    host = urlparse(dsn).hostname or "localhost"
    if host not in ("localhost", "127.0.0.1", "::1"):
        pytest.skip(f"integration tests only run against a local Postgres, not {host}")
    return dsn


@pytest.fixture
async def env(pg_dsn):
    name = f"tp_aa861_{uuid.uuid4().hex[:8]}"
    admin = await asyncpg.connect(pg_dsn)
    await admin.execute(f'CREATE DATABASE "{name}"')
    db_dsn = urlunparse(urlparse(pg_dsn)._replace(path=f"/{name}"))
    su = await asyncpg.connect(db_dsn)
    pool = None
    try:
        await su.execute(STANDIN)
        await su.execute("SET ROLE tp_owner")
        await su.execute(TP021.read_text(encoding="utf-8"))
        await su.execute("RESET ROLE")
        parsed = urlparse(db_dsn)
        tp_dsn = urlunparse(parsed._replace(netloc=f"tripplanner@{parsed.hostname}:{parsed.port or 5432}"))
        pool = await asyncpg.create_pool(tp_dsn, min_size=1, max_size=1)
        yield {"su": su, "pool": pool}
    finally:
        if pool is not None:
            await pool.close()
        await su.close()
        await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        await admin.close()


async def _outbox(su, n: int, age_days: int) -> list[int]:
    ids = []
    for i in range(n):
        trip = str(uuid.uuid4())
        key = f"{trip}:h-{i}"
        ids.append(await su.fetchval(
            "INSERT INTO tripplanner.outbox (event, schema_version, idempotency_key, aggregate_id, payload, created_at) "
            "VALUES ('inquiry.submitted', 1, $1, $2, $3::jsonb, now() - make_interval(days => $4)) RETURNING id",
            key, trip, json.dumps(_contact_payload(key, trip, i)), age_days))
    return ids


def _never_out_of_time() -> float:
    return 600.0


async def _serve(env, event, today=None):
    return await handler.serve(event, pool=env["pool"], worker="test-worker", time_left=_never_out_of_time,
                               today=today)


async def test_tick_queues_one_scheduled_dry_run_per_day_and_counts_without_writing(env):
    su = env["su"]
    await _outbox(su, 3, 120)
    await _outbox(su, 2, 10)
    first = await _serve(env, {"op": "tick"}, today=date(2026, 10, 29))
    again = await _serve(env, {"op": "tick"}, today=date(2026, 10, 29))
    assert len(first["queued"]) == 1 and again["queued"] == [] and again["jobs"] == []
    job = first["jobs"][0]
    assert job["status"] == "succeeded" and job["kind"] == kind.KIND
    res = job["result"]
    assert (res["mode"], res["trigger"], res["candidates_total"], res["candidates_with_contact"]) == \
        ("dry_run", "schedule", 3, 3)
    assert res["reason"].startswith("no approved live run")
    assert await su.fetchval("SELECT count(*) FROM tripplanner.outbox WHERE redacted_at IS NOT NULL") == 0
    row = await su.fetchrow("SELECT idempotency_key, max_attempts, created_by FROM tripplanner.job WHERE id = $1::uuid",
                            job["job_id"])
    assert (row["idempotency_key"], row["max_attempts"], row["created_by"]) == \
        ("tp_retention_outbox_redact:schedule:2026-10-29", 1, handler.SCHEDULER)


async def test_override_dry_run_counts_young_rows_without_the_gate(env):
    su = env["su"]
    await _outbox(su, 2, 10)
    await _outbox(su, 1, 2)
    out = await _serve(env, {"op": "enqueue", "kind": kind.KIND,
                             "payload": {"trigger": "manual_override", "older_than_days": 5}})
    res = out["jobs"][0]["result"]
    assert (res["mode"], res["candidates_total"], res["override"]["older_than_days"]) == ("dry_run", 2, 5)
    assert await su.fetchval("SELECT count(*) FROM tripplanner.outbox WHERE redacted_at IS NOT NULL") == 0


async def test_live_override_is_refused_without_the_opt_in(env):
    await _outbox(env["su"], 2, 10)
    out = await _serve(env, {"op": "enqueue", "kind": kind.KIND, "payload": {
        "trigger": "manual_override", "older_than_days": 7, "dry_run": False, "max_rows": 2}})
    job = out["jobs"][0]
    assert job["status"] == "failed" and "refused" in job["error"]


async def test_manual_live_run_redacts_oldest_first_keeps_keys_and_dedupe(env):
    su = env["su"]
    old = await _outbox(su, 5, 120)
    young = await _outbox(su, 1, 30)
    out = await _serve(env, {"op": "enqueue", "kind": kind.KIND, "payload": {
        "dry_run": False, "max_rows": 3, "batch_size": 2, "pause_ms": 0}})
    res = out["jobs"][0]["result"]
    assert (res["mode"], res["affected_total"], res["parts"][0]["batches"]) == ("live", 3, 2)
    assert res["remaining_with_contact"] == 2  # max_rows stopped it; the rest waits
    assert res["parts"][0]["fields_redacted"] == {"customer.name": 3, "customer.email": 3, "customer.phone": 3,
                                                  "itinerary.custom_days[].note": 3}
    redacted = [r["id"] for r in await su.fetch(
        "SELECT id FROM tripplanner.outbox WHERE redacted_at IS NOT NULL ORDER BY id")]
    assert redacted == old[:3]  # oldest first (same age: id order)

    # Finish the rest; the young row is never touched.
    out = await _serve(env, {"op": "enqueue", "kind": kind.KIND, "payload": {"dry_run": False, "max_rows": 100,
                                                                              "pause_ms": 0}})
    res = out["jobs"][0]["result"]
    assert (res["affected_total"], res["remaining_with_contact"]) == (2, 0)
    rows = await su.fetch("SELECT id, idempotency_key, schema_version, aggregate_id, payload::text AS payload, "
                          "redacted_at FROM tripplanner.outbox ORDER BY id")
    for r in rows:
        payload = json.loads(r["payload"])
        if r["id"] in old:
            assert r["redacted_at"] is not None
            assert payload["customer"]["name"] is None and payload["customer"]["email"] is None
            assert payload["customer"]["phone"] is None and payload["customer"]["consent"] is True
            assert payload["idempotency_key"] == r["idempotency_key"] and r["schema_version"] == 1
            assert payload["itinerary"]["days"][0]["name"] == "Old Quarter"
        else:
            assert r["id"] in young and r["redacted_at"] is None and payload["customer"]["name"]

    # The runbook verification query: no row older than 90 days still holds contact data.
    cut = datetime.now(timezone.utc) - timedelta(days=90)
    assert await su.fetchval(kind.REMAINING_SQL, cut) == 0
    # Dedupe: the outbox still refuses a second row under a redacted row's key, and the view still serves it.
    key = rows[0]["idempotency_key"]
    dup = await su.fetchval(
        "INSERT INTO tripplanner.outbox (event, schema_version, idempotency_key, aggregate_id, payload) "
        "VALUES ('inquiry.submitted', 1, $1, $2, '{}'::jsonb) ON CONFLICT (idempotency_key) DO NOTHING RETURNING id",
        key, rows[0]["aggregate_id"])
    assert dup is None
    assert await su.fetchval("SELECT count(*) FROM tripplanner.v_inquiry_outbox WHERE idempotency_key = $1", key) == 1
    # A third run finds nothing.
    out = await _serve(env, {"op": "enqueue", "kind": kind.KIND, "payload": {}})
    assert out["jobs"][0]["result"]["candidates_total"] == 0


async def test_schedule_goes_live_after_an_approved_live_run_and_holds_on_growth(env):
    su = env["su"]
    await _outbox(su, 2, 120)
    await _serve(env, {"op": "enqueue", "kind": kind.KIND, "payload": {"dry_run": False, "max_rows": 2,
                                                                        "pause_ms": 0}})
    await _outbox(su, 3, 100)
    out = await _serve(env, {"op": "tick"}, today=date(2026, 10, 30))
    res = out["jobs"][0]["result"]
    assert (res["mode"], res["affected_total"], res["previous_live_affected"]) == ("live", 3, 2)

    # Growth past max(2 x 3, 1000) is HELD: nothing written, and a tick reports it as an error.
    await su.execute(
        "INSERT INTO tripplanner.outbox (event, schema_version, idempotency_key, aggregate_id, payload, created_at) "
        "SELECT 'inquiry.submitted', 1, 'bulk:' || g, gen_random_uuid(), '{}'::jsonb, now() - interval '120 days' "
        "FROM generate_series(1, 1001) g")
    with pytest.raises(handler.JobsNeedAttention):
        await _serve(env, {"op": "tick"}, today=date(2026, 10, 31))
    held = await su.fetchrow("SELECT status, result::text AS result FROM tripplanner.job "
                             "WHERE idempotency_key = 'tp_retention_outbox_redact:schedule:2026-10-31'")
    assert held["status"] == "succeeded" and json.loads(held["result"])["mode"] == "held"
    assert await su.fetchval("SELECT count(*) FROM tripplanner.outbox WHERE redacted_at IS NULL") == 1001


async def test_override_live_run_never_counts_as_the_last_live_run(env):
    su = env["su"]
    await _outbox(su, 2, 10)
    payload = {"trigger": "manual_override", "older_than_days": 7, "dry_run": False, "max_rows": 2, "pause_ms": 0}
    job = Job(id=str(await su.fetchval(
        "INSERT INTO tripplanner.job (kind, payload, max_attempts, status, locked_by, locked_until, attempt) "
        "VALUES ($1, $2::jsonb, 1, 'running', 'test-worker', now() + interval '5 minutes', 1) RETURNING id",
        kind.KIND, json.dumps(payload))), kind=kind.KIND, payload=payload)
    result = await kind.run(env["pool"], job, environ={"ENVIRONMENT": "dev", kind.OVERRIDE_FLAG: "true"})
    assert (result["mode"], result["affected_total"]) == ("live", 2)
    async with env["pool"].acquire() as conn:
        await queue.complete(conn, job.id, "test-worker", result)
        assert await kind.last_live(conn) is None
    out = await _serve(env, {"op": "tick"}, today=date(2026, 11, 1))
    assert out["jobs"][0]["result"]["mode"] == "dry_run"


async def test_claim_lease_reap_and_retry_backoff(env):
    su, pool = env["su"], env["pool"]
    async with pool.acquire() as conn:
        a = await queue.enqueue(conn, "tp_probe", {}, created_by="test", max_attempts=2)
        b = await queue.enqueue(conn, "tp_probe", {}, created_by="test", max_attempts=1)
        assert await queue.enqueue(conn, "tp_probe", {}, created_by="test", idempotency_key="k",
                                   max_attempts=3) is not None
        assert await queue.enqueue(conn, "tp_probe", {}, created_by="test", idempotency_key="k") is None
        job_a = await queue.claim(conn, "w1", ["tp_probe"], 60)
        job_b = await queue.claim(conn, "w1", ["tp_probe"], 60)
    assert {job_a.id, job_b.id} == {a, b} and job_a.attempt == 1
    # Both leases run out: the job with an attempt left goes back to the queue, the other fails.
    await su.execute("UPDATE tripplanner.job SET locked_until = now() - interval '1 second' WHERE id = ANY($1::uuid[])",
                     [a, b])
    async with pool.acquire() as conn:
        assert await queue.reap(conn) == {"cancelled": 0, "reaped": 2}
        # A reaped job's late completion is refused (the lease is gone).
        assert await queue.complete(conn, a, "w1", {"x": 1}) is False
    states = {r["id"]: (r["status"], r["error"]) for r in await su.fetch(
        "SELECT id::text AS id, status, error FROM tripplanner.job WHERE id = ANY($1::uuid[])", [a, b])}
    assert states[a] == ("queued", queue.LEASE_EXPIRED_ERROR) and states[b] == ("failed", queue.LEASE_EXPIRED_ERROR)

    async def boom(_pool, _job):
        raise RuntimeError("payload text that must not be stored")

    out = await runner.run_due(pool, {"tp_probe": boom}, worker="w2", time_left=_never_out_of_time)
    statuses = sorted(j["status"] for j in out["jobs"])
    assert statuses == ["failed", "queued"]  # a: attempt 2 of 2 -> failed; the keyed job: retry with backoff
    rows = await su.fetch("SELECT status, error, run_after > now() AS later FROM tripplanner.job WHERE kind = 'tp_probe'")
    assert all(r["error"] in ("RuntimeError", queue.LEASE_EXPIRED_ERROR) for r in rows)
    assert any(r["status"] == "queued" and r["later"] for r in rows)


async def test_cancel_requested_queued_job_never_starts(env):
    su, pool = env["su"], env["pool"]
    async with pool.acquire() as conn:
        job_id = await queue.enqueue(conn, "tp_probe", {}, created_by="test")
    await su.execute("UPDATE tripplanner.job SET cancel_requested = true WHERE id = $1::uuid", job_id)
    out = await runner.run_due(pool, {"tp_probe": lambda *_: None}, worker="w", time_left=_never_out_of_time)
    assert out["reaped"]["cancelled"] == 1 and out["jobs"] == []


async def test_runner_stops_claiming_when_the_invocation_is_almost_over(env):
    async with env["pool"].acquire() as conn:
        await queue.enqueue(conn, "tp_probe", {}, created_by="test")
    out = await runner.run_due(env["pool"], {"tp_probe": lambda *_: None}, worker="w", time_left=lambda: 10.0)
    assert out["jobs"] == []


async def test_status_and_bad_events(env):
    await _serve(env, {"op": "tick"}, today=date(2026, 10, 29))
    status = await _serve(env, {"op": "status"})
    assert status["jobs"][0]["kind"] == kind.KIND and status["jobs"][0]["result"]["mode"] == "dry_run"
    assert "unknown op" in (await _serve(env, {}))["error"]
    assert "unknown kind" in (await _serve(env, {"op": "enqueue", "kind": "bk_retention_job"}))["error"]
    assert "reserved" in (await _serve(env, {"op": "enqueue", "kind": kind.KIND,
                                             "payload": {"trigger": "schedule"}}))["error"]
