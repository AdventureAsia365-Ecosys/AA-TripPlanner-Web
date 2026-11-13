"""AA-1093 lane L4 (offline): the tenant erasure, backup and restore drill rules of the jobs Lambda.

The database side (the plan hash, the apply, the round trip) is in test_aa1093_tenant_erase_pg.py and
migrations/tests/test_tp030_pg.py. Tenant ids here are invented.
"""
from __future__ import annotations

import asyncio
import json
from datetime import date

import pytest

from backend import config
from backend.jobs import handler, tenant_backup, tenant_erase, tenant_restore_drill, tenant_store
from backend.jobs.runner import PermanentError
from backend.jobs.tenant_target import Markers

TENANT = "a7a7a7a7-0000-4000-8000-0000000000e1"
PLAN = "ab" * 32
SYNTHETIC = Markers(is_pilot=False, is_synthetic=True, source="test")
REAL = Markers(is_pilot=False, is_synthetic=False, source="test")
PILOT = Markers(is_pilot=True, is_synthetic=False, source="test")
DEV_ON = {"ENVIRONMENT": "dev", "TENANT_ERASE_APPLY_ENABLED": "true"}
PROD_ON = {"ENVIRONMENT": "prod", "TENANT_ERASE_APPLY_ENABLED": "true"}


# ── refusals ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("mode,tenant,markers,plan,environ,expected", [
    ("dry_run", TENANT, None, None, {}, None),                          # a DRY RUN needs no flag and no markers
    ("verify", TENANT, REAL, None, {}, None),
    ("dry_run", config.DEMO_TENANT_ID, SYNTHETIC, None, DEV_ON, "demo_tenant"),
    ("dry_run", TENANT, PILOT, None, DEV_ON, "pilot_tenant"),
    ("apply", TENANT, PILOT, PLAN, PROD_ON, "pilot_tenant"),
    ("apply", TENANT, SYNTHETIC, PLAN, {"ENVIRONMENT": "dev"}, "apply_disabled"),
    ("apply", TENANT, SYNTHETIC, PLAN, {"ENVIRONMENT": "dev", "TENANT_ERASE_APPLY_ENABLED": "yes"}, "apply_disabled"),
    ("apply", TENANT, None, PLAN, DEV_ON, "tenant_markers_unknown"),
    ("apply", TENANT, REAL, PLAN, DEV_ON, "not_synthetic"),
    # no ENVIRONMENT = not prod
    ("apply", TENANT, REAL, PLAN, {"TENANT_ERASE_APPLY_ENABLED": "true"}, "not_synthetic"),
    ("apply", TENANT, SYNTHETIC, None, DEV_ON, "no_approved_plan"),
    ("apply", TENANT, SYNTHETIC, "ABC", DEV_ON, "no_approved_plan"),
    ("apply", TENANT, SYNTHETIC, PLAN, DEV_ON, None),
    ("apply", TENANT, REAL, PLAN, PROD_ON, None),                       # prod: any tenant but the pilot
])
def test_refusal_order(mode, tenant, markers, plan, environ, expected):
    assert tenant_erase.refusal(mode, tenant, markers, plan, environ) == expected


@pytest.mark.parametrize("payload,message", [
    ([], "must be an object"),
    ({"tenant_id": TENANT, "force": True}, "unknown payload keys"),
    ({"trigger": "cron"}, "trigger must be one of"),
    ({"trigger": "schedule", "tenant_id": TENANT}, "takes no other key"),
    ({"tenant_id": TENANT, "mode": "delete"}, "mode must be one of"),
    ({"tenant_id": TENANT, "mode": "apply", "expect_plan": "short"}, "expect_plan must be"),
    ({"tenant_id": TENANT, "request_id": "not-a-uuid"}, "request_id must be a uuid"),
])
def test_erase_payload_is_validated_without_echoing_values(payload, message):
    with pytest.raises(PermanentError) as e:
        tenant_erase.validate_payload(payload)
    assert message in str(e.value) and "not-a-uuid" not in str(e.value)


def test_verdict_of_each_mode():
    rows = {"tripplanner.sessions": {"action": "delete", "rows": 0},
            "tripplanner.customers": {"action": "keep", "rows": 3}}
    assert tenant_erase.verdict("dry_run", {"status": "planned", "tables": rows}) == ("planned", None)
    assert tenant_erase.verdict("apply", {"status": "applied"}) == ("applied", None)
    assert tenant_erase.verdict("apply", {"status": "refused", "reason": "plan_changed"}) == ("refused", "plan_changed")
    assert tenant_erase.verdict("verify", {"status": "planned", "tables": rows}) == ("verified", None)  # keep ignored
    left = {**rows, "tripplanner.ai_spend_month": {"action": "merge_into_app", "rows": 1}}
    assert tenant_erase.verdict("verify", {"status": "planned", "tables": left}) == ("residue", "rows_left")


def test_attention_on_a_refused_apply_or_a_residue_only():
    assert tenant_erase.needs_attention({"mode": "apply", "status": "refused"})
    assert tenant_erase.needs_attention({"mode": "verify", "status": "residue"})
    assert not tenant_erase.needs_attention({"mode": "dry_run", "status": "refused"})
    assert not tenant_erase.needs_attention({"mode": "apply", "status": "applied"})


class _Conn:
    """Answers the log read; records every statement. A refusal must write the log row and never call the function."""

    def __init__(self, existing=None):
        self.existing = existing
        self.calls = []

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql))
        return self.existing

    async def execute(self, sql, *args):
        self.calls.append(("execute", sql, args))

    async def fetchval(self, sql, *args):  # pragma: no cover - must not run on a refusal
        raise AssertionError("erase_tenant must not run")


def test_a_refused_apply_is_logged_and_runs_nothing():
    conn = _Conn()
    out = asyncio.run(tenant_erase.erase_one(conn, request_id=TENANT, tenant_id=TENANT, mode="apply", expect_plan=PLAN,
                                             markers=REAL, job_id=TENANT, environ=DEV_ON))
    assert (out["status"], out["reason"]) == ("refused", "not_synthetic")
    writes = [c for c in conn.calls if c[0] == "execute"]
    assert len(writes) == 1 and "INSERT INTO tripplanner.tenant_erasure_log" in writes[0][1]
    assert writes[0][2][5:7] == ("refused", "not_synthetic")


def test_an_applied_request_is_not_run_again_and_another_tenant_is_refused():
    applied = {"tenant_id": TENANT, "status": "applied", "reason": None, "plan_hash": PLAN,
               "counts": json.dumps({"tables": {"tripplanner.sessions": {"rows": 2}}, "deleted": {"x": 2}})}
    out = asyncio.run(tenant_erase.erase_one(_Conn(applied), request_id=TENANT, tenant_id=TENANT, mode="apply",
                                             expect_plan=PLAN, markers=SYNTHETIC, job_id=TENANT, environ=DEV_ON))
    assert out["already_applied"] and out["status"] == "applied" and out["rows"] == {"tripplanner.sessions": 2}
    other = {**applied, "tenant_id": "b8b8b8b8-0000-4000-8000-0000000000e2"}
    out = asyncio.run(tenant_erase.erase_one(_Conn(other), request_id=TENANT, tenant_id=TENANT, mode="dry_run",
                                             expect_plan=None, markers=None, job_id=TENANT, environ={}))
    assert (out["status"], out["reason"], out["logged"]) == ("refused", "request_tenant_mismatch", False)


# ── handler wiring ───────────────────────────────────────────────────────────


def test_kinds_are_registered_and_the_erase_runs_daily_once():
    for k in (tenant_erase, tenant_backup, tenant_restore_drill):
        assert k.KIND in handler.HANDLERS and handler.MAX_ATTEMPTS[k.KIND] == k.MAX_ATTEMPTS
        assert k.KIND.startswith("tp_")
    assert set(handler.DAILY) - {"tp_retention_outbox_redact", "tp_retention_ai_rate_counter",
                                 "tp_lead_deliver"} == {tenant_erase.KIND}
    payload, attempts, key = handler.DAILY[tenant_erase.KIND]
    assert payload == {"trigger": "schedule"} and attempts == 1
    assert key(date(2026, 11, 13)) == "tp_tenant_erase:schedule:2026-11-13"
    assert handler.OP_KINDS == {"restore_drill": tenant_restore_drill.KIND}


def test_restore_drill_op_is_an_enqueue_of_its_kind(monkeypatch):
    seen = {}

    async def enqueue(conn, kind, payload, **kw):
        seen.update(kind=kind, payload=payload, created_by=kw["created_by"])
        return "job-1"

    async def run_due(pool, handlers, **kw):
        return {"reaped": {"reaped": 0, "cancelled": 0}, "jobs": []}

    class _Pool:
        def acquire(self):
            class _Ctx:
                async def __aenter__(self):
                    return object()

                async def __aexit__(self, *a):
                    return False
            return _Ctx()

    monkeypatch.setattr(handler.queue, "enqueue", enqueue)
    monkeypatch.setattr(handler.runner, "run_due", run_due)
    out = asyncio.run(handler.serve({"op": "restore_drill", "payload": {"tenant_id": TENANT}}, pool=_Pool(),
                                    worker="w", time_left=lambda: 600.0))
    assert out["queued"] == ["job-1"]
    assert seen == {"kind": tenant_restore_drill.KIND, "payload": {"tenant_id": TENANT}, "created_by": handler.OPERATOR}


# ── backup files (pure) ──────────────────────────────────────────────────────


def _files():
    lines = {"sessions": ['{"id": "s1", "tenant_id": "t"}'], "trip_drafts": [],
             "trip_events": ['{"id": 1}', '{"id": 2}'], "outbox": []}
    cols = {t: ["id", "tenant_id"] for t in tenant_backup.TABLES}
    return tenant_backup.build(TENANT, "bk-1", lines, cols, "tp-030", "2026-11-13T00:00:00+00:00")


def test_backup_files_manifest_and_sums_agree():
    files = _files()
    assert sorted(files) == ["SHA256SUMS", "manifest.json", "tables/tripplanner.outbox.jsonl",
                             "tables/tripplanner.sessions.jsonl", "tables/tripplanner.trip_drafts.jsonl",
                             "tables/tripplanner.trip_events.jsonl"]
    assert files["tables/tripplanner.trip_events.jsonl"] == b'{"id": 1}\n{"id": 2}\n'
    assert files["tables/tripplanner.outbox.jsonl"] == b""
    manifest = json.loads(files["manifest.json"])
    assert manifest["format"] == "tp-tenant-backup/1" and manifest["restore_order"][0] == "tripplanner.sessions"
    assert {k: v["rows"] for k, v in manifest["tables"].items()} == {
        "tripplanner.sessions": 1, "tripplanner.trip_drafts": 0, "tripplanner.trip_events": 2, "tripplanner.outbox": 0}
    sums = tenant_backup.parse_sums(files["SHA256SUMS"])
    assert set(sums) == set(files) - {"SHA256SUMS"}
    assert all(sums[k] == tenant_backup.sha256(v) for k, v in files.items() if k != "SHA256SUMS")
    assert sorted(manifest["excluded"]) == ["tripplanner.ai_spend_month", "tripplanner.customers",
                                            "tripplanner.traveller_accounts"]


def test_backup_payload_and_bucket():
    with pytest.raises(PermanentError):
        tenant_backup.validate_payload({"tenant_id": TENANT, "rows": 5})
    assert tenant_store.prefix(TENANT, "bk-1") == f"exports/tenants/{TENANT}/tripplanner/bk-1/"
    with pytest.raises(tenant_store.BucketNotConfigured):
        tenant_store.bucket_name({})
    assert tenant_store.bucket_name({"TENANT_EXPORT_BUCKET": " gold-bucket "}) == "gold-bucket"


def test_drill_file_checks_catch_a_changed_file(tmp_path):
    store = tenant_store.DirStore(str(tmp_path))
    files = _files()
    prefix = tenant_store.prefix(TENANT, "bk-1")
    for k, v in files.items():
        asyncio.run(store.put_bytes(prefix + k, v, "x"))
    result = {"sha256sums_sha256": tenant_backup.sha256(files["SHA256SUMS"])}
    checks, manifest, lines = asyncio.run(tenant_restore_drill.check_files(store, TENANT, "bk-1", result))
    assert checks["sha256sums_matches_job"] and checks["manifest_matches_sums"] and all(checks["files"].values())
    assert lines["trip_events"] == ['{"id": 1}', '{"id": 2}']
    asyncio.run(store.put_bytes(prefix + "tables/tripplanner.trip_events.jsonl", b'{"id": 1}\n', "x"))
    checks, _, _ = asyncio.run(tenant_restore_drill.check_files(store, TENANT, "bk-1", result))
    assert checks["files"]["trip_events"] is False
    with pytest.raises(PermanentError, match="gone"):
        asyncio.run(tenant_restore_drill.check_files(store, TENANT, "bk-2", None))


def test_drill_pass_rule():
    checks = {"sha256sums_matches_job": None, "manifest_matches_sums": True, "manifest_tenant_matches": True,
              "files": {"sessions": True}}
    ok = {"sessions": {"manifest_rows": 2, "restored_rows": 2, "digest_ok": True, "live_rows": 0}}
    assert tenant_restore_drill.passed(checks, ok)              # live rows are reported, not judged
    assert not tenant_restore_drill.passed(checks, {"sessions": {**ok["sessions"], "restored_rows": 1}})
    assert not tenant_restore_drill.passed(checks, {"sessions": {**ok["sessions"], "digest_ok": False}})
    assert not tenant_restore_drill.passed({**checks, "sha256sums_matches_job": False}, ok)
    assert not tenant_restore_drill.passed(checks, {})
    with pytest.raises(PermanentError):
        tenant_restore_drill.validate_payload({"tenant_id": TENANT, "backup_id": "latest"})
