"""AA-1139 — the agency app's TripPlanner module, TripPlanner side: an agency's leads with their delivery status, its
usage this month, and its lead webhook signing key (version, rotation). Contract: root
``docs/contracts/lead.submitted.v1.openapi.yaml`` 1.1 (tag ``planner``), served under ``/trip/m2m/v1`` by
``handler.serve_m2m`` with an M2M token of the CRM client, scope ``tripplanner.leads/manage``
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

import re
from datetime import datetime, timezone
from typing import Any, Optional

from backend.assembly import leads as leads_mod
from backend.shared import observability, settings, tenant_sessions

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
DEFAULT_LIMIT = 50
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
