"""AA-1038 R5 slice 3 (offline): lead.submitted.v1 from an agency tenant's planner.

The payload matches the vendored contract (tests/contracts/lead.submitted.v1.json, generated from AA-Ecosys
docs/contracts/lead.submitted.v1.openapi.yaml); the form answers are validated (consent first); the route refusals
store nothing; the redaction keeps ids only; the webhook signature, delivery outcomes and the e-mail; the M2M routes
refuse before any SQL; the bare path / demo tenant keeps its demo answer. Invented names only.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import Path

import jsonschema
import pytest

from backend import config
from backend.assembly import handler, leads
from backend.shared import m2m_auth, tenant

SCHEMA = json.loads((Path(__file__).parent / "contracts" / "lead.submitted.v1.json").read_text())
TENANT_A = "3e4f5a6b-7c8d-4e9f-a0b1-c2d3e4f5a6b7"
TRIP = "1b2c3d4e-5f6a-4b7c-8d9e-0f1a2b3c4d5e"
LEAD = "7a1c2e3f-4b5d-4e6f-8a9b-0c1d2e3f4a5b"
ROUTE = leads.Route(TENANT_A, "quillmere-voyages", "Quillmere Voyages", "webhook",
                    webhook_url="https://quillmere.example.invalid/hooks/leads")
GOOD = {"traveller": {"name": " Ada  Fernwick ", "email": "Ada@Example.invalid", "phone": "+84 90 000 0000",
                      "locale": "vi"},
        "party": {"adults": 2, "child_ages": [9]},
        "trip": {"start_date": "2027-03-10", "budget_band": "2000_3500", "message": "A slow pace, please."},
        "consent": {"contact": True, "marketing": False}}
SUMMARY = {"days": 2, "countries": ["Vietnam"],
           "tours": [{"source_system": "aa_master", "source_id": "SYN-TOUR-0042", "title": "Lantern Coast Explorer"}],
           "day_plan": [{"day": 1, "stops": ["Harbour Old Quarter"]}, {"day": 2, "stops": ["Jade Bay"]}]}
TODAY = date(2026, 11, 7)


def _lead(**kw):
    answers = leads.validate(GOOD, today=TODAY)
    return leads.build_lead(route=kw.get("route", ROUTE), trip_id=TRIP, answers=answers, summary=SUMMARY,
                            lead_id=LEAD, now=datetime(2026, 11, 7, 9, 0, tzinfo=timezone.utc))


def test_payload_matches_the_contract():
    payload = _lead()
    jsonschema.validate(payload, SCHEMA)
    assert payload["idempotency_key"] == f"tp-lead:{LEAD}" and payload["tenant"] == {"tenant_id": TENANT_A,
                                                                                       "slug": "quillmere-voyages"}
    assert payload["traveller"] == {"name": "Ada  Fernwick", "email": "ada@example.invalid",
                                    "phone": "+84 90 000 0000", "locale": "vi"}
    assert payload["consent"]["contact"] is True and payload["consent"]["marketing"] is False


def test_a_minimal_lead_matches_the_contract():
    answers = leads.validate({"traveller": {"name": "Bo", "email": "bo@example.invalid"}, "consent": {"contact": True}},
                             today=TODAY)
    payload = leads.build_lead(route=ROUTE, trip_id=TRIP, answers=answers,
                               summary={"days": 1, "countries": ["Not specified"], "tours": [], "day_plan": []})
    jsonschema.validate(payload, SCHEMA)
    assert payload["party"] == {"adults": 1, "child_ages": []} and payload["trip"]["start_date"] is None


def test_the_lead_never_carries_a_session_token_ip_or_price():
    text = json.dumps(_lead()).lower()
    for word in ("session", "token", "user_agent", "price", "\"ip\""):
        assert word not in text, word


@pytest.mark.parametrize("raw,code,fields", [
    ({**GOOD, "consent": {"contact": False}}, "consent_required", []),
    ({**GOOD, "consent": {}}, "consent_required", []),
    ("not an object", "invalid_lead", ["lead"]),
    ({**GOOD, "traveller": {**GOOD["traveller"], "email": "not-an-address"}}, "invalid_lead", ["email"]),
    ({**GOOD, "traveller": {**GOOD["traveller"], "name": "  "}}, "invalid_lead", ["name"]),
    ({**GOOD, "party": {"adults": 0}}, "invalid_lead", ["adults"]),
    ({**GOOD, "party": {"adults": 2, "child_ages": [18]}}, "invalid_lead", ["child_ages"]),
    ({**GOOD, "trip": {"start_date": "2026-11-01"}}, "invalid_lead", ["start_date"]),
    ({**GOOD, "trip": {"budget_band": "a lot"}}, "invalid_lead", ["budget_band"]),
    ({**GOOD, "trip": {"message": "x" * 1001}}, "invalid_lead", ["message"]),
    ({**GOOD, "consent": {"contact": True, "marketing": "yes"}}, "invalid_lead", ["marketing"]),
    # The contract pins E.164 with its country code: the CRM cannot place a local number without a region.
    ({**GOOD, "traveller": {**GOOD["traveller"], "phone": "090 000 0000"}}, "invalid_lead", ["phone"]),
    ({**GOOD, "traveller": {**GOOD["traveller"], "phone": "+0 90 000 0000"}}, "invalid_lead", ["phone"]),
    ({**GOOD, "traveller": {**GOOD["traveller"], "phone": "+84.90.000.0000"}}, "invalid_lead", ["phone"]),
])
def test_validation_refuses(raw, code, fields):
    with pytest.raises(leads.LeadRefused) as e:
        leads.validate(raw, today=TODAY)
    assert (e.value.status, e.value.code, e.value.fields) == (422, code, fields)


@pytest.mark.parametrize("phone", ["+84 90 000 0000", "+44 (20) 7946-0000", "+1-202-555-0100", "+8490000000"])
def test_phones_with_a_country_code_pass_and_match_the_contract(phone):
    answers = leads.validate({**GOOD, "traveller": {**GOOD["traveller"], "phone": phone}}, today=TODAY)
    assert answers["phone"] == phone
    payload = leads.build_lead(route=ROUTE, trip_id=TRIP, answers=answers, summary=SUMMARY)
    jsonschema.validate(payload, SCHEMA)


def test_a_local_phone_is_refused_by_the_contract_too():
    payload = _lead()
    payload["traveller"]["phone"] = "090 000 0000"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, SCHEMA)


def test_redaction_keeps_ids_only():
    out = leads.redacted_payload(_lead())
    assert out == {"contract": "lead.submitted.v1", "version": "1.0", "lead_id": LEAD,
                   "idempotency_key": f"tp-lead:{LEAD}", "submitted_at": "2026-11-07T09:00:00Z",
                   "tenant": {"tenant_id": TENANT_A, "slug": "quillmere-voyages"}, "redacted": True}
    assert leads.redacted_payload("not json") == {"redacted": True}


class RouteConn:
    def __init__(self, row=None, error=None):
        self.row, self.error = row, error

    async def fetchrow(self, sql, *args):
        if self.error:
            raise self.error
        return self.row


class PgError(Exception):
    def __init__(self, sqlstate):
        super().__init__("synthetic")
        self.sqlstate = sqlstate


def _route_row(**over):
    row = {"tenant_id": TENANT_A, "slug": "quillmere-voyages", "name": "Quillmere Voyages", "kind": "agency",
           "served": True, "in_service": True, "crm_core": False, "lead_channel": "email",
           "lead_webhook_url": None, "lead_email": "desk@quillmere.example.invalid"}
    row.update(over)
    return row


@pytest.mark.asyncio
@pytest.mark.parametrize("conn,status,code", [
    (RouteConn(error=PgError("42703")), 503, "lead_route_unavailable"),     # before CIS 390
    (RouteConn(error=PgError("42P01")), 503, "lead_route_unavailable"),
    (RouteConn(row=None), 404, "unknown_tenant"),
    (RouteConn(row=_route_row(served=False)), 404, "unknown_tenant"),
    (RouteConn(row=_route_row(lead_channel="none", lead_email=None)), 409, "lead_route_missing"),
])
async def test_route_refusals(conn, status, code):
    with pytest.raises(leads.LeadRefused) as e:
        await leads.load_route(conn, TENANT_A)
    assert (e.value.status, e.value.code) == (status, code)


@pytest.mark.asyncio
async def test_route_crm_and_email(monkeypatch):
    crm = await leads.load_route(RouteConn(row=_route_row(crm_core=True, lead_channel="crm", lead_email=None)),
                                 TENANT_A)
    assert crm.channel == "crm"
    monkeypatch.setattr(config, "LEAD_CRM_PULL_ENABLED", False)
    assert leads.channel_ready(crm) is False                      # CRM pull not switched on: refused, nothing stored
    monkeypatch.setattr(config, "LEAD_CRM_PULL_ENABLED", True)
    assert leads.channel_ready(crm) is True
    mail = await leads.load_route(RouteConn(row=_route_row()), TENANT_A)
    assert mail.email == "desk@quillmere.example.invalid"
    monkeypatch.setattr(config, "TP_LEAD_EMAIL_FROM", "")
    assert leads.channel_ready(mail) is False                     # no sender: refused, nothing stored
    monkeypatch.setattr(config, "TP_LEAD_EMAIL_FROM", "planner@example.invalid")
    assert leads.channel_ready(mail) is True


def test_webhook_not_ready_without_a_key(monkeypatch):
    monkeypatch.setattr(config, "TP_LEAD_WEBHOOK_KEY", "")
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    assert leads.channel_ready(ROUTE) is False
    monkeypatch.setattr(config, "TP_LEAD_WEBHOOK_KEY", "synthetic-master")
    assert leads.channel_ready(ROUTE) is True


def test_signature_vector():
    key = leads.webhook_key(b"synthetic-master", TENANT_A)
    assert key == hmac.new(b"synthetic-master", b"aa-tripplanner lead webhook v1:" + TENANT_A.encode(),
                           hashlib.sha256).hexdigest().encode()
    sig = leads.signature(key, b'{"a":1}', 1700000000)
    expected = hmac.new(key, b'1700000000.{"a":1}', hashlib.sha256).hexdigest()
    assert sig == f"t=1700000000,v1={expected}"


def test_webhook_delivery(monkeypatch):
    monkeypatch.setattr(config, "TP_LEAD_WEBHOOK_KEY", "synthetic-master")
    seen = []

    def post(url, body, headers, timeout):
        seen.append((url, body, headers, timeout))
        return 204

    payload = _lead()
    assert leads.deliver_webhook(ROUTE.webhook_url, payload, TENANT_A, post=post, now=1700000000) is None
    url, body, headers, _ = seen[0]
    assert json.loads(body) == payload and headers["X-AA-Contract"] == "lead.submitted.v1"
    assert headers["Idempotency-Key"] == f"tp-lead:{LEAD}"
    key = leads.webhook_key(b"synthetic-master", TENANT_A)
    assert headers["X-AA-Signature"] == leads.signature(key, body, 1700000000)
    assert leads.deliver_webhook(ROUTE.webhook_url, payload, TENANT_A, post=lambda *a: 500) == "webhook_http_500"

    def boom(*a):
        raise TimeoutError("synthetic")
    assert leads.deliver_webhook(ROUTE.webhook_url, payload, TENANT_A, post=boom) == "webhook_timeouterror"
    assert leads.deliver_webhook("http://plain.example.invalid", payload, TENANT_A, post=post) == \
        "webhook_url_not_https"
    monkeypatch.setattr(config, "TP_LEAD_WEBHOOK_KEY", "")
    assert leads.deliver_webhook(ROUTE.webhook_url, payload, TENANT_A, post=post) == "webhook_key_unavailable"


def test_email_delivery(monkeypatch):
    sent = []
    monkeypatch.setattr(config, "TP_LEAD_EMAIL_FROM", "planner@example.invalid")
    assert leads.deliver_email("desk@quillmere.example.invalid", _lead(), "Quillmere Voyages", send=sent.append) is None
    msg = sent[0]
    assert msg["Destination"] == {"ToAddresses": ["desk@quillmere.example.invalid"]}
    assert msg["ReplyToAddresses"] == ["ada@example.invalid"]
    text = msg["Content"]["Simple"]["Body"]["Text"]["Data"]
    assert "Quillmere Voyages" in text and "Lantern Coast Explorer" in text and "2,000 to 3,500" in text
    assert "A slow pace, please." in text and "Day 2: Jade Bay" in text

    def boom(_):
        raise RuntimeError("synthetic")
    assert leads.deliver_email("d@example.invalid", _lead(), "Q", send=boom) == "email_runtimeerror"
    monkeypatch.setattr(config, "TP_LEAD_EMAIL_FROM", "")
    assert leads.deliver_email("d@example.invalid", _lead(), "Q", send=sent.append) == "email_sender_unavailable"


@pytest.mark.asyncio
async def test_trip_summary_reads_tours_best_effort():
    itinerary = [{"day": 1, "name": "Kayak", "destination_name": "Jade Bay", "source_tour_id": "SYN-1"},
                 {"day": 3, "name": "Market", "destination_name": None, "source_tour_id": None}]

    class Conn:
        async def fetch(self, sql, keys):
            assert keys == ["SYN-1"]
            return [{"source_system": "aa_master", "source_id": "SYN-1", "tour_id": "x", "title": "Coast", "country":
                     "Vietnam"}]

    out = await leads.trip_summary(Conn(), itinerary, extra_days=1)
    assert out["days"] == 4 and out["countries"] == ["Vietnam"]
    assert out["day_plan"] == [{"day": 1, "stops": ["Jade Bay"]}, {"day": 3, "stops": ["Market"]}]

    class Broken:
        async def fetch(self, sql, keys):
            raise RuntimeError("synthetic")

    out = await leads.trip_summary(Broken(), itinerary)
    assert out["tours"] == [] and out["countries"] == ["Not specified"] and out["days"] == 3


def test_valid_ack():
    assert leads.valid_ack({"outcome": "stored"}) == ("stored", None)
    assert leads.valid_ack({"outcome": "rejected", "reason": "tenant_locked"}) == ("rejected", "tenant_locked")
    for bad in ({}, {"outcome": "maybe"}, {"outcome": "rejected", "reason": "Not A Code"}, []):
        with pytest.raises(ValueError):
            leads.valid_ack(bad)


# --- the M2M routes -------------------------------------------------------------------------------------------------

def _no_sql_acquire(used):
    @asynccontextmanager
    async def acquire():
        used.append("acquire")
        raise AssertionError("no connection may be taken")
        yield None  # pragma: no cover

    return acquire


@pytest.mark.asyncio
@pytest.mark.parametrize("refusal", [m2m_auth.Refused(401, "unauthorized"), m2m_auth.Refused(403, "forbidden"),
                                     m2m_auth.Refused(503, "unavailable")])
async def test_m2m_refused_before_any_sql(refusal):
    used: list = []

    async def verify(_auth):
        raise refusal

    out = await handler.serve_m2m("GET", f"/trip/m2m/v1/tenants/{TENANT_A}/leads", {}, "Bearer x",
                                  acquire=_no_sql_acquire(used), verify=verify)
    assert out["statusCode"] == refusal.status and json.loads(out["body"]) == {"error": refusal.code}
    assert used == []


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,body,status,code", [
    ("GET", "/trip/m2m/v1/tenants/not-a-uuid/leads", {}, 400, "invalid_tenant_id"),
    ("POST", f"/trip/m2m/v1/tenants/{TENANT_A}/leads/nope/ack", {"outcome": "stored"}, 400, "invalid_lead_id"),
    ("GET", f"/trip/m2m/v1/tenants/{TENANT_A}/leads", {"limit": "500"}, 400, "invalid_limit"),
    ("POST", f"/trip/m2m/v1/tenants/{TENANT_A}/leads/{LEAD}/ack", {"outcome": "x"}, 400, "invalid_ack"),
    ("DELETE", f"/trip/m2m/v1/tenants/{TENANT_A}/leads", {}, 404, "not_found"),
])
async def test_m2m_bad_requests_take_no_connection(method, path, body, status, code):
    used: list = []

    async def verify(_auth):
        return "crm-client"

    out = await handler.serve_m2m(method, path, body, "Bearer x", acquire=_no_sql_acquire(used), verify=verify)
    assert (out["statusCode"], json.loads(out["body"])) == (status, {"error": code}) and used == []


@pytest.mark.asyncio
async def test_m2m_list_binds_the_path_tenant_and_drops_other_tenants_rows():
    other = "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d"
    seen: dict = {}

    class Conn:
        async def execute(self, sql, *args):
            seen.setdefault("exec", []).append((sql, args))

        async def fetch(self, sql, tid, limit):
            seen["current"] = tenant.current()
            return [{"id": 1, "payload": json.dumps(_lead())},
                    {"id": 2, "payload": json.dumps({**_lead(), "tenant": {"tenant_id": other, "slug": "x"}})}]

    @asynccontextmanager
    async def acquire():
        yield Conn()

    async def verify(_auth):
        return "crm-client"

    out = await handler.serve_m2m("GET", f"/trip/m2m/v1/tenants/{TENANT_A}/leads", {"limit": "1"}, "Bearer x",
                                  acquire=acquire, verify=verify)
    body = json.loads(out["body"])
    assert out["statusCode"] == 200 and [p["lead_id"] for p in body["leads"]] == [LEAD] and body["more"] is True
    assert seen["current"] == tenant.Current(TENANT_A, tenant.SOURCE_M2M)
    assert seen["exec"][0] == ("SELECT set_config('app.tenant_id', $1, false)", (TENANT_A,))


@pytest.mark.asyncio
async def test_verify_bearer_needs_the_scope_and_a_client_credentials_token(monkeypatch):
    from backend.shared.identity_verifier import InvalidToken, Principal

    class Verifier:
        def __init__(self, principal=None, error=None):
            self.principal, self.error = principal, error

        async def verify(self, token):
            if self.error:
                raise self.error
            return self.principal

    ok = Principal(iss="i", sub="crm", client_id="crm", groups=("tripplanner.leads/deliver other/x",), email=None,
                   email_verified=None, username=None)
    try:
        m2m_auth.set_verifier(Verifier(ok))
        assert await m2m_auth.verify_bearer("Bearer t") == "crm"
        with pytest.raises(m2m_auth.Refused) as e:
            await m2m_auth.verify_bearer("Basic t")
        assert e.value.status == 401
        m2m_auth.set_verifier(Verifier(Principal(iss="i", sub="crm", client_id="crm", groups=("cis.tenant/read",),
                                                 email=None, email_verified=None, username=None)))
        with pytest.raises(m2m_auth.Refused) as e:
            await m2m_auth.verify_bearer("Bearer t")
        assert e.value.status == 403
        m2m_auth.set_verifier(Verifier(Principal(iss="i", sub="a-user", client_id="crm", groups=ok.groups,
                                                 email=None, email_verified=None, username=None)))
        with pytest.raises(m2m_auth.Refused) as e:
            await m2m_auth.verify_bearer("Bearer t")
        assert e.value.status == 403
        m2m_auth.set_verifier(Verifier(error=InvalidToken(m2m_auth.CLIENT_NOT_ALLOWED)))
        with pytest.raises(m2m_auth.Refused) as e:
            await m2m_auth.verify_bearer("Bearer t")
        assert (e.value.status, e.value.code) == (403, "forbidden")
        m2m_auth.set_verifier(Verifier(error=InvalidToken("expired")))
        with pytest.raises(m2m_auth.Refused) as e:
            await m2m_auth.verify_bearer("Bearer t")
        assert e.value.status == 401
    finally:
        m2m_auth.set_verifier(None)


def test_m2m_issuer_config_is_the_crm_client_with_the_scope_claim():
    cfg = m2m_auth.issuer_config()
    assert cfg.client_ids == frozenset({m2m_auth.DEV_M2M_CRM_CLIENT_ID}) and cfg.groups_claim == "scope"


# --- the demo tenant keeps its notice --------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_bare_path_keeps_the_demo_answer(monkeypatch):
    monkeypatch.setattr(config, "ADVISOR_HANDOFF_DEMO", True)
    out = await handler.route("POST", f"/trip/{TRIP}/send-to-advisor", {"session_id": TRIP}, conn=None)
    assert out["statusCode"] == 410 and json.loads(out["body"])["error"] == "b2b_only"
    # The demo tenant through an assertion is still the demo.
    with tenant.using(config.DEMO_TENANT_ID, source=tenant.SOURCE_ASSERTION):
        out = await handler.route("POST", f"/trip/{TRIP}/send-to-advisor", {"session_id": TRIP}, conn=None)
    assert out["statusCode"] == 410


@pytest.mark.asyncio
async def test_an_agency_tenant_goes_to_the_lead_flow(monkeypatch):
    called = {}

    async def fake_send(conn, trip_id, body, guest_token, caller, tenant_id, **kw):
        called.update(trip_id=trip_id, tenant_id=tenant_id)
        return handler._resp(200, {"ok": True})  # noqa: SLF001

    monkeypatch.setattr(handler, "_send_lead", fake_send)
    with tenant.using(TENANT_A, source=tenant.SOURCE_ASSERTION):
        out = await handler.route("POST", f"/trip/{TRIP}/send-to-advisor", {"session_id": TRIP}, conn=None)
    assert out["statusCode"] == 200 and called == {"trip_id": TRIP, "tenant_id": TENANT_A}
