"""AA-1093 lane L4 (offline): the tenant erasure, backup and restore drill rules of the jobs Lambda.

The database side (the plan hash, the apply, the round trip) is in test_aa1093_tenant_erase_pg.py and
migrations/tests/test_tp031_pg.py. Tenant ids here are invented. The reports are checked against the vendored contract
(tests/contracts/tenant.erasure.v1.json, generated from AA-Ecosys docs/contracts/tenant.erasure.v1.openapi.yaml).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import jsonschema
import pytest

from backend import config
from backend.jobs import handler, tenant_backup, tenant_erase, tenant_restore_drill, tenant_store
from backend.jobs.runner import PermanentError
from backend.jobs.tenant_erase import Request

TENANT = "a7a7a7a7-0000-4000-8000-0000000000e1"
REQUEST = "d1d1d1d1-0000-4000-8000-0000000000e5"
JOB = "f0f0f0f0-0000-4000-8000-0000000000a1"
PLAN = "ab" * 32
NOW = datetime(2026, 12, 31, 9, 0, tzinfo=timezone.utc)
DEV_ON = {"ENVIRONMENT": "dev", "TENANT_ERASE_APPLY_ENABLED": "true"}
PROD_ON = {"ENVIRONMENT": "prod", "TENANT_ERASE_APPLY_ENABLED": "true"}
CONTRACT = json.loads((Path(__file__).parent / "contracts" / "tenant.erasure.v1.json").read_text())


def _schema(name: str) -> dict:
    return {**CONTRACT, "$ref": f"#/$defs/{name}"}


def _req(**kw) -> Request:
    base = {"request_id": REQUEST, "tenant_id": TENANT, "round": 1, "phase": "apply", "source": "cis",
            "not_before": NOW - timedelta(days=1), "approved_plan_hash": PLAN,
            "approval_expires_at": NOW + timedelta(hours=2), "is_pilot": False, "is_synthetic": True}
    return Request(**{**base, **kw})


# ── refusals ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("mode,changes,environ,expected", [
    ("dry_run", {"is_pilot": None, "is_synthetic": None, "source": "operator"}, {}, None),   # no flag, no markers
    ("verify", {"is_synthetic": False}, {}, None),
    ("dry_run", {"tenant_id": config.DEMO_TENANT_ID}, DEV_ON, "aa_hosted_refused"),
    ("dry_run", {"is_pilot": True}, DEV_ON, "pilot_refused"),
    ("apply", {"is_pilot": True}, PROD_ON, "pilot_refused"),
    ("dry_run", {"not_before": NOW + timedelta(seconds=1)}, DEV_ON, "not_due"),
    ("apply", {}, {"ENVIRONMENT": "dev"}, "apply_disabled"),
    ("apply", {}, {"ENVIRONMENT": "dev", "TENANT_ERASE_APPLY_ENABLED": "yes"}, "apply_disabled"),
    ("apply", {"is_pilot": None, "is_synthetic": None, "source": "operator"}, DEV_ON, "tenant_markers_unknown"),
    ("apply", {"is_synthetic": False}, DEV_ON, "not_synthetic"),
    ("apply", {"is_synthetic": False}, {"TENANT_ERASE_APPLY_ENABLED": "true"}, "not_synthetic"),  # no ENVIRONMENT
    ("apply", {"approved_plan_hash": None}, DEV_ON, "approval_missing"),
    ("apply", {"approved_plan_hash": "ABC"}, DEV_ON, "approval_missing"),
    ("apply", {"approval_expires_at": NOW}, DEV_ON, "approval_expired"),
    ("apply", {"approval_expires_at": None}, DEV_ON, "approval_expired"),
    ("apply", {"approval_expires_at": None, "source": "operator"}, DEV_ON, None),   # an operator drill has no expiry
    ("apply", {}, DEV_ON, None),
    ("apply", {"is_synthetic": False}, PROD_ON, None),                               # prod: any tenant but the pilot
])
def test_refusal_order_and_codes(mode, changes, environ, expected):
    assert tenant_erase.refusal(_req(**changes), mode, environ, NOW) == expected


def test_the_bare_path_tenant_is_refused_like_the_demo_tenant(monkeypatch):
    """TP_DEFAULT_TENANT_ID (config.DEFAULT_TENANT_ID) is AA-hosted too (config.is_aa_hosted): never erased."""
    bare = "e5e5e5e5-0000-4000-8000-0000000000f1"
    assert tenant_erase.refusal(_req(tenant_id=bare), "dry_run", DEV_ON, NOW) is None
    monkeypatch.setattr(config, "DEFAULT_TENANT_ID", bare)
    for mode in ("dry_run", "apply", "verify"):
        assert tenant_erase.refusal(_req(tenant_id=bare), mode, PROD_ON, NOW) == "aa_hosted_refused"


@pytest.mark.parametrize("payload,message", [
    ([], "must be an object"),
    ({"tenant_id": TENANT, "force": True}, "unknown payload keys"),
    ({"trigger": "cron"}, "trigger must be one of"),
    ({"trigger": "schedule", "tenant_id": TENANT}, "takes no other key"),
    ({"tenant_id": TENANT, "mode": "delete"}, "mode must be one of"),
    ({"tenant_id": TENANT, "mode": "apply", "expect_plan": "short"}, "expect_plan must be"),
    ({"tenant_id": TENANT, "request_id": "not-a-uuid"}, "request_id must be a uuid"),
    ({"tenant_id": TENANT, "round": 0}, "round must be an integer"),
    ({"tenant_id": TENANT, "round": "2"}, "round must be an integer"),
    ({"tenant_id": TENANT, "round": True}, "round must be an integer"),
])
def test_erase_payload_is_validated_without_echoing_values(payload, message):
    with pytest.raises(PermanentError) as e:
        tenant_erase.validate_payload(payload)
    assert message in str(e.value) and "not-a-uuid" not in str(e.value)


def test_the_operator_request_id_is_one_per_tenant():
    assert tenant_erase.operator_request_id(TENANT) == tenant_erase.operator_request_id(TENANT)
    assert tenant_erase.operator_request_id(TENANT) != tenant_erase.operator_request_id(REQUEST)


def test_plan_hash_is_the_contract_formula():
    tables = [{"table": "tripplanner.sessions", "action": "delete", "rows": 2, "digest": "0" * 32},
              {"table": "tripplanner.traveller_accounts", "action": "keep", "rows": 0, "digest": None}]
    text = (f"tenant.erasure.v1|{TENANT}|3\ntripplanner.sessions|delete|2|{'0' * 32}\n"
            "tripplanner.traveller_accounts|keep|0|")
    assert tenant_erase.plan_hash(TENANT, 3, tables) == hashlib.sha256(text.encode()).hexdigest()
    assert tenant_erase.plan_hash(TENANT, 3, tables[::-1]) == tenant_erase.plan_hash(TENANT, 3, tables)
    assert tenant_erase.plan_hash(TENANT, 4, tables) != tenant_erase.plan_hash(TENANT, 3, tables)


def test_verify_verdict():
    clean = [{"table": "tripplanner.sessions", "action": "delete", "rows": 0, "digest": "x"},
             {"table": "tripplanner.traveller_accounts", "action": "keep", "rows": 3, "digest": None}]
    zero = {"pii_columns_checked": 9, "pii_rows_found": 0}
    assert tenant_erase.verify_verdict(clean, zero, []) == ("ok", None, [])           # keep rows are not left-overs
    left = [*clean, {"table": "tripplanner.ai_spend_month", "action": "merge", "rows": 1, "digest": "y"}]
    assert tenant_erase.verify_verdict(left, zero, []) == ("failed", "census_not_zero", [])
    assert tenant_erase.verify_verdict(clean, {**zero, "pii_rows_found": 1}, []) == ("failed", "census_not_zero", [])
    assert tenant_erase.verify_verdict(clean, zero, ["tripplanner.x.y"]) == \
        ("failed", "census_incomplete", [{"code": "pii_column_unlisted", "count": 1}])


def test_attention_on_a_failure_or_a_refused_apply_only():
    assert tenant_erase.needs_attention({"mode": "apply", "status": "refused"})
    assert tenant_erase.needs_attention({"mode": "verify", "status": "failed"})
    assert tenant_erase.needs_attention({"mode": "dry_run", "status": "failed"})
    assert not tenant_erase.needs_attention({"mode": "dry_run", "status": "refused"})
    assert not tenant_erase.needs_attention({"mode": "apply", "status": "ok"})


def test_the_census_covers_the_pii_columns_of_the_planned_tables():
    cleared = {f"{t}.{c}" for t, (_, cols) in tenant_erase.CENSUS.items() for c in cols}
    assert cleared == {"tripplanner.trip_drafts.itinerary", "tripplanner.trip_drafts.date_start",
                       "tripplanner.trip_drafts.date_end", "tripplanner.trip_events.payload",
                       "tripplanner.outbox.payload", "tripplanner.customers.name", "tripplanner.customers.phone",
                       "tripplanner.customers.email"}
    assert not cleared & set(tenant_erase.CENSUS_KEPT)
    assert tenant_erase.census_sql("tripplanner.sessions") is None
    assert "tenant_id = $1::uuid" in tenant_erase.census_sql("tripplanner.outbox")


# ── one run, offline (a fake connection) ─────────────────────────────────────


class _Tx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Conn:
    """Records every statement; answers the report insert like the database (report id + time), the DRY RUN lookup
    with ``dry_run_ok`` and the function with ``plan``. ``calls_function`` False makes a call to it fail the test."""

    def __init__(self, *, dry_run_ok=None, applied=None, plan=None, calls_function=True):
        self.dry_run_ok, self.applied, self.plan, self.calls_function = dry_run_ok, applied, plan, calls_function
        self.cis_requests: set[str] = set()
        self.dry_run_args: tuple = ()
        self.inserts: list[tuple] = []
        self.calls: list[str] = []

    def transaction(self):
        return _Tx()

    async def fetchrow(self, sql, *args):
        self.calls.append(sql)
        if "INSERT INTO tripplanner.tenant_erasure_log" in sql:
            self.inserts.append(args)
            return {"report_id": f"0c9b8a7d-6e5f-4d3c-8b2a-1f0e9d8c7b{len(self.inserts):02d}", "reported_at": NOW}
        if "mode = 'apply' AND status = 'ok'" in sql:
            # Like the database: an ok apply of this request, round and source only.
            a = self.applied
            return a if a is not None and (a.get("round", 1), a.get("source", "cis")) == args[1:3] else None
        raise AssertionError(sql)

    async def fetchval(self, sql, *args):
        self.calls.append(sql)
        if "mode = 'dry_run' AND status = 'ok'" in sql:
            self.dry_run_args = args
            return self.dry_run_ok
        if "erase_tenant" in sql:
            assert self.calls_function, "erase_tenant must not run"
            return json.dumps(self.plan(*args))
        if sql == tenant_erase.CIS_REQUEST_SQL:
            return 1 if args[0] in self.cis_requests else None
        raise AssertionError(sql)

    async def execute(self, sql, *args):
        self.calls.append(sql)

    async def fetch(self, sql, *args):
        self.calls.append(sql)
        return []


def _function(status="ok", code=None, rows=2):
    def plan(tenant_id, round_, dry_run, expect):
        tables = [{"table": "tripplanner.sessions", "action": "delete", "rows": rows, "digest": "0" * 32},
                  {"table": "tripplanner.traveller_accounts", "action": "keep", "rows": 1, "digest": None}]
        return {"format": "tenant.erasure.v1", "status": status, "error_code": code, "tables": tables,
                "plan_hash": tenant_erase.plan_hash(tenant_id, round_, tables), "app_rows_added": 0}
    return plan


def _run(conn, req, mode, environ=DEV_ON):
    return asyncio.run(tenant_erase.run_mode(conn, req, mode, job_id=JOB, environ=environ, now=NOW))


def _valid(report):
    jsonschema.validate(report, _schema("ErasureReport"), format_checker=jsonschema.FormatChecker())


def test_a_refusal_is_a_contract_report_and_runs_nothing():
    conn = _Conn(calls_function=False)
    report = _run(conn, _req(is_synthetic=False), "apply")
    assert (report["status"], report["error_code"], report["tables"], report["plan_hash"]) == \
        ("refused", "not_synthetic", [], None)
    _valid(report)
    assert conn.inserts[0][3:6] == ("apply", "refused", "not_synthetic") and conn.inserts[0][10] == "cis"


def test_an_apply_without_an_ok_dry_run_of_that_hash_is_refused():
    conn = _Conn(dry_run_ok=None, calls_function=False)
    report = _run(conn, _req(), "apply")
    assert (report["status"], report["error_code"]) == ("refused", "dry_run_missing")
    lookup = [c for c in conn.calls if "mode = 'dry_run' AND status = 'ok'" in c]
    assert lookup and "round = $2" in lookup[0] and "source = $5" in lookup[0]
    assert conn.dry_run_args == (REQUEST, 1, TENANT, PLAN, "cis")     # a CIS apply matches a CIS DRY RUN only
    _valid(report)


def test_an_operator_apply_looks_for_an_operator_dry_run():
    conn = _Conn(dry_run_ok=None, calls_function=False)
    report = _run(conn, _req(source="operator"), "apply")
    assert (report["status"], report["error_code"]) == ("refused", "dry_run_missing")
    assert conn.dry_run_args[4] == "operator"


def test_an_apply_with_the_approved_plan_reports_ok_and_a_changed_one_plan_changed():
    req = _req(approved_plan_hash=_function()(TENANT, 1, False, None)["plan_hash"])
    ok = _run(_Conn(dry_run_ok="r1", plan=_function()), req, "apply")
    assert (ok["status"], ok["error_code"], ok["plan_hash"]) == ("ok", None, req.approved_plan_hash)
    _valid(ok)
    changed = _run(_Conn(dry_run_ok="r1", plan=_function(status="refused", code="plan_changed", rows=3)), req, "apply")
    assert (changed["status"], changed["error_code"]) == ("refused", "plan_changed")
    assert changed["plan_hash"] != req.approved_plan_hash and changed["tables"][0]["rows"] == 3
    _valid(changed)


def test_an_already_applied_request_is_not_run_again():
    conn = _Conn(applied={"report_id": "r9", "round": 1, "plan_hash": PLAN}, calls_function=False)
    out = _run(conn, _req(), "apply")
    assert out["already_applied"] and out["report_id"] == "r9" and conn.inserts == []
    lookup = [c for c in conn.calls if "mode = 'apply' AND status = 'ok'" in c]
    assert "round = $2" in lookup[0] and "source = $3" in lookup[0]


def test_a_new_round_applies_its_own_plan_after_an_ok_apply_of_an_earlier_round():
    # Round 1 applied ok, its verification found rows left, CIS opened round 2 and approved its DRY RUN: the round-2
    # apply runs and writes its own report (CIS waits for the newest of (app, round 2, apply)).
    req = _req(round=2, approved_plan_hash=_function()(TENANT, 2, False, None)["plan_hash"])
    conn = _Conn(applied={"report_id": "r9", "round": 1, "plan_hash": PLAN}, dry_run_ok="r2", plan=_function())
    out = _run(conn, req, "apply")
    assert "already_applied" not in out and (out["status"], out["round"]) == ("ok", 2)
    assert conn.inserts[0][2:5] == (2, "apply", "ok")
    _valid(out)


def test_an_operator_apply_is_not_skipped_by_a_cis_apply_of_the_same_request_id():
    conn = _Conn(applied={"report_id": "r9", "round": 1, "source": "cis", "plan_hash": PLAN}, dry_run_ok=None,
                 calls_function=False)
    out = _run(conn, _req(source="operator"), "apply")
    assert "already_applied" not in out and out["error_code"] == "dry_run_missing"


# ── operator runs never take a CIS request id ────────────────────────────────


def _operator(conn, payload, monkeypatch, *, feed_columns=("request_id", "tenant_id")):
    async def columns(_conn, relation):
        return list(feed_columns) if relation == tenant_erase.tenant_target.FEED_VIEW else []

    async def markers(_conn, tenant_id):
        return tenant_erase.tenant_target.Markers(False, True, tenant_erase.tenant_target.FEED_VIEW)

    monkeypatch.setattr(tenant_erase.tenant_target, "columns", columns)
    monkeypatch.setattr(tenant_erase.tenant_target, "markers", markers)

    class _Job:
        id = JOB

    return asyncio.run(tenant_erase._run_operator(conn, payload, _Job(), DEV_ON))


def test_an_operator_run_with_the_id_of_a_cis_request_is_refused_before_anything_runs(monkeypatch):
    conn = _Conn(calls_function=False)
    conn.cis_requests = {REQUEST}
    for mode in ("dry_run", "apply", "verify"):
        with pytest.raises(PermanentError, match="CIS erasure request"):
            _operator(conn, {"tenant_id": TENANT, "mode": mode, "request_id": REQUEST, "expect_plan": PLAN},
                      monkeypatch)
    assert conn.inserts == []


def test_an_operator_run_with_its_own_id_or_without_a_request_view_goes_on(monkeypatch):
    conn = _Conn(plan=_function())
    conn.cis_requests = {REQUEST}
    out = _operator(conn, {"tenant_id": TENANT}, monkeypatch)
    entry = out["requests"][0]
    assert entry["request_id"] == tenant_erase.operator_request_id(TENANT) and entry["status"] == "ok"
    assert conn.inserts[-1][10] == "operator"
    out = _operator(conn, {"tenant_id": TENANT, "request_id": REQUEST}, monkeypatch, feed_columns=())
    assert out["requests"][0]["request_id"] == REQUEST        # no request view yet: no CIS id exists to take


def test_a_dry_run_reports_the_plan_and_a_bad_hash_is_a_failure():
    report = _run(_Conn(plan=_function()), _req(phase="dry_run"), "dry_run")
    assert report["status"] == "ok" and report["plan_hash"] and len(report["tables"]) == 2
    _valid(report)

    def wrong(*args):
        return {**_function()(*args), "plan_hash": "f" * 64}

    report = _run(_Conn(plan=wrong), _req(phase="dry_run"), "dry_run")
    assert (report["status"], report["error_code"], report["plan_hash"]) == ("failed", "internal_error", None)
    _valid(report)


def test_reports_and_rows_validate_against_the_contract_and_its_invalid_shapes_do_not():
    report = _run(_Conn(plan=_function()), _req(phase="dry_run"), "dry_run")
    row = {k: report[k] for k in ("report_id", "request_id", "tenant_id", "app", "round", "mode", "status",
                                  "error_code", "plan_hash", "tables", "blockers", "census", "job_id", "reported_at")}
    jsonschema.validate(row, _schema("ErasureReportRow"), format_checker=jsonschema.FormatChecker())
    for bad in ({**row, "app": "crm"}, {**row, "plan_hash": "abc"}, {**row, "approved_by": "x"},
                {**row, "tables": [{"table": "tripplanner.sessions", "action": "merge_into_app", "rows": 1,
                                    "digest": None}]}):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(bad, _schema("ErasureReportRow"))


def test_a_request_row_of_the_contract_reads_into_a_request():
    row = {"request_id": REQUEST, "tenant_id": TENANT, "phase": "apply", "round": 2,
           "opened_at": "2026-12-01T08:00:00Z", "approval_expires_at": "2026-12-31T10:00:00Z", "is_pilot": False,
           "is_synthetic": True, "app": "tripplanner", "step_status": "active", "not_before": "2026-12-31T08:00:00Z",
           "approved_plan_hash": PLAN, "updated_at": "2026-12-31T10:00:00Z"}
    jsonschema.validate(row, _schema("ErasureRequestRow"))
    assert tuple(sorted(row)) == tuple(sorted(tenant_erase.FEED_COLUMNS))
    req = Request.from_row({**row, "not_before": NOW, "approval_expires_at": NOW})
    assert (req.round, req.phase, req.source, req.is_pilot, req.is_synthetic) == (2, "apply", "cis", False, True)


# ── handler wiring ───────────────────────────────────────────────────────────


def test_kinds_are_registered_and_the_erase_and_pilot_backup_run_daily_once():
    for k in (tenant_erase, tenant_backup, tenant_restore_drill):
        assert k.KIND in handler.HANDLERS and handler.MAX_ATTEMPTS[k.KIND] == k.MAX_ATTEMPTS
        assert k.KIND.startswith("tp_")
    assert set(handler.DAILY) - {"tp_retention_outbox_redact", "tp_retention_ai_rate_counter",
                                 "tp_lead_deliver"} == {tenant_erase.KIND, tenant_backup.KIND}
    payload, attempts, key = handler.DAILY[tenant_erase.KIND]
    assert payload == {"trigger": "schedule"} and attempts == 1
    assert key(date(2026, 11, 13)) == "tp_tenant_erase:schedule:2026-11-13"
    payload, attempts, key = handler.DAILY[tenant_backup.KIND]
    assert payload == {"trigger": "schedule"} and attempts == 2
    assert key(date(2026, 11, 13)) == "tp_tenant_backup:schedule:2026-11-13"
    assert tenant_restore_drill.KIND not in handler.DAILY
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
    return tenant_backup.build(TENANT, "bk-1", lines, cols, "2026-11-13T00:00:00+00:00")


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
    with pytest.raises(PermanentError, match="takes no other key"):
        tenant_backup.validate_payload({"trigger": "schedule", "tenant_id": TENANT})
    with pytest.raises(PermanentError, match="trigger must be one of"):
        tenant_backup.validate_payload({"trigger": "cron"})
    assert tenant_backup.validate_payload({"trigger": "schedule"}) == {"trigger": "schedule"}
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


def test_drill_finds_a_tenant_in_a_daily_pilot_backup_result():
    single = {"tenant_id": TENANT, "sha256sums_sha256": "1" * 64}
    daily = {"mode": "scheduled", "backups": [{"tenant_id": "b8b8b8b8-0000-4000-8000-0000000000e2"},
                                              {"tenant_id": TENANT, "sha256sums_sha256": "2" * 64}]}
    assert tenant_restore_drill.entry_of(single, TENANT) is single
    assert tenant_restore_drill.entry_of(daily, TENANT)["sha256sums_sha256"] == "2" * 64
    assert tenant_restore_drill.entry_of({"mode": "no_feed", "backups": []}, TENANT) is None


# ── S3 errors ────────────────────────────────────────────────────────────────


class _ClientError(Exception):
    """The shape of botocore's ClientError (``response["Error"]["Code"]``), without botocore."""

    def __init__(self, code):
        super().__init__(code)
        self.response = {"Error": {"Code": code, "Message": "x"}}


class _S3:
    def __init__(self, code):
        self.code = code

    def get_object(self, **kw):
        raise _ClientError(self.code)

    def put_object(self, **kw):
        raise _ClientError(self.code)


@pytest.mark.parametrize("code,expected", [
    ("NoSuchKey", tenant_store.ObjectMissing), ("404", tenant_store.ObjectMissing),
    ("NotFound", tenant_store.ObjectMissing),
    ("AccessDenied", tenant_store.AccessDenied), ("403", tenant_store.AccessDenied),
    ("SlowDown", _ClientError),                                         # anything else: raised as is (retryable)
])
def test_s3_get_maps_missing_and_denied_apart(code, expected):
    store = tenant_store.S3Store(bucket="b", client=_S3(code))
    with pytest.raises(expected):
        asyncio.run(store.get_bytes("exports/tenants/x/tripplanner/y/SHA256SUMS"))


def test_s3_put_access_denied_is_its_own_error():
    store = tenant_store.S3Store(bucket="b", client=_S3("AccessDenied"))
    with pytest.raises(tenant_store.AccessDenied):
        asyncio.run(store.put_bytes("k", b"", "text/plain"))
    assert tenant_store.denied(tenant_store.AccessDenied("k")).startswith("s3_access_denied")


def test_drill_fails_with_s3_access_denied_not_files_gone():
    store = tenant_store.S3Store(bucket="b", client=_S3("AccessDenied"))
    with pytest.raises(PermanentError, match="^s3_access_denied") as e:
        asyncio.run(tenant_restore_drill.check_files(store, TENANT, "bk-1", None))
    assert "gone" not in str(e.value) and TENANT not in str(e.value)
