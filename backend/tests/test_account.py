"""AA-908 — traveller accounts: the access-token caller, the claim, My Trips and the account-aware guards.

Offline: FakeConn from test_assembly plus the tp-019 account SQL (AccountConn), and a local RSA issuer from
test_identity_verifier for the header -> caller step. test_account_pg.py runs the same SQL on a real Postgres.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from backend import config
from backend.assembly import account, guest_session, handler, notify
from backend.shared import identity_config
from backend.shared.identity_verifier import IssuerConfig, MultiIssuerVerifier, Principal
from backend.tests.test_assembly import CATALOG, OWNER, T1, T9, TOKEN, FakeConn, _session
from backend.tests.test_identity_verifier import FakeFetcher, LocalIssuer

ISS = "https://cognito-idp.us-west-1.amazonaws.com/us-west-1_TESTPOOLX"
TP_CLIENT = "tripplannerclient000000001"
SUB_A = "aaaaaaaa-0000-4000-8000-00000000000a"
SUB_B = "bbbbbbbb-0000-4000-8000-00000000000b"
OTHER_TOKEN = "Zz9_-aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789ab"   # another browser's guest cookie
SECOND = "00000000-0000-4000-8000-0000000000c2"             # a session of another browser


def _principal(sub=SUB_A, email="traveller@example.com", verified=True) -> Principal:
    return Principal(iss=ISS, sub=sub, client_id=TP_CLIENT, groups=(), email=email, email_verified=verified,
                     username=sub)


def signed_in(sub=SUB_A, **kw) -> account.Caller:
    return account.Caller(account.OK, _principal(sub, **kw))


def _n(q: str) -> str:
    return " ".join(q.split())


class AccountConn(FakeConn):
    """FakeConn plus tripplanner.traveller_accounts and sessions.account_id (tp-019), matched on the exact SQL."""

    def __init__(self):
        super().__init__()
        self.accounts: dict[tuple[str, str], dict] = {}

    def _account_by_id(self, acc_id):
        return next((a for a in self.accounts.values() if a["id"] == acc_id), None)

    def _owner_key(self, session_id):
        row = self.sessions.get(session_id) or {}
        acc = self._account_by_id(row.get("account_id"))
        return (acc["iss"], acc["sub"]) if acc else None

    def _new_account(self, iss, sub, signed=False):
        acc = {"id": f"acc-{len(self.accounts) + 1}", "iss": iss, "sub": sub, "last_sign_in_at": None}
        if signed:
            acc["last_sign_in_at"] = "now"
        self.accounts[(iss, sub)] = acc
        return acc

    async def execute(self, query, *args):
        q = _n(query)
        if q == _n(account.CLAIM_SQL):
            self.sql.append(q)
            acc_id, digest = args
            hit = [r for r in self.sessions.values()
                   if r.get("guest_token_hash") == digest and r.get("account_id") is None]
            for r in hit:
                r["account_id"] = acc_id
            return f"UPDATE {len(hit)}"
        if q == _n(account.LINK_SQL):
            self.sql.append(q)
            acc_id, session_id = args
            row = self.sessions.get(session_id)
            if row is not None and row.get("account_id") is None:
                row["account_id"] = acc_id
                return "UPDATE 1"
            return "UPDATE 0"
        return await super().execute(query, *args)

    async def fetchrow(self, query, *args):
        q = _n(query)
        if q == _n(account.FIND_SQL):
            self.sql.append(q)
            acc = self.accounts.get(args)
            return {"id": acc["id"]} if acc else None
        if q == _n(account.INSERT_SQL):
            self.sql.append(q)
            if args in self.accounts:
                return None
            return {"id": self._new_account(*args)["id"]}
        if q == _n(account.SIGN_IN_SQL):
            self.sql.append(q)
            acc = self.accounts.get(args) or self._new_account(*args)
            acc["last_sign_in_at"] = "now"
            return {"id": acc["id"]}
        if q == _n(account.OWNS_SESSION_SQL):
            self.sql.append(q)
            session_id, iss, sub = args
            return {"?column?": 1} if self._owner_key(session_id) == (iss, sub) else None
        if q == _n(account.OWNS_TRIP_SQL):
            self.sql.append(q)
            trip_id, iss, sub = args
            d = self.drafts.get(trip_id)
            return {"?column?": 1} if d and self._owner_key(d["session_id"]) == (iss, sub) else None
        return await super().fetchrow(query, *args)

    async def fetch(self, query, *args):
        q = _n(query)
        if q == _n(account.LIST_SQL):
            self.sql.append(q)
            iss, sub, limit = args
            rows = [{"trip_id": d["id"], "session_id": d["session_id"], "status": d["status"],
                     "itinerary": d["itinerary"], "date_start": None, "date_end": None,
                     "created_at": None, "updated_at": None}
                    for d in self.drafts.values() if self._owner_key(d["session_id"]) == (iss, sub)]
            return rows[:limit]
        return await super().fetch(query, *args)


async def _pin(conn, session_id, token, trip, caller=account.GUEST, component="c1"):
    return await handler.route("POST", f"/trip/{trip}/components",
                               {"session_id": session_id, "component_id": component},
                               conn=conn, guest_token=token, caller=caller)


async def _me(conn, method, path, caller, token=None):
    return await handler.route(method, path, {}, conn=conn, guest_token=token, caller=caller)


def _body(resp):
    return json.loads(resp["body"])


# --- header -> caller -------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def issuer():
    return LocalIssuer(ISS, kid="tp1")


def _cfg() -> IssuerConfig:
    """The shape of identity_config.traveller_issuer() for the local test issuer."""
    return IssuerConfig(issuer=ISS, client_ids=frozenset({TP_CLIENT}))


def _verifier(issuer):
    return MultiIssuerVerifier([_cfg()], FakeFetcher(issuer))


async def test_a_valid_access_token_is_a_signed_in_caller(issuer):
    token = issuer.token(sub=SUB_A, client_id=TP_CLIENT, email="traveller@example.com", email_verified="true")
    caller = await account.caller_from_header(f"Bearer {token}", _verifier(issuer))
    assert caller.signed_in and caller.principal.sub == SUB_A and caller.principal.iss == ISS
    assert caller.principal.email_verified is True
    assert (await account.caller_from_header(token, _verifier(issuer))).signed_in   # bare token too


@pytest.mark.parametrize("value", [None, "", "   "])
async def test_no_header_is_a_guest(issuer, value):
    assert await account.caller_from_header(value, _verifier(issuer)) == account.GUEST


@pytest.mark.parametrize("claims", [
    {"client_id": "bookingcustomerclient00001"},    # another client of the EXTERNAL pool
    {"token_use": "id"},                             # an ID token
    {"exp": 1},                                      # expired
], ids=["other_client", "id_token", "expired"])
async def test_an_unacceptable_token_is_invalid_not_a_guest(issuer, claims, capsys):
    token = issuer.token(sub=SUB_A, **{"client_id": TP_CLIENT, **claims})
    caller = await account.caller_from_header(f"Bearer {token}", _verifier(issuer))
    assert caller.state == account.INVALID and not caller.signed_in
    out = capsys.readouterr().out
    assert SUB_A not in out and token not in out


async def test_no_key_set_is_unavailable(issuer):
    fetcher = FakeFetcher(issuer)
    fetcher.fail = True
    token = issuer.token(sub=SUB_A, client_id=TP_CLIENT)
    caller = await account.caller_from_header(token, MultiIssuerVerifier([_cfg()], fetcher))
    assert caller.state == account.UNAVAILABLE


def test_the_traveller_issuer_is_external_x_tripplanner_only(monkeypatch):
    monkeypatch.delenv(identity_config.ENV_ISSUER, raising=False)
    monkeypatch.delenv(identity_config.ENV_CLIENT_ID, raising=False)
    cfg = identity_config.traveller_issuer()
    assert cfg.issuer == identity_config.DEV_TRAVELLER_ISSUER
    assert cfg.client_ids == frozenset({identity_config.DEV_TRAVELLER_CLIENT_ID})
    assert cfg.token_use == "access" and cfg.audience_claim == "client_id"
    monkeypatch.setenv(identity_config.ENV_ISSUER, ISS)
    monkeypatch.setenv(identity_config.ENV_CLIENT_ID, TP_CLIENT)
    cfg = identity_config.traveller_issuer()
    assert (cfg.issuer, cfg.client_ids) == (ISS, frozenset({TP_CLIENT}))


async def test_serve_verifies_the_header_before_routing(issuer, monkeypatch):
    identity_config.set_verifier(_verifier(issuer))
    try:
        conn = AccountConn()

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *a):
                return False

        token = issuer.token(sub=SUB_A, client_id=TP_CLIENT)
        ok = await handler.serve("GET", "/me/trips", {}, access_token=f"Bearer {token}", acquire=lambda: _Ctx())
        assert ok["statusCode"] == 200 and _body(ok) == {"trips": []}
        bad = await handler.serve("GET", "/me/trips", {}, access_token="Bearer x.y.z", acquire=lambda: _Ctx())
        assert bad["statusCode"] == 401 and _body(bad) == {"error": "sign_in_required"}
    finally:
        identity_config.set_verifier(None)


# --- /me routes -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("method, path", [("POST", "/me/claim"), ("GET", "/me/trips")])
@pytest.mark.parametrize("caller", [account.GUEST, account.Caller(account.INVALID)], ids=["guest", "invalid"])
async def test_me_routes_need_a_signed_in_caller(method, path, caller):
    conn = AccountConn()
    resp = await _me(conn, method, path, caller, token=TOKEN)
    assert resp["statusCode"] == 401 and _body(resp) == {"error": "sign_in_required"}
    assert conn.accounts == {} and not any(q.startswith(("INSERT", "UPDATE")) for q in conn.sql)


@pytest.mark.parametrize("method, path", [("POST", "/me/claim"), ("GET", "/me/trips")])
async def test_me_routes_answer_503_when_no_key_set_was_ever_loaded(method, path):
    resp = await _me(AccountConn(), method, path, account.Caller(account.UNAVAILABLE))
    assert resp["statusCode"] == 503 and _body(resp) == {"error": "auth_unavailable"}


@pytest.mark.parametrize("method, path", [("GET", "/me/claim"), ("POST", "/me/trips"), ("DELETE", "/me/trips")])
async def test_me_routes_with_the_wrong_method_are_the_unknown_route(method, path):
    assert await _me(AccountConn(), method, path, signed_in()) == handler._not_found()  # noqa: SLF001


async def test_claim_links_only_the_sessions_of_this_guest_token():
    conn = AccountConn()
    await _pin(conn, OWNER, TOKEN, T1)                     # this browser's guest trip
    await _pin(conn, SECOND, OTHER_TOKEN, T9)              # another browser's
    resp = await _me(conn, "POST", "/me/claim", signed_in(), token=TOKEN)
    assert resp["statusCode"] == 200 and _body(resp) == {"claimed": 1}
    acc = conn.accounts[(ISS, SUB_A)]
    assert conn.sessions[OWNER]["account_id"] == acc["id"] and acc["last_sign_in_at"] == "now"
    assert conn.sessions[SECOND].get("account_id") is None


async def test_claiming_twice_is_idempotent_and_never_moves_an_owned_session():
    conn = AccountConn()
    await _pin(conn, OWNER, TOKEN, T1)
    assert _body(await _me(conn, "POST", "/me/claim", signed_in(), token=TOKEN)) == {"claimed": 1}
    assert _body(await _me(conn, "POST", "/me/claim", signed_in(), token=TOKEN)) == {"claimed": 0}
    # The same guest cookie replayed by another account claims nothing: the session stays A's.
    assert _body(await _me(conn, "POST", "/me/claim", signed_in(SUB_B), token=TOKEN)) == {"claimed": 0}
    assert conn.sessions[OWNER]["account_id"] == conn.accounts[(ISS, SUB_A)]["id"]


@pytest.mark.parametrize("token", [None, "", "short", "A" * 43])
async def test_claim_without_a_usable_guest_token_only_signs_in(token):
    conn = AccountConn()
    await _pin(conn, OWNER, TOKEN, T1)
    assert _body(await _me(conn, "POST", "/me/claim", signed_in(), token=token)) == {"claimed": 0}
    assert (ISS, SUB_A) in conn.accounts and conn.sessions[OWNER].get("account_id") is None
    assert not any("short" in q for q in conn.sql)


async def test_my_trips_lists_only_the_callers_trips():
    conn = AccountConn()
    await _pin(conn, OWNER, TOKEN, T1)
    await _pin(conn, SECOND, OTHER_TOKEN, T9)
    await _me(conn, "POST", "/me/claim", signed_in(SUB_A), token=TOKEN)
    await _me(conn, "POST", "/me/claim", signed_in(SUB_B), token=OTHER_TOKEN)
    a = _body(await _me(conn, "GET", "/me/trips", signed_in(SUB_A)))["trips"]
    b = _body(await _me(conn, "GET", "/me/trips", signed_in(SUB_B)))["trips"]
    assert [(t["trip_id"], t["session_id"]) for t in a] == [(T1, OWNER)]
    assert [(t["trip_id"], t["session_id"]) for t in b] == [(T9, SECOND)]
    assert a[0]["status"] == "draft" and a[0]["stops"] == 1 and a[0]["stop_names"] == [CATALOG["c1"]["name"]]
    assert _body(await _me(conn, "GET", "/me/trips", signed_in("cccccccc-0000-4000-8000-00000000000c"))) == {
        "trips": []}


def test_trip_summary_counts_served_stops_days_and_names():
    itinerary = [
        {"day": 2, "component_id": "x2", "name": "Kayak", "destination_name": "Ha Long", "available": True},
        {"day": 1, "component_id": "x1", "name": "Old Quarter", "destination_name": "Hanoi", "available": True},
        {"day": 1, "component_id": "x3", "name": "Street food", "destination_name": "Hanoi", "available": True},
        {"day": 3, "component_id": "x4", "name": "Gone", "destination_name": "Hue", "available": False},
    ]
    row = {"trip_id": T1, "session_id": OWNER, "status": "sent", "itinerary": json.dumps(itinerary),
           "date_start": None, "date_end": None, "created_at": None, "updated_at": None}
    s = account.trip_summary(row)
    assert (s["stops"], s["unavailable_stops"], s["days"]) == (3, 1, 2)
    assert s["stop_names"] == ["Hanoi", "Ha Long"] and s["status"] == "sent"


# --- account-aware guards ----------------------------------------------------------------------------------------

async def _claimed_trip() -> AccountConn:
    """Browser 1 (TOKEN) builds T1 as a guest, then signs in as A: the session is A's."""
    conn = AccountConn()
    await _pin(conn, OWNER, TOKEN, T1)
    await _me(conn, "POST", "/me/claim", signed_in(), token=TOKEN)
    return conn


async def test_the_owner_account_opens_the_trip_on_another_device():
    conn = await _claimed_trip()
    # Device 2 has its own guest cookie (OTHER_TOKEN) and the ids from My Trips.
    read = await handler.route("GET", f"/trip/{T1}", {}, conn=conn, guest_token=OTHER_TOKEN, caller=signed_in())
    assert read["statusCode"] == 200 and _body(read)["trip_id"] == T1
    edit = await _pin(conn, OWNER, OTHER_TOKEN, T1, caller=signed_in(), component="c2")
    assert edit["statusCode"] == 200, edit["body"]
    assert len(conn.events) == 2


@pytest.mark.parametrize("caller", [account.GUEST, account.Caller(account.INVALID)], ids=["guest", "bad_token"])
async def test_without_the_account_the_other_device_gets_the_guest_answers(caller):
    conn = await _claimed_trip()
    read = await handler.route("GET", f"/trip/{T1}", {}, conn=conn, guest_token=OTHER_TOKEN, caller=caller)
    assert read == handler._not_found()  # noqa: SLF001
    edit = await _pin(conn, OWNER, OTHER_TOKEN, T1, caller=caller, component="c2")
    assert edit["statusCode"] == 401 and _body(edit) == {"error": "guest_session_invalid"}


async def test_another_account_gets_the_same_404_and_401():
    conn = await _claimed_trip()
    other = signed_in(SUB_B)
    for path in (f"/trip/{T1}", f"/trip/{T1}/coverage", f"/trip/{T1}/suggestions"):
        resp = await handler.route("GET", path, {}, conn=conn, guest_token=OTHER_TOKEN, caller=other)
        assert resp == handler._not_found()  # noqa: SLF001
    edit = await _pin(conn, OWNER, OTHER_TOKEN, T1, caller=other, component="c2")
    assert edit["statusCode"] == 401


async def test_the_account_cannot_write_through_another_session_of_the_trip():
    """The owner check (403) still holds: the account owns OWNER, but T9 belongs to another session."""
    conn = await _claimed_trip()
    await _pin(conn, SECOND, OTHER_TOKEN, T9)
    resp = await _pin(conn, OWNER, OTHER_TOKEN, T9, caller=signed_in(), component="c2")
    assert resp["statusCode"] == 403


async def test_a_signed_in_mutation_links_its_session_once():
    conn = AccountConn()
    await _pin(conn, OWNER, TOKEN, T1, caller=signed_in())      # a trip started after sign-in
    acc = conn.accounts[(ISS, SUB_A)]
    assert conn.sessions[OWNER]["account_id"] == acc["id"] and acc["last_sign_in_at"] is None
    # Another account using the same browser later never takes it over.
    await _pin(conn, OWNER, TOKEN, T1, caller=signed_in(SUB_B), component="c2")
    assert conn.sessions[OWNER]["account_id"] == acc["id"]


async def test_a_guest_mutation_touches_no_account_sql():
    conn = AccountConn()
    await _pin(conn, OWNER, TOKEN, T1)
    assert not any("traveller_accounts" in q or "account_id" in q for q in conn.sql)


async def test_guard_log_lines_name_the_outcome_and_never_the_identity(capsys):
    conn = await _claimed_trip()
    capsys.readouterr()
    await handler.route("GET", f"/trip/{T1}", {}, conn=conn, guest_token=OTHER_TOKEN, caller=signed_in())
    await _pin(conn, OWNER, OTHER_TOKEN, T1, caller=signed_in(), component="c2")
    await _me(conn, "GET", "/me/trips", signed_in())
    out = capsys.readouterr().out
    lines = [json.loads(x) for x in out.splitlines() if x.startswith("{")]
    outcomes = {x.get("outcome") for x in lines if x.get("evt") == "guest_auth"}
    assert {guest_session.ACCOUNT_READ_OK, guest_session.ACCOUNT_OK} <= outcomes
    for secret in (SUB_A, ISS, "traveller@example.com", TOKEN, OTHER_TOKEN, OWNER, T1,
                   hashlib.sha256(TOKEN.encode()).hexdigest()):
        assert secret not in out


# --- signed-in hand-off (v1.4) -----------------------------------------------------------------------------------

async def _send(conn, caller, customer=None, token=TOKEN):
    body = {"session_id": OWNER, **({"customer": customer} if customer is not None else {})}
    return await handler.route("POST", f"/trip/{T1}/send-to-advisor", body, conn=conn, guest_token=token,
                               caller=caller, sender=notify.LoggingSender())


async def test_signed_in_send_hands_off_the_token_email_and_identity(monkeypatch):
    monkeypatch.setattr(config, "REQUIRE_REGISTRATION_BEFORE_HANDOFF", True)
    conn = await _claimed_trip()
    resp = await _send(conn, signed_in(), {"name": "Jo", "email": "typed@elsewhere.com", "consent": True})
    assert resp["statusCode"] == 200, resp["body"]
    c = conn.outbox[0]["payload"]["customer"]
    assert conn.outbox[0]["payload"]["version"] == 1.4
    assert c["email"] == "traveller@example.com" and c["verified"] is True    # the token's, never the typed one
    assert c["identity"] == {"iss": ISS, "sub": SUB_A} and c["customer_id"] is None and c["consent"] is True
    assert conn.customers == []                                                # no tripplanner.customers row


async def test_signed_in_send_without_a_name_asks_for_registration(monkeypatch):
    monkeypatch.setattr(config, "REQUIRE_REGISTRATION_BEFORE_HANDOFF", True)
    conn = await _claimed_trip()
    resp = await _send(conn, signed_in())
    assert resp["statusCode"] == 422 and _body(resp)["error"] == "registration_required"
    assert conn.outbox == []


async def test_signed_in_send_reuses_the_sessions_registration(monkeypatch):
    monkeypatch.setattr(config, "REQUIRE_REGISTRATION_BEFORE_HANDOFF", True)
    conn = await _claimed_trip()
    conn.customers.append({"id": "cust-1", "name": "Jo Guest", "phone": "+84 9", "email": "old@typed.com",
                           "contact_consent": True, "contact_consent_at": "2026-10-01T00:00:00+00:00"})
    conn.sessions[OWNER]["customer_id"] = "cust-1"
    assert (await _send(conn, signed_in()))["statusCode"] == 200
    c = conn.outbox[0]["payload"]["customer"]
    assert (c["name"], c["phone"], c["email"], c["consent"]) == ("Jo Guest", "+84 9", "traveller@example.com", True)


async def test_guest_send_is_unchanged_with_a_null_identity(monkeypatch):
    monkeypatch.setattr(config, "REQUIRE_REGISTRATION_BEFORE_HANDOFF", True)
    conn = AccountConn()
    conn.sessions[OWNER] = _session(OWNER)
    await _pin(conn, OWNER, TOKEN, T1)
    assert (await _send(conn, account.GUEST, {"name": "Jo", "email": "jo@x.com", "consent": True}))[
        "statusCode"] == 200
    c = conn.outbox[0]["payload"]["customer"]
    assert c["verified"] is False and c["identity"] is None and c["email"] == "jo@x.com"
