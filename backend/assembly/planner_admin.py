"""AA-1139 — the agency app's TripPlanner module, TripPlanner side: an agency's leads with their delivery status, its
usage this month, and its lead webhook signing key (version, rotation). Contract: root
``docs/contracts/lead.submitted.v1.openapi.yaml`` 1.1 (tag ``planner``), served under ``/trip/m2m/v1`` by :func:`m2m`
(the assembly Lambda's M2M entry; any other M2M path goes on to ``handler.serve_m2m``, the lead pull) with an M2M token
of the CRM client, scope ``tripplanner.leads/manage``
(:data:`backend.shared.m2m_auth.SCOPE_LEADS_MANAGE`). The agency app (AA-Partner CRM API) is the only caller; it checks
the person's agency role first (root ADR 0008a: it never reads this schema).

  GET  /trip/m2m/v1/tenants/{id}/planner/leads?limit=n    the newest leads (n <= 50): id, time, channel, status, tries,
                                                         the last error code; this month's counts per status
  GET  /trip/m2m/v1/tenants/{id}/planner/usage            this UTC month's AI sessions against the plan quota
                                                         (``tp.ai_sessions``, CIS 438; null = no limit) and the
                                                         leads of the last 24 hours against the tenant's lead cap
  GET  /trip/m2m/v1/tenants/{id}/planner/webhook-key      the key version and when it was rotated (never the key)
  POST /trip/m2m/v1/tenants/{id}/planner/webhook-key/rotate  {"actor": "crm-member:<uuid>"} -> the new version and,
                                                         once, the new key (the agency app shows it once)

What never leaves: a traveller's name, e-mail, phone or message (a pending lead still holds them; the list reads ids,
times and states only), the webhook URL or the e-mail address (CIS serves the route), the master secret. A tenant is
served when CIS lists it with a TripPlanner subscription (``catalog.v_tenant_tripplanner_v1``); else 404
``unknown_tenant``. The connection is bound to the path's tenant (source m2m), so the tp-025 policy filters the
outbox as well.

Lead status (what the agency sees): ``delivered`` (handed over: the CRM stored it, the webhook answered 2xx or the
e-mail was sent), ``pending`` (waiting for the CRM pull or the first attempt), ``retrying`` (an attempt failed; the
daily run tries again inside the hold), ``rejected`` (the CRM refused it), ``expired`` (not delivered within the hold).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from backend.assembly import leads as leads_mod
from backend.shared import m2m_auth, observability, settings, tenant, tenant_sessions

VIEW = "catalog.v_tenant_tripplanner_v1"
TENANT_SQL = f"SELECT tenant_id, ai_sessions_monthly FROM {VIEW} WHERE tenant_id = $1"
_MONTH = "date_trunc('month', now() AT TIME ZONE 'UTC')"
LEADS_SQL = (
    "SELECT idempotency_key, created_at, delivery_channel, delivered_at, redacted_at, delivery_attempts, "
    "delivery_error FROM tripplanner.outbox WHERE event = 'lead.submitted' AND tenant_id = $1 "
    "ORDER BY created_at DESC, id DESC LIMIT $2")
MONTH_SQL = (
    "SELECT delivered_at IS NOT NULL AS delivered, redacted_at IS NOT NULL AS redacted, delivery_error, "
    "count(*)::int AS n FROM tripplanner.outbox WHERE event = 'lead.submitted' AND tenant_id = $1 "
    f"AND created_at >= ({_MONTH} AT TIME ZONE 'UTC') GROUP BY 1, 2, 3")
LEADS_24H_SQL = (
    "SELECT count(*)::int AS n FROM tripplanner.outbox WHERE event = 'lead.submitted' AND tenant_id = $1 "
    "AND created_at > now() - interval '24 hours'")
KEY_SQL = "SELECT version, rotated_at FROM tripplanner.lead_webhook_key WHERE scope = $1"
ROTATE_SQL = (
    "INSERT INTO tripplanner.lead_webhook_key AS k (scope, version, rotated_by) VALUES ($1, 1, $2) "
    "ON CONFLICT (scope) DO UPDATE SET version = k.version + 1, rotated_at = now(), "
    "rotated_by = EXCLUDED.rotated_by RETURNING version, rotated_at")

STATUSES = ("pending", "retrying", "delivered", "rejected", "expired")
DEFAULT_LIMIT = leads_mod.PAGE_MAX   # one page of the list, the contract maximum
ACTOR_RE = re.compile(r"^crm-member:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_CODE_RE = re.compile(r"^[a-z][a-z0-9_:]{0,63}$")


class Refused(Exception):  # noqa: N818 - an outcome
    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status = status
        self.code = code


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def lead_status(delivered: bool, redacted: bool, error: Optional[str]) -> str:
    """The status the agency sees (module docstring). Pure."""
    if delivered:
        return "delivered"
    err = error or ""
    if redacted:
        return "rejected" if err.startswith("rejected") else "expired"
    return "retrying" if err else "pending"


def _error_code(value: Any) -> Optional[str]:
    """A delivery error as its stable code (the outbox CHECK shape), never free text."""
    return value if isinstance(value, str) and _CODE_RE.fullmatch(value) else None


async def tenant_row(conn: Any, tenant_id: str) -> Any:
    """The tenant's row of the CIS view, or :class:`Refused` (404 unknown_tenant; 503 when the view cannot be read)."""
    try:
        row = await conn.fetchrow(TENANT_SQL, tenant_id)
    except Exception as e:  # noqa: BLE001 — only the type and SQLSTATE are logged
        observability.emit("planner_admin_tenant_unreadable", error=type(e).__name__,
                           sqlstate=getattr(e, "sqlstate", None))
        raise Refused(503, "unavailable") from None
    if row is None:
        raise Refused(404, "unknown_tenant")
    return row


async def leads(conn: Any, tenant_id: str, limit: int = DEFAULT_LIMIT) -> dict:
    """PlannerLeadList: the newest leads (ids, times, states) and this UTC month's counts per status."""
    rows = await conn.fetch(LEADS_SQL, tenant_id, limit)
    items = []
    for r in rows:
        key = str(r["idempotency_key"] or "")
        if not key.startswith("tp-lead:"):
            continue
        status = lead_status(r["delivered_at"] is not None, r["redacted_at"] is not None, r["delivery_error"])
        items.append({"lead_id": key[len("tp-lead:"):], "created_at": _iso(r["created_at"]),
                      "channel": r["delivery_channel"], "status": status,
                      "attempts": int(r["delivery_attempts"] or 0),
                      "delivered_at": _iso(r["delivered_at"]),
                      "error": None if status == "delivered" else _error_code(r["delivery_error"])})
    month = {s: 0 for s in STATUSES}
    for r in await conn.fetch(MONTH_SQL, tenant_id):
        month[lead_status(bool(r["delivered"]), bool(r["redacted"]), r["delivery_error"])] += int(r["n"])
    return {"leads": items, "month": {"total": sum(month.values()), **month}}


async def usage(conn: Any, tenant_id: str, quota: Optional[int], *, now: Optional[datetime] = None) -> dict:
    """PlannerUsage: this UTC month's AI sessions (tp-032) against the plan quota, and the leads of the last 24 hours
    against the tenant's lead cap (the setting ``tripplanner.leads.tenant_daily_cap``, a tenant override first)."""
    scope = tenant_sessions.scope(tenant_id)
    used = int(await conn.fetchval(tenant_sessions.COUNT_SQL, scope) or 0)
    leads_24h = int(await conn.fetchval(LEADS_24H_SQL, tenant_id) or 0)
    stamp = now or datetime.now(timezone.utc)
    return {"month": stamp.strftime("%Y-%m"),
            "ai_sessions": {"used": used, "quota": int(quota) if quota is not None else None},
            "leads_24h": {"count": leads_24h, "cap": int(settings.get(settings.LEAD_TENANT_DAILY_CAP, tenant_id))}}


async def key_info(conn: Any, tenant_id: str) -> dict:
    """PlannerWebhookKey: the version in force (0 = never rotated) and when; the key itself is never read back."""
    row = await conn.fetchrow(KEY_SQL, tenant_sessions.scope(tenant_id))
    return {"version": int(row["version"]) if row else 0, "rotated_at": _iso(row["rotated_at"]) if row else None,
            "available": leads_mod.webhook_master_key() is not None}


def valid_actor(body: Any) -> str:
    actor = body.get("actor") if isinstance(body, dict) else None
    if not isinstance(actor, str) or not ACTOR_RE.fullmatch(actor):
        raise Refused(400, "invalid_actor")
    return actor


async def rotate_key(conn: Any, tenant_id: str, actor: str) -> dict:
    """PlannerWebhookKeyRotated: a new version and its key, shown once. Refused (503 webhook_key_unavailable) before
    anything is written when this deployment has no master secret."""
    master = leads_mod.webhook_master_key()
    if master is None:
        raise Refused(503, "webhook_key_unavailable")
    async with conn.transaction():
        row = await conn.fetchrow(ROTATE_SQL, tenant_sessions.scope(tenant_id), actor)
    version = int(row["version"])
    observability.emit("lead_webhook_key_rotated", tenant_id=tenant_id, version=version)
    return {"version": version, "rotated_at": _iso(row["rotated_at"]),
            "key": leads_mod.webhook_key(master, tenant_id, version).decode()}


# --- the M2M routes ---------------------------------------------------------------------------------------------------

PATH_RE = re.compile(r"^/trip/m2m/v1/tenants/([^/]+)/planner/(leads|usage|webhook-key|webhook-key/rotate)$")
METHODS = {"leads": "GET", "usage": "GET", "webhook-key": "GET", "webhook-key/rotate": "POST"}
_HEADERS = {"content-type": "application/json", "cache-control": "no-store"}


def _resp(status: int, body: dict) -> dict:
    return {"statusCode": status, "headers": _HEADERS, "body": json.dumps(body, default=str)}


async def m2m(method: str, path: str, body: dict, authorization: Optional[str],
              fallback: Callable[..., Awaitable[dict]]) -> dict:
    """The assembly Lambda's M2M entry: ``.../planner/*`` here, any other ``/trip/m2m/`` path to ``fallback`` (the lead
    pull, ``handler.serve_m2m``)."""
    if PATH_RE.match(path):
        return await serve(method, path, body, authorization)
    return await fallback(method, path, body, authorization)


def parse(method: str, path: str, body: dict) -> tuple[str, str, int, str]:
    """(tenant id, route, limit, actor) of a planner request, or :class:`Refused` (no connection is taken)."""
    match = PATH_RE.match(path)
    if match is None or METHODS[match.group(2)] != method:
        raise Refused(404, "not_found")
    tenant_raw, what = match.group(1), match.group(2)
    if not leads_mod.is_uuid(tenant_raw):
        raise Refused(400, "invalid_tenant_id")
    limit, actor = DEFAULT_LIMIT, ""
    if what == "leads":
        raw = body.get("limit", DEFAULT_LIMIT)
        if not str(raw).isdigit() or not 1 <= int(raw) <= leads_mod.PAGE_MAX:
            raise Refused(400, "invalid_limit")
        limit = int(raw)
    if what == "webhook-key/rotate":
        actor = valid_actor(body)
    return tenant_raw, what, limit, actor


async def answer(conn: Any, tenant_id: str, what: str, limit: int, actor: str) -> dict:
    """The body of one planner route for the bound tenant (CIS must list it: else 404 unknown_tenant)."""
    row = await tenant_row(conn, tenant_id)
    if what == "leads":
        return await leads(conn, tenant_id, limit)
    if what == "usage":
        return await usage(conn, tenant_id, row["ai_sessions_monthly"])
    if what == "webhook-key":
        return await key_info(conn, tenant_id)
    return await rotate_key(conn, tenant_id, actor)


async def serve(method: str, path: str, body: dict, authorization: Optional[str], *, acquire=None,
                verify=None) -> dict:
    """GET .../planner/leads, .../planner/usage, .../planner/webhook-key and POST .../planner/webhook-key/rotate. The
    bearer (scope tripplanner.leads/manage) is verified before any SQL; the connection is then bound to the path's
    tenant (source m2m), so the tp-025 policy filters the outbox as well."""
    verify = verify or m2m_auth.verify_bearer
    try:
        await verify(authorization, m2m_auth.SCOPE_LEADS_MANAGE)
        tenant_raw, what, limit, actor = parse(method, path, body)
    except (m2m_auth.Refused, Refused) as e:
        if isinstance(e, m2m_auth.Refused):
            observability.emit("m2m_refused", code=e.code)
        return _resp(e.status, {"error": e.code})
    try:
        if acquire is None:
            from backend.shared.db import get_pool
            acquire = (await get_pool()).acquire
        async with acquire() as conn, tenant.bound(conn, tenant_raw, source=tenant.SOURCE_M2M) as tid:
            return _resp(200, await answer(conn, tid, what, limit, actor))
    except Refused as e:
        return _resp(e.status, {"error": e.code})
    except Exception as e:  # noqa: BLE001
        observability.emit("assembly_error", route=f"m2m_planner_{what}", error=type(e).__name__)
        observability.capture(e)
        return _resp(503, {"error": "unavailable"})
