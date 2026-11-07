"""AA-1038 R5 slice 3 (offline), S272 review fixes: each test is the reproduction of one review finding.

* the Traveller rules are the shared vectors (tests/contracts/lead.submitted.v1.traveller-rules.json, a byte copy of
  AA-Ecosys docs/contracts/rules/): a name NFC lengthens past 120 is refused here, never rejected later by the CRM;
* send-to-advisor is limited per session and per IP (429 rate_limited) and per tenant per day (429 lead_cap_reached),
  and the crm channel refuses a tenant the CRM is not pulling (503): in every case nothing is stored;
* the M2M API serves only a tenant whose route is crm now; a list call is the tenant's beacon;
* an ack never changes a final state; the webhook never posts into a private network, and a webhook route on a
  non-public address is refused before anything is stored; the SES client has short timeouts.

A fake connection plays the database. Invented names only.
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from backend import config
from backend.assembly import account as account_mod
from backend.assembly import handler, leads
from backend.shared import ratelimit, tenant

RULES = json.loads((Path(__file__).parent / "contracts" / "lead.submitted.v1.traveller-rules.json").read_text())
TENANT_A = "3e4f5a6b-7c8d-4e9f-a0b1-c2d3e4f5a6b7"
TRIP = "1b2c3d4e-5f6a-4b7c-8d9e-0f1a2b3c4d5e"
SESSION = "2c3d4e5f-6a7b-4c8d-9e0f-1a2b3c4d5e6f"
LEAD = "7a1c2e3f-4b5d-4e6f-8a9b-0c1d2e3f4a5b"
GOOD = {"traveller": {"name": "Ada Fernwick", "email": "ada@example.invalid", "phone": None, "locale": "en"},
        "party": {"adults": 2, "child_ages": []}, "trip": {}, "consent": {"contact": True, "marketing": False}}


# --- one Traveller rule on both sides -------------------------------------------------------------------------------

def _with(field, value):
    lead = json.loads(json.dumps(GOOD))
    lead["traveller"][field] = value
    return lead


@pytest.mark.parametrize("case", RULES["cases"], ids=lambda c: f"{c['field']}-{c['id']}")
def test_traveller_rules_shared_vectors(case):
    try:
        leads.validate(_with(case["field"], case["value"]))
        verdict = True
    except leads.LeadRefused as e:
        assert e.code == "invalid_lead" and e.fields == [case["field"]]
        verdict = False
    assert verdict is case["valid"]


def test_the_rule_constants_are_the_shared_ones():
    assert leads._EMAIL_RE.pattern == RULES["email_pattern"]  # noqa: SLF001
    assert leads._PHONE_RE.pattern == RULES["phone_pattern"]  # noqa: SLF001
    assert (leads.MAX_NAME, leads.MAX_EMAIL, leads.MAX_PHONE) == tuple(RULES["max_length"][k]
                                                                       for k in ("name", "email", "phone"))


def test_review_repro_a_name_nfc_lengthens_is_refused_before_storing():
    with pytest.raises(leads.LeadRefused) as e:
        leads.validate(_with("name", "שּׁ" * 41))  # 41 code points raw, 123 after NFC (the CRM's rule)
    assert e.value.fields == ["name"]


# --- send-to-advisor: limits and the crm beacon, nothing stored on a refusal ----------------------------------------

class SendConn:
    def __init__(self):
        self.stored = 0

    async def execute(self, sql, *args):
        return "SELECT 1"

    def transaction(self):
        @asynccontextmanager
        async def tx():
            yield

        return tx()


@pytest.fixture
def send_env(monkeypatch):
    state = {"channel": "crm", "fresh": True, "leads_24h": 0, "limited": set(), "hits": [], "stored": 0, "sent": 0,
             "webhook_url": "https://hooks.example.invalid/l"}

    async def guard(*a, **k):
        return None

    async def load_route(conn, tid):
        return leads.Route(tid, "quillmere-voyages", "Quillmere Voyages", state["channel"],
                           webhook_url=state["webhook_url"] if state["channel"] == "webhook" else None)

    async def fresh(conn, tid):
        return state["fresh"]

    async def hit(conn, scope, kind, value, *, signed_in=False):
        state["hits"].append((scope, kind))
        allowed = scope not in state["limited"]
        return ratelimit.Decision(allowed, scope, 5, 3600, 6 if not allowed else 1, 600 if not allowed else 0)

    async def leads_24h(conn, tid):
        return state["leads_24h"]

    async def last_event(conn, trip_id):
        return {"id": 9, "event_type": "sent"} if state["sent"] else None

    async def itinerary(conn, trip_id):
        return [{"day": 1, "stops": [{"component_id": "c1"}]}]

    async def custom_days(conn, trip_id):
        return []

    async def summary(conn, current, extra_days=0):
        return {"days": 1, "countries": ["Vietnam"], "tours": [], "day_plan": []}

    async def append_event(conn, trip_id, session_id, event, payload):
        state["sent"] += 1
        return []

    async def write_lead(conn, *, trip_id, payload, channel):
        state["stored"] += 1
        return 1

    monkeypatch.setattr(handler, "_guard", guard)
    monkeypatch.setattr(handler, "_last_event", last_event)
    monkeypatch.setattr(leads, "load_route", load_route)
    monkeypatch.setattr(leads, "crm_pull_fresh", fresh)
    monkeypatch.setattr(leads, "tenant_leads_24h", leads_24h)
    monkeypatch.setattr(leads, "trip_summary", summary)
    monkeypatch.setattr(leads, "write_lead", write_lead)
    monkeypatch.setattr(ratelimit, "hit", hit)
    monkeypatch.setattr(handler.events_mod, "current_itinerary", itinerary)
    monkeypatch.setattr(handler.events_mod, "current_custom_days", custom_days)
    monkeypatch.setattr(handler.events_mod, "append_event", append_event)
    monkeypatch.setattr(config, "LEAD_CRM_PULL_ENABLED", True)
    return state


async def _send(client=ratelimit.UNKNOWN_CLIENT):
    out = await handler._send_lead(SendConn(), TRIP, {"session_id": SESSION, "lead": GOOD}, "tok",  # noqa: SLF001
                                   account_mod.GUEST, TENANT_A, client=client)
    return out["statusCode"], json.loads(out["body"])


@pytest.mark.asyncio
async def test_a_fresh_crm_tenant_stores_one_lead(send_env):
    status, body = await _send(ratelimit.Client(ip="203.0.113.7"))
    assert status == 200 and body["lead"]["channel"] == "crm" and send_env["stored"] == 1
    assert send_env["hits"] == [("send", "session"), ("send_ip", "ip")]


@pytest.mark.asyncio
async def test_review_repro_crm_not_pulling_this_tenant_refuses_before_storing(send_env):
    """Agency locked / offboarding / not provisioned in the CRM: the CRM never lists it, so the beacon is stale."""
    send_env["fresh"] = False
    assert await _send() == (503, {"error": "lead_channel_unavailable"})
    assert send_env["stored"] == 0 and send_env["hits"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["send", "send_ip"])
async def test_review_repro_a_send_loop_is_rate_limited_before_storing(send_env, scope):
    send_env["limited"] = {scope}
    status, body = await _send(ratelimit.Client(ip="203.0.113.7"))
    assert status == 429 and body["error"] == "rate_limited" and body["scope"] == scope
    assert send_env["stored"] == 0


@pytest.mark.asyncio
async def test_review_repro_the_tenant_daily_cap_refuses_before_storing(send_env, monkeypatch):
    monkeypatch.setattr(config, "TP_LEAD_TENANT_DAILY_CAP", 3)
    send_env["leads_24h"] = 3
    assert await _send() == (429, {"error": "lead_cap_reached"})
    assert send_env["stored"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("url,addresses", [
    ("https://127.0.0.1/hook", None), ("https://10.0.3.7:8443/leads", None), ("https://169.254.169.254/x", None),
    ("https://[::1]/hook", None), ("https://internal.example.invalid/h", ["10.0.4.20"]),
    ("https://nowhere.example.invalid/h", OSError("synthetic: no such host")),
])
async def test_review_repro_a_webhook_route_on_a_non_public_address_refuses_before_storing(send_env, monkeypatch,
                                                                                         url, addresses):
    """Review repro: the CIS 390 CHECK accepts https://127.0.0.1/hook; the lead was stored, never delivered and held
    until the hold ended it. Now the target is checked before the transaction: 503, nothing stored, nothing posted."""
    posted = []

    def resolve(host):
        if addresses is None:
            raise AssertionError("an IP literal is never resolved")
        if isinstance(addresses, Exception):
            raise addresses
        return addresses

    monkeypatch.setattr(leads, "resolve_host_bounded", resolve)
    monkeypatch.setattr(leads, "urllib_post", lambda *a, **k: posted.append(a) or 200)
    monkeypatch.setattr(config, "TP_LEAD_WEBHOOK_KEY", "synthetic-master")
    send_env.update(channel="webhook", webhook_url=url)
    assert await _send(ratelimit.Client(ip="203.0.113.7")) == (503, {"error": "lead_channel_unavailable"})
    assert send_env["stored"] == 0 and send_env["hits"] == [] and posted == []


@pytest.mark.asyncio
async def test_a_webhook_route_on_a_public_address_stores_and_posts(send_env, monkeypatch):
    posted = []
    monkeypatch.setattr(leads, "resolve_host_bounded", lambda host: ["93.184.215.14"])
    monkeypatch.setattr(leads, "urllib_post", lambda url, body, headers, timeout: posted.append(url) or 200)
    monkeypatch.setattr(config, "TP_LEAD_WEBHOOK_KEY", "synthetic-master")
    send_env.update(channel="webhook")
    status, body = await _send()
    assert status == 200 and body["lead"]["channel"] == "webhook" and send_env["stored"] == 1
    assert posted == ["https://hooks.example.invalid/l"]


def test_a_slow_resolver_is_bounded_and_refuses(monkeypatch):
    import time as _time

    monkeypatch.setattr(config, "LEAD_WEBHOOK_RESOLVE_TIMEOUT_S", 0.05)
    monkeypatch.setattr(leads, "resolve_host", lambda host: _time.sleep(0.5) or ["93.184.215.14"])
    started = _time.monotonic()
    assert leads.webhook_target_problem("https://slow.example.invalid/h") == leads.WEBHOOK_URL_UNRESOLVABLE
    assert _time.monotonic() - started < 0.4


def test_target_problem_codes():
    public = lambda host: ["93.184.215.14"]  # noqa: E731
    assert leads.webhook_target_problem("https://hooks.example.invalid/l", resolve=public) is None
    assert leads.webhook_target_problem("https://127.0.0.1/hook") == leads.WEBHOOK_URL_NOT_PUBLIC
    assert leads.webhook_target_problem("http://hooks.example.invalid/l", resolve=public) == "webhook_url_not_https"


def test_unresolvable_is_not_the_permanent_defect_code(monkeypatch):
    """The daily retry treats webhook_urlnotpublic as a permanent route defect; a DNS failure stays transient."""
    monkeypatch.setattr(config, "TP_LEAD_WEBHOOK_KEY", "synthetic-master")

    def post(url, body, headers, timeout):
        return leads.urllib_post(url, body, headers, timeout, resolve=lambda h: [])

    lead = {"lead_id": LEAD, "idempotency_key": f"tp-lead:{LEAD}"}
    assert leads.deliver_webhook("https://agency.example.invalid/h", lead, TENANT_A, post=post) == \
        leads.WEBHOOK_URL_UNRESOLVABLE


@pytest.mark.asyncio
async def test_an_invalid_lead_is_not_counted_by_the_limits(send_env):
    out = await handler._send_lead(SendConn(), TRIP, {"session_id": SESSION, "lead": _with("email", "x")},  # noqa: SLF001
                                   "tok", account_mod.GUEST, TENANT_A)
    assert out["statusCode"] == 422 and send_env["hits"] == [] and send_env["stored"] == 0


def test_send_rules_exist_with_the_signed_in_factor(monkeypatch):
    monkeypatch.setattr(config, "TP_RATE_SEND_PER_HOUR", 5)
    monkeypatch.setattr(config, "TP_RATE_SEND_IP_PER_HOUR", 20)
    assert ratelimit.rule_for("send", signed_in=False) == ratelimit.Rule("send", 3600, 5)
    assert ratelimit.rule_for("send_ip", signed_in=True).limit == 20 * config.TP_RATE_SIGNED_IN_FACTOR


class BeaconConn:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        if self.fail:
            raise ConnectionError("synthetic")
        return True

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        if self.fail:
            raise ConnectionError("synthetic")
        return {"count": 1, "retry_after_s": 0}


@pytest.mark.asyncio
async def test_the_beacon_is_keyed_and_a_read_error_fails_closed(monkeypatch):
    monkeypatch.setattr(config, "LEAD_CRM_PULL_FRESH_S", 600)
    conn = BeaconConn()
    await leads.mark_crm_pull(conn, TENANT_A)
    assert await leads.crm_pull_fresh(conn, TENANT_A) is True
    (_, upsert, (h1, window)), (_, seen, (h2, secs)) = conn.calls
    assert upsert == ratelimit.UPSERT_SQL and seen == ratelimit.SEEN_SQL and h1 == h2 and TENANT_A not in h1
    assert (window, secs) == (ratelimit.BEACON_WINDOW_SECONDS, 600)
    assert await leads.crm_pull_fresh(BeaconConn(fail=True), TENANT_A) is False
    await leads.mark_crm_pull(BeaconConn(fail=True), TENANT_A)  # a failed beacon write never raises


# --- the M2M API serves only tenants on the CRM now -----------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["webhook", "missing"])
async def test_review_repro_a_leaked_crm_token_reaches_no_tenant_off_the_crm(monkeypatch, route):
    listed = []

    async def load_route(conn, tid):
        if route == "missing":
            raise leads.LeadRefused(409, "lead_route_missing")
        return leads.Route(tid, "s", "N", route, webhook_url="https://hooks.example.invalid/l")

    async def list_pending(conn, tid, limit):
        listed.append(tid)
        return {"leads": [], "more": False}

    class Conn:
        async def execute(self, sql, *args):
            return None

    @asynccontextmanager
    async def acquire():
        yield Conn()

    async def verify(_auth):
        return "crm-client"

    monkeypatch.setattr(leads, "load_route", load_route)
    monkeypatch.setattr(leads, "list_pending", list_pending)
    for method, path in [("GET", f"/trip/m2m/v1/tenants/{TENANT_A}/leads"),
                         ("POST", f"/trip/m2m/v1/tenants/{TENANT_A}/leads/{LEAD}/ack")]:
        out = await handler.serve_m2m(method, path, {"outcome": "rejected"}, "Bearer x", acquire=acquire,
                                      verify=verify)
        assert (out["statusCode"], json.loads(out["body"])) == (404, {"error": "tenant_not_on_crm"})
    assert listed == []


# --- ack: a final state never changes -------------------------------------------------------------------------------

class AckConn:
    def __init__(self, row):
        self.row = row
        self.writes = []

    async def fetchrow(self, sql, *args):
        assert sql == leads.LEAD_ROW_SQL
        return self.row

    async def execute(self, sql, *args):
        self.writes.append((sql, args))
        if sql == leads.DELIVERED_SQL:
            self.row = {**self.row, "delivered_at": "now", "delivery_error": None, "redacted_at": "now"}
        elif sql == leads.REJECTED_SQL:
            self.row = {**self.row, "redacted_at": "now", "delivery_error": args[2]}

    def transaction(self):
        @asynccontextmanager
        async def tx():
            yield

        return tx()


def _pending():
    return {"id": 5, "payload": json.dumps({"lead_id": LEAD, "tenant": {"tenant_id": TENANT_A}}),
            "delivered_at": None, "redacted_at": None, "delivery_error": None}


@pytest.mark.asyncio
async def test_review_repro_rejected_then_stored_changes_nothing():
    conn = AckConn(_pending())
    first = await leads.acknowledge(conn, TENANT_A, LEAD, "rejected", "invalid_lead")
    again = await leads.acknowledge(conn, TENANT_A, LEAD, "stored", None)
    assert first == {"lead_id": LEAD, "state": "rejected", "already": False}
    assert again == {"lead_id": LEAD, "state": "rejected", "already": True}
    assert len(conn.writes) == 1 and conn.row["delivery_error"] == "rejected:invalid_lead"


@pytest.mark.asyncio
async def test_review_repro_rejected_after_expiry_keeps_the_expiry_record():
    conn = AckConn({**_pending(), "redacted_at": "then", "delivery_error": "expired"})
    out = await leads.acknowledge(conn, TENANT_A, LEAD, "rejected", "invalid_lead")
    assert out == {"lead_id": LEAD, "state": "expired", "already": True} and conn.writes == []


@pytest.mark.asyncio
async def test_stored_after_expiry_says_the_agency_has_it():
    conn = AckConn({**_pending(), "redacted_at": "then", "delivery_error": "expired"})
    out = await leads.acknowledge(conn, TENANT_A, LEAD, "stored", None)
    assert out == {"lead_id": LEAD, "state": "delivered", "already": False}
    assert await leads.acknowledge(conn, TENANT_A, LEAD, "rejected", "x") == \
        {"lead_id": LEAD, "state": "delivered", "already": True}


# --- the webhook never posts into a private network -----------------------------------------------------------------

@pytest.mark.parametrize("url,addresses", [
    ("https://10.0.1.5/hook", None), ("https://127.0.0.1/hook", None), ("https://[::1]/hook", None),
    ("https://169.254.169.254/latest", None), ("https://internal.example.invalid/h", ["10.0.4.20"]),
    ("https://mixed.example.invalid/h", ["93.184.215.14", "172.16.0.9"]),
    ("https://cgnat.example.invalid/h", ["100.64.0.1"]), ("https://mapped.example.invalid/h", ["::ffff:192.168.1.1"]),
    ("https://nowhere.example.invalid/h", []), ("https:///h", None),
])
def test_review_repro_webhook_target_must_be_public(url, addresses):
    def resolve(host):
        if addresses is None:
            raise AssertionError("an IP literal is never resolved")
        return addresses

    with pytest.raises(leads.UrlNotPublic):
        leads.public_address_check(url, resolve=resolve)


def test_a_public_webhook_target_passes():
    leads.public_address_check("https://hooks.example.invalid/l", resolve=lambda h: ["93.184.215.14"])


def test_a_private_target_is_refused_before_any_connection(monkeypatch):
    monkeypatch.setattr(config, "TP_LEAD_WEBHOOK_KEY", "synthetic-master")

    def post(url, body, headers, timeout):
        return leads.urllib_post(url, body, headers, timeout, resolve=lambda h: ["10.0.0.8"])

    lead = {"lead_id": LEAD, "idempotency_key": f"tp-lead:{LEAD}"}
    assert leads.deliver_webhook("https://agency.example.invalid/h", lead, TENANT_A, post=post) == \
        "webhook_urlnotpublic"


# --- the inline SES send cannot outlive API Gateway ------------------------------------------------------------------

def test_ses_client_has_short_timeouts_and_is_cached(monkeypatch):
    import boto3

    made = []

    class Client:
        def __init__(self):
            self.sent = []

        def send_email(self, **message):
            self.sent.append(message)

    def client(name, region_name=None, config=None):
        made.append((name, config))
        return Client()

    monkeypatch.setattr(boto3, "client", client)
    monkeypatch.setattr(leads, "_ses_client", {})
    leads.ses_mailer({"a": 1})
    leads.ses_mailer({"a": 2})
    assert len(made) == 1 and made[0][0] == "sesv2"
    cfg = made[0][1]
    assert (cfg.connect_timeout, cfg.read_timeout, cfg.retries["max_attempts"]) == (2, 3, 1)


def test_tenant_cap_counts_the_tenant_only():
    assert "tenant_id = $1" in leads.TENANT_LEADS_24H_SQL and "24 hours" in leads.TENANT_LEADS_24H_SQL


def test_the_bound_tenant_is_unchanged_by_the_m2m_check():
    assert tenant.SOURCE_M2M == "m2m"
