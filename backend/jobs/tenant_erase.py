"""AA-1093 lane L4 — job kind ``tp_tenant_erase``: the TripPlanner step of the tenant offboarding saga, contract
tenant.erasure.v1 (root docs/contracts/tenant.erasure.v1.openapi.yaml; root plan
docs/audits/2026-11-13-S279-session/aa1093-plan.md, Part 2; decisions D4, D8).

TripPlanner deletes every row of an offboarded tenant (``tripplanner.sessions``, ``trip_drafts``, ``trip_events``,
``outbox``, and the ``tripplanner.customers`` rows only its sessions link) and merges its AI spend rows
(``tripplanner.ai_spend_month`` scope ``tenant:<id>``) into the app scope. The work is one database function,
``tripplanner.erase_tenant(tenant, round, dry_run, expect_plan)`` (tp-031): it computes the plan (contract table
entries ``{table, action, rows, digest}``, never a row value) and its hash (``'tenant.erasure.v1|<tenant>|<round>'`` and
the sorted ``'<table>|<action>|<rows>|<digest>'`` lines), and on an apply recomputes it under EXCLUSIVE locks and
refuses a hash other than the approved one (``plan_changed``). This module decides whether a request may run, calls
the function under the jobs Lambda's system scope and appends one report per run to the append-only
``tripplanner.tenant_erasure_log`` (contract ErasureReportRow), which CIS reads through
``tripplanner.v_tenant_erasure_report_v1`` and keeps the newest of each (app, round, mode).

Modes: ``dry_run`` (the plan, nothing deleted), ``apply`` (the approved plan hash of this round), ``verify`` (a plan
after the apply, every delete / merge entry must be 0, plus a census of the ``pii:``-tagged columns, DM-6.1, for the
tenant: ``pii_rows_found`` must be 0; a ``pii:`` column the census neither clears nor keeps by rule fails it, so a new
column is never silently left out).

Reports: status ``ok`` | ``refused`` (a guard or a changed plan stopped it, nothing erased) | ``failed`` (an error,
the transaction rolled back; ``census_not_zero``, ``census_incomplete``) with a contract error code, never a message.
TripPlanner has no blocker (status ``blocked`` is never written).

Payload:

* ``{"trigger": "schedule"}`` (the daily tick): read ``catalog.v_tenant_erasure_v1`` (contract ErasureRequestRow,
  written by the CIS saga) ``WHERE app = 'tripplanner'`` when the view exists with every contract column,
  feature-detected; the newest ``updated_at`` row of each request; act on a step ``active`` past its ``not_before`` in
  phase ``dry_run``, ``apply`` (only with an approved hash) or ``verify``, once per (request, round, phase): a request
  that already has a report of that round and mode is not run again (a refusal or a failure is a report, CIS decides
  the next round). No view: ``no_feed``; a view without the contract columns: ``feed_shape_unknown`` (attention).
* ``{"tenant_id" | "tenant_slug", "mode", "expect_plan", "request_id", "round"}`` (an operator's ``enqueue``, a drill;
  DRY RUN by default): ``request_id`` defaults to a UUIDv5 of the tenant (so a drill's DRY RUN and apply share it),
  ``round`` to 1; an apply needs ``expect_plan`` (the DRY RUN's ``plan_hash``); markers from
  :func:`backend.jobs.tenant_target.markers`. A ``request_id`` that names a CIS request (a row of the request view)
  is refused before anything runs (PermanentError, no report): an operator report never lands in a CIS request.

Refusals (a report of status ``refused``; nothing is deleted), in this order:

* ``aa_hosted_refused``: the AA-hosted tenant (``config.is_aa_hosted``: ``DEMO_TENANT_ID`` or the bare path's
  ``DEFAULT_TENANT_ID``, the public planner's rows) is never erased;
* ``pilot_refused``: the request (or CIS) marks the tenant as the pilot (D8), in every mode;
* ``not_due``: the step's ``not_before`` has not passed;
* apply only: ``apply_disabled`` unless the raw environment has ``TENANT_ERASE_APPLY_ENABLED=true`` (Terraform,
  default off); ``tenant_markers_unknown`` (operator run, no CIS view says pilot / synthetic); ``not_synthetic``
  outside prod for a tenant not marked synthetic (D8); ``approval_missing`` without a 64-hex approved hash;
  ``approval_expired`` when the CIS approval has expired; ``dry_run_missing`` when no ok DRY RUN report of the
  same request, round, tenant and source carries exactly that hash (an apply runs only a plan this app computed and
  reported); ``plan_changed`` from the function.

An apply already reported ``ok`` for the request, round and source is not run again (a new round runs its own
plan). A failed report or a refused apply asks for attention (the daily tick then reports an error). Results hold
ids, codes, counts and hashes only (DM-6.3).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Optional

import asyncpg

from backend import config
from backend.jobs import tenant_target
from backend.jobs.queue import Job
from backend.jobs.runner import PermanentError
from backend.shared import observability, tenant

KIND = "tp_tenant_erase"
MAX_ATTEMPTS = 1          # an apply keeps no checkpoint of its own: one attempt, a refusal or failure is a report
APPLY_FLAG = "TENANT_ERASE_APPLY_ENABLED"
PROD_ENVIRONMENTS = frozenset({"prod", "production"})
CONTRACT = "tenant.erasure.v1"
APP = "tripplanner"
MODES = ("dry_run", "apply", "verify")
PAYLOAD_KEYS = frozenset({"trigger", "tenant_id", "tenant_slug", "mode", "expect_plan", "request_id", "round"})
TRIGGERS = ("schedule", "manual")
PLAN_RE = re.compile(r"^[0-9a-f]{64}$")
# Namespace of the request id of an operator run (one per tenant): its DRY RUN and its apply share the request.
OPERATOR_NAMESPACE = uuid.UUID("3d7e8c1a-5b2f-5e4a-9c61-7a0f93a1b2c4")
# The erase runs on tables the assembly Lambda writes: never wait long for their locks, never run long. Technical
# constants (TripPlanner has no admin-editable settings yet; follow-up in the implementation notes).
LOCK_TIMEOUT = "5s"
STATEMENT_TIMEOUT = "60s"
# Contract ErasureRequestRow: a view without one of these columns is not read (feed_shape_unknown).
FEED_COLUMNS = ("request_id", "tenant_id", "phase", "round", "opened_at", "approval_expires_at", "is_pilot",
                "is_synthetic", "app", "step_status", "not_before", "approved_plan_hash", "updated_at")

Environ = Mapping[str, str]

_REQUEST_COLUMNS = ("request_id::text AS request_id, tenant_id::text AS tenant_id, phase, round, step_status, "
                    "not_before, approved_plan_hash, approval_expires_at, is_pilot, is_synthetic")
REQUESTS_SQL = f"""
    SELECT * FROM (
        SELECT DISTINCT ON (request_id) {_REQUEST_COLUMNS}
          FROM {tenant_target.FEED_VIEW} WHERE app = '{APP}' ORDER BY request_id, updated_at DESC
    ) r WHERE step_status = 'active' AND phase IN ('dry_run', 'apply', 'verify') AND not_before <= now()
    ORDER BY request_id
"""  # noqa: S608 — fixed names
ERASE_SQL = "SELECT tripplanner.erase_tenant($1::uuid, $2::integer, $3, $4)::text AS result"
INSERT_REPORT_SQL = """
    INSERT INTO tripplanner.tenant_erasure_log
        (request_id, tenant_id, round, mode, status, error_code, plan_hash, tables, blockers, census, source, job_id)
    VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8::jsonb, $9::jsonb, $10::jsonb, $11, $12::uuid)
    RETURNING report_id::text AS report_id, reported_at
"""
REPORTED_SQL = """
    SELECT DISTINCT request_id::text AS request_id, round, mode FROM tripplanner.tenant_erasure_log
     WHERE request_id = ANY($1::uuid[]) AND source = 'cis'
"""
# The DRY RUN an apply runs must be of the same source: a CIS apply matches only a CIS DRY RUN, an operator apply only
# an operator one (and never a tp030 row).
DRY_RUN_OK_SQL = """
    SELECT report_id::text AS report_id FROM tripplanner.tenant_erasure_log
     WHERE request_id = $1::uuid AND round = $2 AND tenant_id = $3::uuid AND mode = 'dry_run' AND status = 'ok'
       AND plan_hash = $4 AND source = $5
     ORDER BY reported_at DESC LIMIT 1
"""
# An ok apply of the same request, round and source: a new round (leftover rows after a failed verification) runs
# its own approved plan and writes its own apply report.
APPLIED_SQL = """
    SELECT report_id::text AS report_id, round, plan_hash FROM tripplanner.tenant_erasure_log
     WHERE request_id = $1::uuid AND round = $2 AND source = $3 AND mode = 'apply' AND status = 'ok'
     ORDER BY reported_at DESC LIMIT 1
"""
# Operator runs never take the id of a CIS request (they would report into the saga outside its phase and approval).
CIS_REQUEST_SQL = f"SELECT 1 FROM {tenant_target.FEED_VIEW} WHERE request_id = $1::uuid LIMIT 1"  # noqa: S608

# ── The census (verify) ──────────────────────────────────────────────────────────────────────────────────────────
_CUSTOMERS = ("id IN (SELECT s.customer_id FROM tripplanner.sessions s WHERE s.tenant_id = $1::uuid) AND NOT EXISTS "
              "(SELECT 1 FROM tripplanner.sessions o WHERE o.customer_id = customers.id AND o.tenant_id <> $1::uuid)")
# Per table: the tenant's rows, and per pii: column the erasure clears, the condition "still holds a value".
CENSUS: dict[str, tuple[str, dict[str, str]]] = {
    "tripplanner.trip_drafts": ("tenant_id = $1::uuid", {"itinerary": "itinerary IS NOT NULL",
                                                         "date_start": "date_start IS NOT NULL",
                                                         "date_end": "date_end IS NOT NULL"}),
    "tripplanner.trip_events": ("tenant_id = $1::uuid", {"payload": "payload IS NOT NULL"}),
    "tripplanner.outbox": ("tenant_id = $1::uuid", {"payload": "payload IS NOT NULL"}),
    "tripplanner.customers": (_CUSTOMERS, {"name": "name IS NOT NULL", "phone": "phone IS NOT NULL",
                                           "email": "email IS NOT NULL"}),
    "tripplanner.sessions": ("tenant_id = $1::uuid", {}),
    "tripplanner.ai_spend_month": ("scope = 'tenant:' || $1::text", {}),
}
# pii: columns of the planned tables that stay by rule, with the rule. Never checked by the census.
CENSUS_KEPT: dict[str, str] = {
    "tripplanner.traveller_accounts.iss": "global sign-in account outside the tenant scope (a traveller's own "
                                          "erasure is the DSAR runbook)",
    "tripplanner.traveller_accounts.sub": "global sign-in account outside the tenant scope (a traveller's own "
                                          "erasure is the DSAR runbook)",
}
CENSUS_TABLES = sorted({*CENSUS, "tripplanner.traveller_accounts"})
PII_COLUMNS_SQL = """
    SELECT n.nspname || '.' || c.relname || '.' || a.attname AS col
      FROM pg_attribute a
      JOIN pg_class c ON c.oid = a.attrelid
      JOIN pg_namespace n ON n.oid = c.relnamespace
      JOIN pg_description d ON d.objoid = c.oid AND d.objsubid = a.attnum AND d.classoid = 'pg_class'::regclass
     WHERE n.nspname || '.' || c.relname = ANY($1::text[]) AND a.attnum > 0 AND NOT a.attisdropped
       AND d.description LIKE 'pii:%'
     ORDER BY 1
"""


def census_sql(table: str) -> Optional[str]:
    where, columns = CENSUS[table]
    if not columns:
        return None
    left = " OR ".join(f"({p})" for p in columns.values())
    return f"SELECT count(*)::int AS n FROM {table} WHERE {where} AND ({left})"  # noqa: S608 — constant census SQL


# ── Requests and refusals (pure) ─────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Request:
    """One erasure request as TripPlanner acts on it (a ``catalog.v_tenant_erasure_v1`` row, or an operator run)."""

    request_id: str
    tenant_id: str
    round: int
    phase: str
    source: str = "cis"
    step_status: str = "active"
    not_before: Optional[datetime] = None
    approved_plan_hash: Optional[str] = None
    approval_expires_at: Optional[datetime] = None
    is_pilot: Optional[bool] = None             # None: no CIS source answered (an operator run)
    is_synthetic: Optional[bool] = None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> Request:
        return cls(request_id=tenant.canonical(row["request_id"]), tenant_id=tenant.canonical(row["tenant_id"]),
                   round=int(row["round"]), phase=row["phase"], step_status=row["step_status"],
                   not_before=row["not_before"], approved_plan_hash=row["approved_plan_hash"],
                   approval_expires_at=row["approval_expires_at"], is_pilot=row["is_pilot"] is True,
                   is_synthetic=row["is_synthetic"] is True)


def operator_request_id(tenant_id: str) -> str:
    return str(uuid.uuid5(OPERATOR_NAMESPACE, f"tripplanner-tenant-erase:{tenant_id}"))


def validate_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise PermanentError("the payload must be an object")
    unknown = set(payload) - PAYLOAD_KEYS
    if unknown:
        raise PermanentError(f"unknown payload keys (allowed: {sorted(PAYLOAD_KEYS)})")
    trigger = payload.get("trigger", "manual")
    if trigger not in TRIGGERS:
        raise PermanentError(f"trigger must be one of {list(TRIGGERS)}")
    if trigger == "schedule":
        if set(payload) - {"trigger"}:
            raise PermanentError("a scheduled run takes no other key (it reads the CIS request view)")
        return payload
    _validate_manual(payload)
    return payload


def _validate_manual(payload: dict[str, Any]) -> None:
    if payload.get("mode", "dry_run") not in MODES:
        raise PermanentError(f"mode must be one of {list(MODES)}")
    expect = payload.get("expect_plan")
    if expect is not None and (not isinstance(expect, str) or not PLAN_RE.match(expect)):
        raise PermanentError("expect_plan must be the 64 hex characters of a DRY RUN plan_hash")
    round_ = payload.get("round", 1)
    if isinstance(round_, bool) or not isinstance(round_, int) or round_ < 1:
        raise PermanentError("round must be an integer from 1")
    if payload.get("request_id") is not None:
        try:
            tenant.canonical(payload["request_id"])
        except tenant.InvalidTenant:
            raise PermanentError("request_id must be a uuid") from None


def apply_enabled(environ: Environ) -> bool:
    return (environ.get(APPLY_FLAG) or "").strip().lower() == "true"


def is_prod(environ: Environ) -> bool:
    return (environ.get("ENVIRONMENT") or "").strip().lower() in PROD_ENVIRONMENTS


def refusal(req: Request, mode: str, environ: Environ, now: datetime) -> Optional[str]:
    """The contract error code of a refused run, or None. Pure; the order is the module docstring's."""
    if config.is_aa_hosted(req.tenant_id):
        return "aa_hosted_refused"
    if req.is_pilot:
        return "pilot_refused"
    if req.not_before is not None and req.not_before > now:
        return "not_due"
    if mode != "apply":
        return None
    return _apply_refusal(req, environ, now)


def _apply_refusal(req: Request, environ: Environ, now: datetime) -> Optional[str]:
    if not apply_enabled(environ):
        return "apply_disabled"
    if req.is_pilot is None or req.is_synthetic is None:
        return "tenant_markers_unknown"
    if not is_prod(environ) and not req.is_synthetic:
        return "not_synthetic"
    if not isinstance(req.approved_plan_hash, str) or not PLAN_RE.match(req.approved_plan_hash):
        return "approval_missing"
    if req.source == "cis" and (req.approval_expires_at is None or req.approval_expires_at <= now):
        return "approval_expired"
    return None


def plan_hash(tenant_id: str, round_: int, tables: list[dict[str, Any]]) -> str:
    """Contract: sha256 hex of 'tenant.erasure.v1|<tenant>|<round>' and the sorted table lines, joined by newlines
    (the same formula as tp-031's function and Booking's plan_hash)."""
    lines = sorted(f"{t['table']}|{t['action']}|{t['rows']}|{t['digest'] or ''}" for t in tables)
    return hashlib.sha256("\n".join([f"{CONTRACT}|{tenant_id}|{round_}", *lines]).encode()).hexdigest()


def verify_verdict(tables: list[dict[str, Any]], census_: dict[str, int],
                   unlisted: list[str]) -> tuple[str, Optional[str], list[dict[str, Any]]]:
    """(status, error_code, blockers) of a verification: every delete / merge entry 0 and no pii row left."""
    if unlisted:
        return "failed", "census_incomplete", [{"code": "pii_column_unlisted", "count": len(unlisted)}]
    left = sum(t["rows"] for t in tables if t["action"] != "keep")
    if left or census_["pii_rows_found"]:
        return "failed", "census_not_zero", []
    return "ok", None, []


def needs_attention(entry: dict[str, Any]) -> bool:
    return entry.get("status") == "failed" or (entry.get("status") == "refused" and entry.get("mode") == "apply")


def schedule_key(day: date) -> str:
    return f"{KIND}:schedule:{day.isoformat()}"


# ── One run of one request ───────────────────────────────────────────────────────────────────────────────────────


async def census(conn: Any, tenant_id: str) -> tuple[dict[str, int], list[str]]:
    """Verify: (contract Census, the pii: columns neither cleared nor kept by rule). A clean erasure is ({.., 0}, [])."""
    tagged = [r["col"] for r in await conn.fetch(PII_COLUMNS_SQL, CENSUS_TABLES)]
    cleared = {f"{t}.{c}" for t, (_, cols) in CENSUS.items() for c in cols}
    unlisted = [c for c in tagged if c not in cleared and c not in CENSUS_KEPT]
    found = 0
    for table in CENSUS:
        sql = census_sql(table)
        if sql is not None:
            found += int(await conn.fetchval(sql, tenant_id) or 0)
    return {"pii_columns_checked": len([c for c in tagged if c in cleared]), "pii_rows_found": found}, unlisted


async def write_report(conn: Any, req: Request, mode: str, status: str, *, error_code: Optional[str] = None,
                       plan_hash_: Optional[str] = None, tables: Optional[list[dict[str, Any]]] = None,
                       blockers: Optional[list[dict[str, Any]]] = None, census_: Optional[dict[str, int]] = None,
                       job_id: Optional[str] = None) -> dict[str, Any]:
    """Append one report (contract ErasureReport) and return it."""
    row = await conn.fetchrow(INSERT_REPORT_SQL, req.request_id, req.tenant_id, req.round, mode, status, error_code,
                              plan_hash_, json.dumps(tables or []), json.dumps(blockers or []),
                              json.dumps(census_) if census_ is not None else None, req.source, job_id)
    reported = row["reported_at"]
    return {"report_id": row["report_id"], "request_id": req.request_id, "tenant_id": req.tenant_id, "app": APP,
            "round": req.round, "mode": mode, "status": status, "error_code": error_code, "plan_hash": plan_hash_,
            "tables": tables or [], "blockers": blockers or [], "census": census_, "job_id": job_id,
            "reported_at": reported.isoformat() if isinstance(reported, datetime) else str(reported)}


async def _plan(conn: Any, req: Request, *, expect: Optional[str] = None) -> dict[str, Any]:
    """Call the function (DRY RUN when ``expect`` is None, else the apply) in the caller's transaction; the hash it
    returns must be the contract formula recomputed here."""
    result = json.loads(await conn.fetchval(ERASE_SQL, req.tenant_id, req.round, expect is None, expect))
    if result.get("plan_hash") != plan_hash(req.tenant_id, req.round, result.get("tables") or []):
        raise RuntimeError("erase_tenant returned a plan hash that is not the contract formula")
    return result


async def _apply(conn: Any, req: Request, job_id: Optional[str]) -> dict[str, Any]:
    if await conn.fetchval(DRY_RUN_OK_SQL, req.request_id, req.round, req.tenant_id, req.approved_plan_hash,
                           req.source) is None:
        return await write_report(conn, req, "apply", "refused", error_code="dry_run_missing", job_id=job_id)
    async with conn.transaction():
        await conn.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
        await conn.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'")
        result = await _plan(conn, req, expect=req.approved_plan_hash)
        if result["status"] == "ok":
            return await write_report(conn, req, "apply", "ok", plan_hash_=result["plan_hash"],
                                      tables=result["tables"], job_id=job_id)
    return await write_report(conn, req, "apply", "refused", error_code=result.get("error_code") or "plan_changed",
                              plan_hash_=result["plan_hash"], tables=result["tables"], job_id=job_id)


async def _dry_run(conn: Any, req: Request, job_id: Optional[str]) -> dict[str, Any]:
    async with conn.transaction():
        await conn.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'")
        result = await _plan(conn, req)
    return await write_report(conn, req, "dry_run", "ok", plan_hash_=result["plan_hash"], tables=result["tables"],
                              job_id=job_id)


async def _verify(conn: Any, req: Request, job_id: Optional[str]) -> dict[str, Any]:
    async with conn.transaction():
        await conn.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'")
        result = await _plan(conn, req)
        found, unlisted = await census(conn, req.tenant_id)
    status, code, blockers = verify_verdict(result["tables"], found, unlisted)
    if unlisted:
        observability.emit("tenant_erase_census_unlisted", columns=",".join(unlisted))
    return await write_report(conn, req, "verify", status, error_code=code, tables=result["tables"],
                              blockers=blockers, census_=found, job_id=job_id)


async def run_mode(conn: Any, req: Request, mode: str, *, job_id: Optional[str], environ: Environ,
                   now: Optional[datetime] = None) -> dict[str, Any]:
    """Run one mode for one request on ``conn`` (held under the system scope) and append its report: every outcome
    is a report, never a silent skip (an apply already reported ok for this request, round and source excepted).
    """
    now = now or datetime.now(timezone.utc)
    code = refusal(req, mode, environ, now)
    if code:
        return await write_report(conn, req, mode, "refused", error_code=code, job_id=job_id)
    if mode == "apply":
        done = await conn.fetchrow(APPLIED_SQL, req.request_id, req.round, req.source)
        if done is not None:
            return {"request_id": req.request_id, "tenant_id": req.tenant_id, "mode": mode, "status": "ok",
                    "error_code": None, "plan_hash": done["plan_hash"], "report_id": done["report_id"],
                    "already_applied": True}
    try:
        return await {"apply": _apply, "dry_run": _dry_run, "verify": _verify}[mode](conn, req, job_id)
    except (asyncpg.PostgresError, RuntimeError) as exc:
        observability.emit("tenant_erase_failed", request_id=req.request_id, mode=mode, error=type(exc).__name__)
        return await write_report(conn, req, mode, "failed", error_code="internal_error", job_id=job_id)


# ── The feed of CIS requests and the operator run ────────────────────────────────────────────────────────────────


async def _feed(conn: Any) -> tuple[str, list[Request]]:
    """('no_feed' | 'feed_shape_unknown' | 'feed', the requests to act on)."""
    cols = await tenant_target.columns(conn, tenant_target.FEED_VIEW)
    if not cols:
        return "no_feed", []
    if not all(c in cols for c in FEED_COLUMNS):
        return "feed_shape_unknown", []
    return "feed", [Request.from_row(r) for r in await conn.fetch(REQUESTS_SQL)]


async def _run_feed(conn: Any, job: Job, env: Environ) -> dict[str, Any]:
    state, requests = await _feed(conn)
    if state != "feed":
        observability.emit("tenant_erase_feed", state=state)
        return {"mode": state, "trigger": "schedule", "requests": [], "attention": state == "feed_shape_unknown"}
    reported = {(r["request_id"], r["round"], r["mode"])
                for r in await conn.fetch(REPORTED_SQL, [r.request_id for r in requests])}
    out = []
    for req in requests:
        if (req.request_id, req.round, req.phase) in reported:
            continue
        if req.phase == "apply" and not PLAN_RE.match(req.approved_plan_hash or ""):
            continue                                  # CIS sets the approved hash with the apply phase
        out.append(await run_mode(conn, req, req.phase, job_id=job.id, environ=env))
    return {"mode": "feed", "trigger": "schedule", "requests": out, "attention": any(needs_attention(e) for e in out)}


async def _refuse_cis_request_id(conn: Any, request_id: str) -> None:
    """An operator run with the id of a CIS request is refused before anything runs (PermanentError, no report)."""
    if "request_id" not in await tenant_target.columns(conn, tenant_target.FEED_VIEW):
        return                                        # no request view yet: no CIS request id exists to take
    if await conn.fetchval(CIS_REQUEST_SQL, request_id) is not None:
        raise PermanentError("request_id belongs to a CIS erasure request: an operator run uses its own id "
                             "(leave request_id out for the tenant's drill id)")


async def _run_operator(conn: Any, payload: dict[str, Any], job: Job, env: Environ) -> dict[str, Any]:
    tenant_id = await tenant_target.resolve(conn, payload)
    mode = payload.get("mode", "dry_run")
    request_id = (tenant.canonical(payload["request_id"]) if payload.get("request_id")
                  else operator_request_id(tenant_id))
    await _refuse_cis_request_id(conn, request_id)
    markers = await tenant_target.markers(conn, tenant_id)
    req = Request(
        request_id=request_id,
        tenant_id=tenant_id, round=int(payload.get("round", 1)), phase=mode, source="operator",
        approved_plan_hash=payload.get("expect_plan"),
        is_pilot=markers.is_pilot if markers else None, is_synthetic=markers.is_synthetic if markers else None)
    entry = await run_mode(conn, req, mode, job_id=job.id, environ=env)
    return {"mode": mode, "trigger": "manual", "requests": [entry], "attention": needs_attention(entry)}


async def run(pool: Any, job: Job, *, environ: Optional[Environ] = None) -> dict[str, Any]:
    payload = validate_payload(job.payload)
    env = os.environ if environ is None else environ
    async with pool.acquire() as conn, tenant.system_scope(conn, op=KIND):
        if payload.get("trigger", "manual") == "schedule":
            result = await _run_feed(conn, job, env)
        else:
            result = await _run_operator(conn, payload, job, env)
    for e in result["requests"]:
        observability.emit("tenant_erase", request_id=e["request_id"], tenant_id=e["tenant_id"], mode=e["mode"],
                           round=e.get("round"), status=e["status"], error_code=e.get("error_code"),
                           plan_hash=e.get("plan_hash"))
    return result
