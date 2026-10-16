"""AA-827: guest token in an HttpOnly BFF cookie, stored as sha256 only, checked on every mutation.

Offline (FakeConn from test_assembly). The legacy grace window is closed on Dev since S239
(`config.GUEST_LEGACY_GRACE_UNTIL` is in the past), so the post-cutoff behaviour is the default and
those tests run on the shipped constant, unpatched (`grace_closed` only asserts it is in the past).
The dormant grace code path is still covered by the `in_grace` tests, which move the cutoff into the
future.
"""
from __future__ import annotations

import hashlib
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest

from backend import config
from backend.assembly import events, guest_session, handler, notify
from backend.tests.test_assembly import FakeConn, INTRUDER, OWNER, S1, T1, TOKEN, _session

OTHER_TOKEN = "Zz9_-aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789ab"
NOW = datetime.now(timezone.utc)


class RecordingConn(FakeConn):
    """FakeConn that also keeps every (statement, args) pair, to prove what reaches SQL."""

    def __init__(self):
        super().__init__()
        self.calls: list[tuple[str, tuple]] = []

    async def execute(self, query, *args):
        self.calls.append((query, args))
        return await super().execute(query, *args)

    async def fetch(self, query, *args):
        self.calls.append((query, args))
        return await super().fetch(query, *args)

    async def fetchrow(self, query, *args):
        self.calls.append((query, args))
        return await super().fetchrow(query, *args)


@pytest.fixture
def in_grace(monkeypatch):
    """The dormant grace code path: move the cutoff into the future."""
    monkeypatch.setattr(config, "GUEST_LEGACY_GRACE_UNTIL", NOW + timedelta(days=1))


@pytest.fixture
def grace_closed():
    """The shipped default. Nothing is patched: the real cutoff must already be in the past."""
    assert not guest_session.in_grace(datetime.now(timezone.utc))


async def _add(conn, session_id=S1, token=TOKEN, component="c1", trip=T1):
    return await handler.route("POST", f"/trip/{trip}/components",
                               {"session_id": session_id, "component_id": component},
                               conn=conn, guest_token=token)


def _err(resp) -> str:
    return json.loads(resp["body"]).get("error")


def test_grace_cutoff_is_a_timezone_aware_constant():
    assert config.GUEST_LEGACY_GRACE_UNTIL.tzinfo is not None
    assert config.GUEST_TOKEN_HEADER == "x-tripplanner-guest"


def test_the_shipped_grace_window_is_already_closed():
    """S239: Dev has no real guests, so the window was closed instead of waiting until 19/10."""
    assert config.GUEST_LEGACY_GRACE_UNTIL == datetime(2026, 10, 16, tzinfo=timezone.utc)
    assert config.GUEST_LEGACY_GRACE_UNTIL <= datetime.now(timezone.utc)
    assert not guest_session.in_grace(datetime.now(timezone.utc))


# --- token-bound sessions ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_new_session_stores_only_the_sha256_of_the_token(grace_closed):
    conn = RecordingConn()
    resp = await _add(conn)
    assert resp["statusCode"] == 200
    stored = conn.sessions[S1]["guest_token_hash"]
    assert stored == hashlib.sha256(TOKEN.encode()).digest() and len(stored) == 32
    assert "guest_token" not in conn.sessions[S1]                       # plaintext never written
    assert TOKEN not in repr(conn.calls) and TOKEN not in repr(conn.__dict__)


@pytest.mark.asyncio
async def test_same_token_passes_again_and_a_wrong_token_gets_401(grace_closed):
    conn = FakeConn()
    assert (await _add(conn))["statusCode"] == 200
    assert (await _add(conn, component="c2"))["statusCode"] == 200
    wrong = await _add(conn, token=OTHER_TOKEN, component="c3")
    assert wrong["statusCode"] == 401 and _err(wrong) == "guest_session_invalid"
    assert [e["payload"] for e in conn.events if "c3" in e["payload"]] == []


@pytest.mark.parametrize("bad", ["", "short", TOKEN + "x", TOKEN[:-1] + "=", TOKEN + "\n", S1])
@pytest.mark.asyncio
async def test_a_malformed_token_gets_401_before_any_sql(bad, in_grace):
    conn = FakeConn()
    resp = await _add(conn, token=bad)
    assert resp["statusCode"] == 401 and _err(resp) == "guest_session_invalid"
    assert conn.sql == []


# --- legacy sessions and the grace window -----------------------------------------------------

@pytest.mark.asyncio
async def test_legacy_session_binds_to_its_first_token_inside_grace(in_grace):
    conn = FakeConn()
    conn.sessions[S1] = _session(S1, token=None)                        # pre-AA-827 row, hash NULL
    assert (await _add(conn))["statusCode"] == 200
    assert conn.sessions[S1]["guest_token_hash"] == guest_session.hash_token(TOKEN)
    second = await _add(conn, token=OTHER_TOKEN, component="c2")
    assert second["statusCode"] == 401


@pytest.mark.asyncio
async def test_tokenless_works_inside_grace_until_the_session_is_bound(in_grace):
    conn = FakeConn()
    assert (await _add(conn, token=None))["statusCode"] == 200          # old BFF / old bundle
    assert conn.sessions[S1]["guest_token_hash"] is None
    assert (await _add(conn, component="c2"))["statusCode"] == 200      # new BFF binds it
    downgrade = await _add(conn, token=None, component="c3")
    assert downgrade["statusCode"] == 401 and _err(downgrade) == "guest_session_invalid"


@pytest.mark.asyncio
async def test_after_the_cutoff_the_old_id_alone_never_passes(grace_closed):
    """Done-when: after GUEST_LEGACY_GRACE_UNTIL (the shipped default since S239) a legacy session,
    a tokenless request and the session id sent as the token all get 401."""
    conn = FakeConn()
    conn.sessions[S1] = _session(S1, token=None)
    for token in (TOKEN, None, S1):
        resp = await _add(conn, token=token)
        assert resp["statusCode"] == 401, token
        assert _err(resp) == "guest_session_invalid"
    assert conn.sessions[S1]["guest_token_hash"] is None and conn.events == []
    # a brand-new session with a real token still works after the cutoff
    other = "00000000-0000-4000-8000-0000000000c1"
    trip = "00000000-0000-4000-8000-0000000000c2"
    assert (await _add(conn, session_id=other, trip=trip))["statusCode"] == 200


@pytest.mark.asyncio
async def test_tokenless_for_a_session_that_does_not_exist_yet_is_refused_after_the_cutoff(grace_closed):
    conn = FakeConn()
    assert (await _add(conn, token=None))["statusCode"] == 401
    assert conn.sessions == {}


# --- owner check on every mutation --------------------------------------------------------------

DAY = {"kind": "extend_end"}

# name -> (method, path suffix, body without session_id, extra route kwargs)
OPS = {
    "add": ("POST", "components", {"component_id": "c2"}, {}),
    "remove": ("DELETE", "components/c1", {}, {}),
    "reorder": ("PATCH", "reorder", {"ordered_component_ids": ["c1"]}, {}),
    "send": ("POST", "send-to-advisor", {}, {"sender": None}),
    "narrate": ("POST", "narrate", {}, {"narrator": lambda it: iter(["Day 1: x."])}),
    "apply_route": ("POST", "apply-route", {"component_ids": ["c1", "c3"]}, {}),
    "add_day": ("POST", "days", DAY, {}),
    "remove_day": ("DELETE", "days/{day_id}", {}, {}),
}


async def _owner_trip(conn) -> str:
    """The owner pins c1 and adds one day; returns that day's id."""
    assert (await _add(conn, session_id=OWNER))["statusCode"] == 200
    r = await handler.route("POST", f"/trip/{T1}/days", {"session_id": OWNER, **DAY}, conn=conn,
                            guest_token=TOKEN)
    return json.loads(r["body"])["day_id"]


async def _op(conn, name, session_id, token, day_id):
    method, suffix, body, kw = OPS[name]
    kw = {k: (notify.LoggingSender() if k == "sender" else v) for k, v in kw.items()}
    path = f"/trip/{T1}/" + suffix.format(day_id=day_id)
    return await handler.route(method, path, {"session_id": session_id, **body}, conn=conn,
                               guest_token=token, **kw)


@pytest.mark.parametrize("name", sorted(OPS))
@pytest.mark.asyncio
async def test_every_mutation_refuses_another_session_and_serves_the_owner(name, monkeypatch, grace_closed):
    monkeypatch.setattr(config, "REQUIRE_REGISTRATION_BEFORE_HANDOFF", False)
    conn = FakeConn()
    day_id = await _owner_trip(conn)
    before = len(conn.events)
    intruder = await _op(conn, name, INTRUDER, OTHER_TOKEN, day_id)
    assert intruder["statusCode"] == 403 and _err(intruder) == "forbidden"
    assert len(conn.events) == before                                   # nothing written
    # the intruder's own token on the owner's session id is a 401, not a 403
    stolen_id = await _op(conn, name, OWNER, OTHER_TOKEN, day_id)
    assert stolen_id["statusCode"] == 401
    owner = await _op(conn, name, OWNER, TOKEN, day_id)
    assert owner["statusCode"] == 200, owner["body"]
    # the guard created every session row an event points at (the real FK would fail otherwise)
    assert {e["session_id"] for e in conn.events} <= set(conn.sessions)


@pytest.mark.parametrize("method,path,body", [
    ("POST", "/trip/not-a-uuid/components", {"session_id": S1, "component_id": "c1"}),
    ("POST", f"/trip/{T1}/components", {"session_id": "s1", "component_id": "c1"}),
    ("POST", f"/trip/{T1}/components", {"session_id": S1.upper(), "component_id": "c1"}),
    ("POST", f"/trip/{T1}/narrate", {"session_id": 42}),
    ("GET", "/trip/'; drop table x;--", {}),
])
@pytest.mark.asyncio
async def test_non_uuid_ids_get_400_without_echo_or_sql(method, path, body):
    conn = FakeConn()
    resp = await handler.route(method, path, body, conn=conn, guest_token=TOKEN)
    assert resp["statusCode"] == 400
    assert "not-a-uuid" not in resp["body"] and "drop" not in resp["body"]
    assert conn.sql == []


# --- nothing secret in responses or logs ---------------------------------------------------------

@pytest.mark.asyncio
async def test_no_token_hash_or_session_id_in_any_response_or_log(monkeypatch, capsys, caplog,
                                                                  grace_closed):
    monkeypatch.setattr(config, "REQUIRE_REGISTRATION_BEFORE_HANDOFF", False)
    caplog.set_level("DEBUG")
    secrets = (TOKEN, hashlib.sha256(TOKEN.encode()).hexdigest(), OWNER)
    conn = FakeConn()
    day_id = await _owner_trip(conn)
    bodies = []
    for name in sorted(OPS):
        bodies.append((await _op(conn, name, INTRUDER, OTHER_TOKEN, day_id))["body"])
        bodies.append((await _op(conn, name, OWNER, OTHER_TOKEN, day_id))["body"])
        bodies.append((await _op(conn, name, OWNER, "bad token", day_id))["body"])
        bodies.append((await _op(conn, name, OWNER, TOKEN, day_id))["body"])

    # a crash inside route() whose message carries the secrets goes through the catch-all
    class Boom(FakeConn):
        async def fetchrow(self, query, *args):
            raise RuntimeError(f"invalid input for query argument $1: {TOKEN!r} session {OWNER}")

    @asynccontextmanager
    async def acquire():
        yield Boom()

    crash = await handler.serve("POST", f"/trip/{T1}/components",
                                {"session_id": OWNER, "component_id": "c1"},
                                guest_token=TOKEN, acquire=acquire)
    assert crash["statusCode"] == 500 and json.loads(crash["body"]) == {"error": "internal"}
    bodies.append(crash["body"])

    out = capsys.readouterr()
    logged = out.out + out.err + caplog.text
    for secret in secrets:
        for text in bodies + [logged]:
            assert secret not in text
    lines = [json.loads(line) for line in out.out.splitlines() if line.startswith("{")]
    auth = [line for line in lines if line.get("evt") == "guest_auth"]
    assert {line["outcome"] for line in auth} >= {"token_new", "token_ok", "forbidden_owner",
                                                 "token_mismatch", "bad_token"}
    assert all(set(line) == {"evt", "outcome", "route"} for line in auth)
    assert T1 not in out.out.replace(f"/trip/{T1}", "")        # no trip id in the auth/error lines
    errors = [line for line in lines if line.get("evt") == "assembly_error"]
    assert errors == [{"evt": "assembly_error", "route": "add", "error": "RuntimeError",
                       "at": errors[0]["at"]}]


@pytest.mark.asyncio
async def test_registration_hand_off_still_writes_an_outbox_row_without_the_session(monkeypatch,
                                                                                   grace_closed):
    monkeypatch.setattr(config, "REQUIRE_REGISTRATION_BEFORE_HANDOFF", True)
    conn = FakeConn()
    assert (await _add(conn))["statusCode"] == 200
    first = await handler.route("POST", f"/trip/{T1}/send-to-advisor", {"session_id": S1}, conn=conn,
                                guest_token=TOKEN, sender=notify.LoggingSender())
    assert first["statusCode"] == 422                                   # the inline sign-up form
    sent = await handler.route("POST", f"/trip/{T1}/send-to-advisor",
                               {"session_id": S1, "customer": {"name": "Jo", "email": "jo@x.com",
                                                               "consent": True}},
                               conn=conn, guest_token=TOKEN, sender=notify.LoggingSender())
    assert sent["statusCode"] == 200
    assert conn.sessions[S1]["customer_id"] == "cust-1"
    payload = conn.outbox[0]["payload"]
    assert S1 not in json.dumps(payload) and "session" not in json.dumps(payload)


def test_route_names_cover_the_eight_mutations():
    names = {handler._route_name(m, f"/trip/{T1}/" + s.format(day_id="d"))   # noqa: SLF001
             for m, s, _b, _k in OPS.values()}
    assert names == set(OPS)
    assert handler._route_name("GET", "/nowhere") == "unknown"            # noqa: SLF001


@pytest.mark.asyncio
async def test_events_no_longer_create_sessions():
    """The guard owns session creation; append_event alone writes no sessions row."""
    conn = FakeConn()
    await events.append_event(conn, T1, S1, "add_component", {"component_id": "c1"})
    assert conn.sessions == {}
    assert not any("tripplanner.sessions" in q for q in conn.sql)
