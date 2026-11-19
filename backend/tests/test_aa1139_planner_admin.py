"""AA-1139 (offline): the agency app's TripPlanner module, TripPlanner side (backend/assembly/planner_admin.py and
its M2M entry ``planner_admin.m2m``, contract lead.submitted.v1 1.1 tag planner).

Checks: the rotatable webhook key (version 0 is the key every tenant had, version n adds ":" + n, the delivery signs
with the stored version and sends nothing when the version cannot be read); the planner routes need the manage scope
and refuse before any SQL; bad requests take no connection; the lead list carries ids, times and states only (never a
traveller field) and its statuses; usage reads the month's AI sessions against the quota; the key info never carries
the key; a rotation shows the new key once and refuses without a master secret. Invented names only.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest

from backend import config
from backend.assembly import handler, leads, planner_admin
from backend.shared import m2m_auth, settings, tenant

TENANT = "5c6d7e8f-0000-4000-8000-000000001139"
MEMBER = "crm-member:5c6d7e8f-0000-4000-8000-0000000000aa"
LEAD_1 = "5c6d7e8f-0000-4000-8000-0000000000b1"
LEAD_2 = "5c6d7e8f-0000-4000-8000-0000000000b2"
T0 = datetime(2026, 11, 18, 9, 0, tzinfo=timezone.utc)
BASE = f"/trip/m2m/v1/tenants/{TENANT}/planner"


# --- the rotatable key ----------------------------------------------------------------------------------------------

def test_version_zero_is_the_key_every_tenant_had_and_a_rotation_changes_it():
    legacy = hmac.new(b"synthetic-master", b"aa-tripplanner lead webhook v1:" + TENANT.encode(),
                      hashlib.sha256).hexdigest().encode()
    v2 = hmac.new(b"synthetic-master", b"aa-tripplanner lead webhook v1:" + TENANT.encode() + b":2",
                  hashlib.sha256).hexdigest().encode()
    assert leads.webhook_key(b"synthetic-master", TENANT) == legacy
    assert leads.webhook_key(b"synthetic-master", TENANT, 0) == legacy
    assert leads.webhook_key(b"synthetic-master", TENANT, 2) == v2
    assert len({leads.webhook_key(b"synthetic-master", TENANT, n) for n in range(4)}) == 4
    with pytest.raises(ValueError):
        leads.webhook_key(b"synthetic-master", TENANT, -1)


class _VersionConn:
    def __init__(self, value=None, error=None, in_tx=False):
        self.value, self.error, self.in_tx, self.savepoints = value, error, in_tx, 0

    def is_in_transaction(self):
        return self.in_tx

    def transaction(self):
        conn = self

        @asynccontextmanager
        async def tx():
            conn.savepoints += 1
            yield

        return tx()

    async def fetchval(self, sql, scope):
        assert sql == leads.KEY_VERSION_SQL and scope == f"tenant:{TENANT}"
        if self.error:
            raise self.error
        return self.value

    async def execute(self, sql, *args):
        self.executed = (sql, args)


class _Missing(Exception):
    sqlstate = "42P01"


class _Broken(Exception):
    sqlstate = "08006"


@pytest.mark.asyncio
async def test_key_version_reads_the_row_and_falls_back_only_for_a_missing_table():
    assert await leads.webhook_key_version(_VersionConn(3), TENANT) == 3
    assert await leads.webhook_key_version(_VersionConn(None), TENANT) == 0
    assert await leads.webhook_key_version(_VersionConn(error=_Missing()), TENANT) == 0
    assert await leads.webhook_key_version(_VersionConn(error=_Broken()), TENANT) is None
    conn = _VersionConn(1, in_tx=True)
    assert await leads.webhook_key_version(conn, TENANT) == 1 and conn.savepoints == 1


@pytest.mark.asyncio
async def test_delivery_signs_with_the_stored_version_and_sends_nothing_when_it_is_unreadable(monkeypatch):
    monkeypatch.setattr(config, "TP_LEAD_WEBHOOK_KEY", "synthetic-master")
    route = leads.Route(TENANT, "brackenfold-trails", "Brackenfold Trails", "webhook",
                        webhook_url="https://brackenfold.example.invalid/hooks/leads")
    payload = {"lead_id": LEAD_1, "idempotency_key": f"tp-lead:{LEAD_1}", "tenant": {"tenant_id": TENANT}}
    seen = []

    def post(url, body, headers, timeout):
        seen.append((body, headers))
        return 204

    assert await leads.deliver_now(_VersionConn(2), 7, route, payload, post=post) is None
    body, headers = seen[0]
    ts = int(headers["X-AA-Signature"].split(",")[0][2:])
    assert headers["X-AA-Signature"] == leads.signature(leads.webhook_key(b"synthetic-master", TENANT, 2), body, ts)
    conn = _VersionConn(error=_Broken())
    assert await leads.deliver_now(conn, 7, route, payload, post=post) == "webhook_key_unavailable"
    assert len(seen) == 1 and conn.executed[1] == (7, "webhook_key_unavailable")


# --- statuses -------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("delivered,redacted,error,status", [
    (True, True, None, "delivered"),
    (False, False, None, "pending"),
    (False, False, "webhook_http_500", "retrying"),
    (False, True, "rejected:missing_name", "rejected"),
    (False, True, "rejected", "rejected"),
    (False, True, "expired", "expired"),
])
def test_lead_status(delivered, redacted, error, status):
    assert planner_admin.lead_status(delivered, redacted, error) == status


# --- the M2M routes -------------------------------------------------------------------------------------------------

def _no_sql_acquire(used):
    @asynccontextmanager
    async def acquire():
        used.append("acquire")
        raise AssertionError("no connection may be taken")
        yield None  # pragma: no cover

    return acquire


@pytest.mark.asyncio
@pytest.mark.parametrize("refusal", [m2m_auth.Refused(401, "unauthorized"), m2m_auth.Refused(403, "forbidden")])
async def test_planner_routes_need_the_manage_scope_before_any_sql(refusal):
    used: list = []
    scopes: list = []

    async def verify(_auth, scope=m2m_auth.SCOPE_LEADS_DELIVER):
        scopes.append(scope)
        raise refusal

    out = await planner_admin.serve("GET", f"{BASE}/leads", {}, "Bearer x", acquire=_no_sql_acquire(used),
                                    verify=verify)
    assert (out["statusCode"], json.loads(out["body"])) == (refusal.status, {"error": refusal.code})
    assert used == [] and scopes == [m2m_auth.SCOPE_LEADS_MANAGE] == ["tripplanner.leads/manage"]


@pytest.mark.asyncio
async def test_a_deliver_scope_token_cannot_reach_the_planner_routes(monkeypatch):
    from backend.shared.identity_verifier import Principal

    class Verifier:
        async def verify(self, token):
            return Principal(iss="i", sub="crm", client_id="crm", groups=("tripplanner.leads/deliver",), email=None,
                             email_verified=None, username=None)

    used: list = []
    try:
        m2m_auth.set_verifier(Verifier())
        out = await planner_admin.serve("POST", f"{BASE}/webhook-key/rotate", {"actor": MEMBER}, "Bearer t",
                                        acquire=_no_sql_acquire(used))
    finally:
        m2m_auth.set_verifier(None)
    assert out["statusCode"] == 403 and used == []


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,body,status,code", [
    ("GET", "/trip/m2m/v1/tenants/not-a-uuid/planner/leads", {}, 400, "invalid_tenant_id"),
    ("GET", f"{BASE}/leads", {"limit": "0"}, 400, "invalid_limit"),
    ("GET", f"{BASE}/leads", {"limit": "51"}, 400, "invalid_limit"),
    ("POST", f"{BASE}/webhook-key/rotate", {}, 400, "invalid_actor"),
    ("POST", f"{BASE}/webhook-key/rotate", {"actor": "admin:someone"}, 400, "invalid_actor"),
    ("POST", f"{BASE}/usage", {}, 404, "not_found"),
    ("GET", f"{BASE}/webhook-key/rotate", {}, 404, "not_found"),
])
async def test_planner_bad_requests_take_no_connection(method, path, body, status, code):
    used: list = []

    async def verify(_auth, scope=None):
        return "crm-client"

    out = await planner_admin.serve(method, path, body, "Bearer x", acquire=_no_sql_acquire(used), verify=verify)
    assert (out["statusCode"], json.loads(out["body"])) == (status, {"error": code}) and used == []


class _Conn:
    """The bound connection of one tenant: the CIS view row, the outbox rows, the month's sessions, the key row."""

    def __init__(self, view_row=None, key_row=None):
        self.view_row = view_row if view_row is not None else {"tenant_id": TENANT, "ai_sessions_monthly": 300}
        self.key_row = key_row
        self.seen: list = []

    async def execute(self, sql, *args):
        self.seen.append(("execute", sql, args))

    async def fetchrow(self, sql, *args):
        self.seen.append(("fetchrow", sql, args))
        if sql == planner_admin.TENANT_SQL:
            return self.view_row or None
        if sql == planner_admin.KEY_SQL:
            return self.key_row
        if sql == planner_admin.ROTATE_SQL:
            version = (self.key_row["version"] if self.key_row else 0) + 1
            self.key_row = {"version": version, "rotated_at": T0}
            return self.key_row
        raise AssertionError(sql)

    async def fetch(self, sql, *args):
        self.seen.append(("fetch", sql, args))
        if sql == planner_admin.LEADS_SQL:
            return [
                {"idempotency_key": f"tp-lead:{LEAD_1}", "created_at": T0, "delivery_channel": "webhook",
                 "delivered_at": None, "redacted_at": None, "delivery_attempts": 2,
                 "delivery_error": "webhook_http_500", "payload": {"traveller": {"name": "Ada Fernwick"}}},
                {"idempotency_key": f"tp-lead:{LEAD_2}", "created_at": T0, "delivery_channel": "crm",
                 "delivered_at": T0, "redacted_at": T0, "delivery_attempts": 0, "delivery_error": None},
            ]
        if sql == planner_admin.MONTH_SQL:
            return [{"delivered": True, "redacted": True, "delivery_error": None, "n": 4},
                    {"delivered": False, "redacted": False, "delivery_error": "webhook_http_500", "n": 1},
                    {"delivered": False, "redacted": True, "delivery_error": "expired", "n": 2}]
        raise AssertionError(sql)

    async def fetchval(self, sql, *args):
        self.seen.append(("fetchval", sql, args))
        return {planner_admin.LEADS_24H_SQL: 5}.get(sql, 17)

    def transaction(self):
        @asynccontextmanager
        async def tx():
            yield

        return tx()


async def _call(method, path, body=None, conn=None):
    conn = conn or _Conn()

    @asynccontextmanager
    async def acquire():
        yield conn

    async def verify(_auth, scope=None):
        assert scope == m2m_auth.SCOPE_LEADS_MANAGE
        return "crm-client"

    out = await planner_admin.serve(method, path, body or {}, "Bearer x", acquire=acquire, verify=verify)
    return out["statusCode"], json.loads(out["body"]), conn


@pytest.mark.asyncio
async def test_leads_list_ids_times_and_states_only():
    status, body, conn = await _call("GET", f"{BASE}/leads", {"limit": "20"})
    assert status == 200
    assert body["leads"] == [
        {"lead_id": LEAD_1, "created_at": T0.isoformat(), "channel": "webhook", "status": "retrying", "attempts": 2,
         "delivered_at": None, "error": "webhook_http_500"},
        {"lead_id": LEAD_2, "created_at": T0.isoformat(), "channel": "crm", "status": "delivered", "attempts": 0,
         "delivered_at": T0.isoformat(), "error": None},
    ]
    assert body["month"] == {"total": 7, "pending": 0, "retrying": 1, "delivered": 4, "rejected": 0, "expired": 2}
    assert "Ada" not in json.dumps(body) and "traveller" not in json.dumps(body)
    assert ("execute", "SELECT set_config('app.tenant_id', $1, false)", (TENANT,)) in conn.seen
    assert ("fetch", planner_admin.LEADS_SQL, (TENANT, 20)) in conn.seen


@pytest.mark.asyncio
async def test_unknown_tenant_is_404():
    status, body, _ = await _call("GET", f"{BASE}/usage", conn=_Conn(view_row={}))
    assert (status, body) == (404, {"error": "unknown_tenant"})


@pytest.mark.asyncio
async def test_usage_is_the_month_sessions_against_the_quota(monkeypatch):
    monkeypatch.setattr(settings, "get", lambda key, tenant_id=None: 200 if key == settings.LEAD_TENANT_DAILY_CAP
                        else None)
    status, body, conn = await _call("GET", f"{BASE}/usage")
    assert status == 200
    assert body["ai_allowance"] == {"used": 17, "quota": 300} and body["leads_24h"] == {"count": 5, "cap": 200}
    assert len(body["month"]) == 7
    assert ("fetchval", planner_admin.tenant_sessions.COUNT_SQL, (f"tenant:{TENANT}",)) in conn.seen
    _, open_body, _ = await _call("GET", f"{BASE}/usage", conn=_Conn(view_row={"tenant_id": TENANT,
                                                                               "ai_sessions_monthly": None}))
    assert open_body["ai_allowance"]["quota"] is None


@pytest.mark.asyncio
async def test_key_info_never_carries_the_key(monkeypatch):
    monkeypatch.setattr(config, "TP_LEAD_WEBHOOK_KEY", "synthetic-master")
    status, body, _ = await _call("GET", f"{BASE}/webhook-key")
    assert (status, body) == (200, {"version": 0, "rotated_at": None, "available": True})
    status, body, _ = await _call("GET", f"{BASE}/webhook-key", conn=_Conn(key_row={"version": 3, "rotated_at": T0}))
    assert body == {"version": 3, "rotated_at": T0.isoformat(), "available": True}


@pytest.mark.asyncio
async def test_rotation_shows_the_new_key_once(monkeypatch):
    monkeypatch.setattr(config, "TP_LEAD_WEBHOOK_KEY", "synthetic-master")
    conn = _Conn(key_row={"version": 1, "rotated_at": T0})
    status, body, _ = await _call("POST", f"{BASE}/webhook-key/rotate", {"actor": MEMBER}, conn=conn)
    assert status == 200 and body["version"] == 2
    assert body["key"] == leads.webhook_key(b"synthetic-master", TENANT, 2).decode()
    assert ("fetchrow", planner_admin.ROTATE_SQL, (f"tenant:{TENANT}", MEMBER)) in conn.seen
    _, info, _ = await _call("GET", f"{BASE}/webhook-key", conn=conn)
    assert "key" not in info and info["version"] == 2


@pytest.mark.asyncio
async def test_rotation_without_a_master_secret_writes_nothing(monkeypatch):
    monkeypatch.setattr(config, "TP_LEAD_WEBHOOK_KEY", "")
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    conn = _Conn()
    status, body, _ = await _call("POST", f"{BASE}/webhook-key/rotate", {"actor": MEMBER}, conn=conn)
    assert (status, body) == (503, {"error": "webhook_key_unavailable"})
    assert not any(sql == planner_admin.ROTATE_SQL for _, sql, _ in conn.seen)


@pytest.mark.asyncio
async def test_the_m2m_entry_sends_planner_paths_here_and_the_rest_to_the_pull(monkeypatch):
    seen = []

    async def serve(method, path, body, authorization):
        seen.append(("planner", path))
        return {"statusCode": 200}

    async def fallback(method, path, body, authorization):
        seen.append(("pull", path))
        return {"statusCode": 200}

    monkeypatch.setattr(planner_admin, "serve", serve)
    await planner_admin.m2m("GET", f"{BASE}/usage", {}, "Bearer x", fallback)
    await planner_admin.m2m("GET", f"/trip/m2m/v1/tenants/{TENANT}/leads", {}, "Bearer x", fallback)
    await planner_admin.m2m("GET", f"{BASE}/other", {}, "Bearer x", fallback)
    assert seen == [("planner", f"{BASE}/usage"), ("pull", f"/trip/m2m/v1/tenants/{TENANT}/leads"),
                    ("pull", f"{BASE}/other")]


@pytest.mark.asyncio
async def test_the_lead_pull_is_unchanged_by_the_planner_routes():
    used: list = []
    scopes: list = []

    async def verify(_auth, scope=m2m_auth.SCOPE_LEADS_DELIVER):
        scopes.append(scope)
        raise m2m_auth.Refused(401, "unauthorized")

    await handler.serve_m2m("GET", f"/trip/m2m/v1/tenants/{TENANT}/leads", {}, "Bearer x",
                            acquire=_no_sql_acquire(used), verify=verify)
    assert scopes == [m2m_auth.SCOPE_LEADS_DELIVER] and tenant.current() is None
