"""AA-1093 lane L4 — job kind ``tp_tenant_erase``: the TripPlanner part of the tenant offboarding saga (root plan
docs/audits/2026-11-13-S279-session/aa1093-plan.md, Part 2; decisions D4, D8).

TripPlanner deletes every row of an offboarded tenant (``tripplanner.sessions``, ``trip_drafts``, ``trip_events``,
``outbox``) and merges its AI spend rows (``tripplanner.ai_spend_month`` scope ``tenant:<id>``) into the app scope.
The work is one database function, ``tripplanner.erase_tenant`` (tp-030): it computes the plan (per table: action,
row count, a fingerprint of the rows, never a row value) and its hash, and on an apply recomputes the hash under a
lock and refuses a hash other than the approved one (``plan_changed``). This module decides whether a request may
run, calls the function under the jobs Lambda's system scope and writes one row per (request, mode) to
``tripplanner.tenant_erasure_log``, which CIS reads through ``tripplanner.v_tenant_erasure_report_v1``.

Modes: ``dry_run`` (the plan, nothing deleted), ``apply`` (the approved plan hash is required), ``verify`` (a DRY RUN
after the apply: every delete / merge count must be 0, else ``residue``).

Payload:

* ``{"trigger": "schedule"}`` (the daily tick): read the requests of ``catalog.v_tenant_erasure_v1`` (contract
  tenant.erasure.v1, written by the CIS saga) when that view exists, feature-detected; until CIS ships it the run
  answers ``no_feed`` and does nothing. Columns read: ``request_id``, ``tenant_id``, ``mode`` (required);
  ``app`` (rows of another app are skipped), ``tripplanner_plan_hash`` (the approved hash of this app), ``is_pilot``,
  ``is_synthetic`` (optional). A request whose (request, mode) row is already planned / applied / verified, or refused
  for a reason no rerun changes (:data:`FINAL_REFUSALS`), is skipped.
* ``{"tenant_id" | "tenant_slug", "mode", "expect_plan", "request_id"}`` (an operator's ``enqueue`` for a drill):
  ``request_id`` defaults to the job id; an apply needs ``expect_plan`` (the DRY RUN's ``plan_hash``).

Refusals (the log row says ``refused`` with the reason; nothing is deleted), in this order:

* ``demo_tenant``: the AA-hosted tenant (``config.DEMO_TENANT_ID``, the public planner's rows) is never erased;
* ``pilot_tenant``: CIS marks the tenant as the pilot (D8), in every mode;
* apply only: ``apply_disabled`` unless the raw environment has ``TENANT_ERASE_APPLY_ENABLED=true`` (Terraform,
  default off); ``tenant_markers_unknown`` when no CIS view says whether the tenant is the pilot / synthetic;
  ``not_synthetic`` outside prod for a tenant CIS does not mark synthetic (D8: on Dev only synthetic tenants);
  ``no_approved_plan`` without a 64-hex plan hash; ``plan_changed`` from the function.

An apply that is already logged ``applied`` is not run again (the answer repeats the logged counts). A refused apply,
a residue or an unknown feed shape asks for attention (the daily tick then reports an error). Results hold ids,
counts and hashes only (DM-6.3).
"""
from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from datetime import date
from typing import Any, Optional

from backend import config
from backend.jobs import tenant_target
from backend.jobs.queue import Job
from backend.jobs.runner import PermanentError
from backend.jobs.tenant_target import Markers
from backend.shared import observability, tenant

KIND = "tp_tenant_erase"
MAX_ATTEMPTS = 1          # an apply keeps no checkpoint of its own: one attempt, a refusal is logged, not retried
APPLY_FLAG = "TENANT_ERASE_APPLY_ENABLED"
PROD_ENVIRONMENTS = frozenset({"prod", "production"})
MODES = ("dry_run", "apply", "verify")
DONE_STATUSES = ("planned", "applied", "verified")
# Refusals a later run of the same (request, mode) cannot change: the feed does not retry them (CIS needs a new request,
# or a new approval after plan_changed). Every other refusal (apply_disabled, tenant_markers_unknown, not_synthetic,
# no_approved_plan) is retried by the next daily run, since the flag, the markers or the approval may have changed.
FINAL_REFUSALS = ("demo_tenant", "pilot_tenant", "plan_changed")
PAYLOAD_KEYS = frozenset({"trigger", "tenant_id", "tenant_slug", "mode", "expect_plan", "request_id"})
TRIGGERS = ("schedule", "manual")
APP = "tripplanner"
FEED_REQUIRED = ("request_id", "tenant_id", "mode")
FEED_OPTIONAL = ("app", "tripplanner_plan_hash", "is_pilot", "is_synthetic")
PLAN_RE = re.compile(r"^[0-9a-f]{64}$")
# The erase runs on tables the assembly Lambda writes: never wait long for their locks, never run long.
LOCK_TIMEOUT = "5s"
STATEMENT_TIMEOUT = "60s"

Environ = Mapping[str, str]

ERASE_SQL = "SELECT tripplanner.erase_tenant($1::uuid, $2, $3)::text AS result"
LOG_READ_SQL = """
    SELECT tenant_id::text AS tenant_id, status, reason, plan_hash, counts::text AS counts
      FROM tripplanner.tenant_erasure_log WHERE request_id = $1::uuid AND mode = $2
"""
LOG_UPSERT_SQL = """
    INSERT INTO tripplanner.tenant_erasure_log (request_id, tenant_id, mode, plan_hash, counts, status, reason, job_id)
    VALUES ($1::uuid, $2::uuid, $3, $4, $5::jsonb, $6, $7, $8::uuid)
    ON CONFLICT (request_id, mode) DO UPDATE
       SET plan_hash = EXCLUDED.plan_hash, counts = EXCLUDED.counts, status = EXCLUDED.status,
           reason = EXCLUDED.reason, job_id = EXCLUDED.job_id, updated_at = now()
     WHERE tripplanner.tenant_erasure_log.status <> 'applied'
       AND tripplanner.tenant_erasure_log.tenant_id = EXCLUDED.tenant_id
"""
DONE_SQL = """
    SELECT request_id::text AS request_id, mode FROM tripplanner.tenant_erasure_log
     WHERE status = ANY($1::text[]) OR (status = 'refused' AND reason = ANY($2::text[]))
"""


# ── Payload and refusals (pure) ──────────────────────────────────────────────


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
    if payload.get("mode", "dry_run") not in MODES:
        raise PermanentError(f"mode must be one of {list(MODES)}")
    expect = payload.get("expect_plan")
    if expect is not None and (not isinstance(expect, str) or not PLAN_RE.match(expect)):
        raise PermanentError("expect_plan must be the 64 hex characters of a DRY RUN plan_hash")
    if payload.get("request_id") is not None:
        try:
            tenant.canonical(payload["request_id"])
        except tenant.InvalidTenant:
            raise PermanentError("request_id must be a uuid") from None
    return payload


def apply_enabled(environ: Environ) -> bool:
    return (environ.get(APPLY_FLAG) or "").strip().lower() == "true"


def is_prod(environ: Environ) -> bool:
    return (environ.get("ENVIRONMENT") or "").strip().lower() in PROD_ENVIRONMENTS


def refusal(mode: str, tenant_id: str, markers: Optional[Markers], expect_plan: Optional[str],
            environ: Environ) -> Optional[str]:
    """The reason a request may not run, or None. Pure; the order is the module docstring's."""
    if tenant_id == config.DEMO_TENANT_ID:
        return "demo_tenant"
    if markers is not None and markers.is_pilot:
        return "pilot_tenant"
    if mode != "apply":
        return None
    if not apply_enabled(environ):
        return "apply_disabled"
    if markers is None:
        return "tenant_markers_unknown"
    if not is_prod(environ) and not markers.is_synthetic:
        return "not_synthetic"
    if not isinstance(expect_plan, str) or not PLAN_RE.match(expect_plan):
        return "no_approved_plan"
    return None


def verdict(mode: str, result: dict[str, Any]) -> tuple[str, Optional[str]]:
    """(status, reason) of the log row from the function's answer."""
    if result.get("status") == "refused":
        return "refused", result.get("reason") or "refused"
    if mode == "apply":
        return "applied", None
    if mode == "verify":
        left = {k: v["rows"] for k, v in (result.get("tables") or {}).items()
                if v.get("action") in ("delete", "merge_into_app") and v.get("rows")}
        return ("verified", None) if not left else ("residue", "rows_left")
    return "planned", None


def needs_attention(entry: dict[str, Any]) -> bool:
    return (entry.get("status") == "residue"
            or (entry.get("status") == "refused" and entry.get("mode") == "apply"))


def schedule_key(day: date) -> str:
    return f"{KIND}:schedule:{day.isoformat()}"


# ── One request ──────────────────────────────────────────────────────────────


def _summary(request_id: str, tenant_id: str, mode: str, status: str, reason: Optional[str],
             plan_hash: Optional[str], counts: dict[str, Any], **extra: Any) -> dict[str, Any]:
    tables = counts.get("tables") or {}
    return {"request_id": request_id, "tenant_id": tenant_id, "mode": mode, "status": status, "reason": reason,
            "plan_hash": plan_hash, "rows": {k: v.get("rows") for k, v in tables.items()},
            "deleted": counts.get("deleted") or {}, **extra}


async def erase_one(conn: Any, *, request_id: str, tenant_id: str, mode: str, expect_plan: Optional[str],
                    markers: Optional[Markers], job_id: str, environ: Environ) -> dict[str, Any]:
    """Run one (request, mode) on ``conn``, which the caller holds under the system scope. Returns the summary."""
    existing = await conn.fetchrow(LOG_READ_SQL, request_id, mode)
    if existing is not None and existing["tenant_id"] != tenant_id:
        return _summary(request_id, tenant_id, mode, "refused", "request_tenant_mismatch", None, {}, logged=False)
    if existing is not None and existing["status"] == "applied":
        return _summary(request_id, tenant_id, mode, "applied", None, existing["plan_hash"],
                        json.loads(existing["counts"]), already_applied=True)

    reason = refusal(mode, tenant_id, markers, expect_plan, environ)
    if reason:
        await conn.execute(LOG_UPSERT_SQL, request_id, tenant_id, mode, None, "{}", "refused", reason, job_id)
        return _summary(request_id, tenant_id, mode, "refused", reason, None, {})

    async with conn.transaction():
        await conn.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
        await conn.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'")
        result = json.loads(await conn.fetchval(ERASE_SQL, tenant_id, mode != "apply",
                                                expect_plan if mode == "apply" else None))
        status, why = verdict(mode, result)
        counts = {"tables": result.get("tables") or {}, "deleted": result.get("deleted") or {},
                  "app_rows_added": result.get("app_rows_added") or 0}
        await conn.execute(LOG_UPSERT_SQL, request_id, tenant_id, mode, result.get("plan_hash"), json.dumps(counts),
                           status, why, job_id)
    return _summary(request_id, tenant_id, mode, status, why, result.get("plan_hash"), counts)


# ── The feed of CIS requests ─────────────────────────────────────────────────


async def _feed(conn: Any) -> tuple[str, list[dict[str, Any]]]:
    """('no_feed' | 'feed_shape_unknown' | 'feed', rows)."""
    cols = await tenant_target.columns(conn, tenant_target.FEED_VIEW)
    if not cols:
        return "no_feed", []
    if not all(c in cols for c in FEED_REQUIRED):
        return "feed_shape_unknown", []
    select = ["request_id::text AS request_id", "tenant_id::text AS tenant_id", "mode"]
    select += [c if c in cols else f"NULL AS {c}" for c in FEED_OPTIONAL]
    where = "mode = ANY($1::text[])" + (" AND app = $2" if "app" in cols else "")
    args: list[Any] = [list(MODES)] + ([APP] if "app" in cols else [])
    rows = await conn.fetch(f"SELECT {', '.join(select)} FROM {tenant_target.FEED_VIEW} WHERE {where} "
                            f"ORDER BY request_id, mode", *args)
    return "feed", [dict(r) for r in rows]


def _markers_of(row: dict[str, Any]) -> Optional[Markers]:
    if row.get("is_pilot") is None or row.get("is_synthetic") is None:
        return None
    return Markers(bool(row["is_pilot"]), bool(row["is_synthetic"]), tenant_target.FEED_VIEW)


# ── The kind ─────────────────────────────────────────────────────────────────


async def run(pool: Any, job: Job, *, environ: Optional[Environ] = None) -> dict[str, Any]:
    payload = validate_payload(job.payload)
    env = os.environ if environ is None else environ
    emit = observability.emit
    async with pool.acquire() as conn, tenant.system_scope(conn, op=KIND):
        if payload.get("trigger", "manual") == "schedule":
            state, rows = await _feed(conn)
            if state != "feed":
                emit("tenant_erase_feed", state=state)
                return {"mode": state, "trigger": "schedule", "requests": [],
                        "attention": state == "feed_shape_unknown"}
            done = {(r["request_id"], r["mode"])
                    for r in await conn.fetch(DONE_SQL, list(DONE_STATUSES), list(FINAL_REFUSALS))}
            out = []
            for row in rows:
                if (row["request_id"], row["mode"]) in done:
                    continue
                markers = _markers_of(row) or await tenant_target.markers(conn, row["tenant_id"])
                out.append(await erase_one(conn, request_id=row["request_id"], tenant_id=row["tenant_id"],
                                           mode=row["mode"], expect_plan=row.get("tripplanner_plan_hash"),
                                           markers=markers, job_id=job.id, environ=env))
            result = {"mode": "feed", "trigger": "schedule", "requests": out,
                      "attention": any(needs_attention(e) for e in out)}
        else:
            tenant_id = await tenant_target.resolve(conn, payload)
            mode = payload.get("mode", "dry_run")
            request_id = tenant.canonical(payload["request_id"]) if payload.get("request_id") else job.id
            markers = await tenant_target.markers(conn, tenant_id)
            entry = await erase_one(conn, request_id=request_id, tenant_id=tenant_id, mode=mode,
                                    expect_plan=payload.get("expect_plan"), markers=markers, job_id=job.id,
                                    environ=env)
            result = {"mode": mode, "trigger": "manual", "requests": [entry], "attention": needs_attention(entry)}
    for e in result["requests"]:
        emit("tenant_erase", request_id=e["request_id"], tenant_id=e["tenant_id"], mode=e["mode"],
             status=e["status"], reason=e["reason"], plan_hash=e["plan_hash"])
    return result
