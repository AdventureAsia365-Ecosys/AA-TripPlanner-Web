"""AA-909: the four trip read GETs need the guest token of the session that owns the trip.

Offline (FakeConn from test_assembly). Per route: the owner's token 200; a wrong, missing or malformed
token 404; another trip's token 404; a trip with no draft yet 404. Every refusal is byte for byte the
unknown-route 404, so a caller cannot tell a trip that exists from one that does not.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from backend.assembly import guest_session, handler
from backend.tests.test_assembly import (CATALOG, FakeConn, INTRUDER, OWNER, T1, T9, TEMPTY, TOKEN,
                                         _session)

OTHER_TOKEN = "Zz9_-aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789ab"
DEST = "00000000-0000-4000-8000-0000000000d1"

# route name -> path suffix under /trip/{trip_id}
READS = {
    "get_trip": "",
    "suggestions": "/suggestions",
    "coverage": "/coverage",
    "activities": f"/stops/{DEST}/activities",
}


async def _get(conn, name, token, trip=T1):
    return await handler.route("GET", f"/trip/{trip}{READS[name]}", {}, conn=conn, guest_token=token)


async def _pin(conn, session_id, token, trip):
    r = await handler.route("POST", f"/trip/{trip}/components",
                            {"session_id": session_id, "component_id": "c1"},
                            conn=conn, guest_token=token)
    assert r["statusCode"] == 200, r["body"]


async def _two_trips() -> FakeConn:
    """OWNER (TOKEN) owns T1; INTRUDER (OTHER_TOKEN) owns T9. Both made through the real guard."""
    conn = FakeConn()
    await _pin(conn, OWNER, TOKEN, T1)
    await _pin(conn, INTRUDER, OTHER_TOKEN, T9)
    return conn


@pytest.fixture(autouse=True)
def c1_on_a_tour_day(monkeypatch):
    """c1 as a real catalog component (a tour day at DEST), so coverage has a leg to build."""
    monkeypatch.setitem(CATALOG, "c1", {**CATALOG["c1"], "destination_id": DEST,
                                        "source_tour_id": "tour-1", "source_day_index": 1})


@pytest.fixture
def unknown_route_404():
    return handler._not_found()  # noqa: SLF001


def test_the_reads_are_the_four_gets_of_the_assembly_lambda():
    gets = {name for verb, _rx, name in handler._ROUTE_NAMES if verb == "GET"}  # noqa: SLF001
    assert gets == set(READS)
    for name, suffix in READS.items():
        assert handler._route_name("GET", f"/trip/{T1}{suffix}") == name  # noqa: SLF001


@pytest.mark.parametrize("name", sorted(READS))
@pytest.mark.asyncio
async def test_the_owners_token_reads_the_trip(name):
    conn = await _two_trips()
    resp = await _get(conn, name, TOKEN)
    assert resp["statusCode"] == 200, resp["body"]
    assert json.loads(resp["body"])["trip_id"] == T1


@pytest.mark.asyncio
async def test_the_owner_reads_the_pinned_itinerary():
    conn = await _two_trips()
    body = json.loads((await _get(conn, "get_trip", TOKEN))["body"])
    assert [d["component_id"] for d in body["itinerary"]] == ["c1"]


@pytest.mark.parametrize("name", sorted(READS))
@pytest.mark.parametrize("token", [None, "", OTHER_TOKEN, "A" * 43])
@pytest.mark.asyncio
async def test_a_missing_or_wrong_token_is_a_plain_404(name, token, unknown_route_404):
    """OTHER_TOKEN is another trip's real token (INTRUDER owns T9); "A"*43 is well formed, unbound."""
    conn = await _two_trips()
    resp = await _get(conn, name, token)
    assert resp == unknown_route_404


@pytest.mark.parametrize("name", sorted(READS))
@pytest.mark.parametrize("bad", ["short", "x" * 44, "has space" + "a" * 34, "a" * 42 + "\n"])
@pytest.mark.asyncio
async def test_a_malformed_token_is_a_404_before_any_sql(name, bad, unknown_route_404):
    conn = FakeConn()
    resp = await _get(conn, name, bad)
    assert resp == unknown_route_404
    assert conn.sql == []


@pytest.mark.parametrize("name", sorted(READS))
@pytest.mark.asyncio
async def test_each_owner_reads_only_their_own_trip(name, unknown_route_404):
    conn = await _two_trips()
    assert (await _get(conn, name, OTHER_TOKEN, trip=T9))["statusCode"] == 200
    assert await _get(conn, name, OTHER_TOKEN, trip=T1) == unknown_route_404
    assert await _get(conn, name, TOKEN, trip=T9) == unknown_route_404


@pytest.mark.parametrize("name", sorted(READS))
@pytest.mark.asyncio
async def test_a_trip_with_no_draft_answers_like_one_that_does_not_exist(name, unknown_route_404):
    """No draft = no owner to prove against. Same 404 as a wrong token, so existence never leaks."""
    conn = await _two_trips()
    assert await _get(conn, name, TOKEN, trip=TEMPTY) == unknown_route_404


@pytest.mark.parametrize("name", sorted(READS))
@pytest.mark.asyncio
async def test_a_pre_aa827_owner_session_with_no_hash_is_refused(name, unknown_route_404):
    conn = FakeConn()
    await _pin(conn, OWNER, TOKEN, T1)
    conn.sessions[OWNER] = _session(OWNER, token=None)           # legacy row, hash NULL
    assert await _get(conn, name, TOKEN) == unknown_route_404


@pytest.mark.asyncio
async def test_one_cookie_reads_every_trip_its_sessions_own():
    """After a 401 reset the browser makes a new session_id but keeps its cookie: the old trip stays
    readable by that cookie, and by nothing else."""
    conn = FakeConn()
    second = "00000000-0000-4000-8000-0000000000a4"
    await _pin(conn, OWNER, TOKEN, T1)
    await _pin(conn, second, TOKEN, T9)
    assert (await _get(conn, "get_trip", TOKEN, trip=T1))["statusCode"] == 200
    assert (await _get(conn, "get_trip", TOKEN, trip=T9))["statusCode"] == 200


@pytest.mark.asyncio
async def test_a_read_never_creates_or_binds_a_session():
    conn = FakeConn()
    for name in READS:
        await _get(conn, name, TOKEN)
    assert conn.sessions == {}
    assert not any(q.startswith(("INSERT", "UPDATE")) for q in conn.sql)


@pytest.mark.asyncio
async def test_read_log_lines_carry_outcome_and_route_only(capsys):
    conn = await _two_trips()
    capsys.readouterr()
    for name in sorted(READS):
        await _get(conn, name, TOKEN)
        await _get(conn, name, OTHER_TOKEN)
        await _get(conn, name, None)
        await _get(conn, name, "bad")
        await _get(conn, name, TOKEN, trip=TEMPTY)
    out = capsys.readouterr().out
    lines = [json.loads(x) for x in out.splitlines() if x.startswith("{")]
    auth = [x for x in lines if x.get("evt") == "guest_auth"]
    assert len(auth) == 5 * len(READS)
    assert all(set(x) == {"evt", "outcome", "route"} for x in auth)
    assert {x["outcome"] for x in auth} == {guest_session.READ_OK, guest_session.TOKEN_MISMATCH,
                                           guest_session.NO_TOKEN, guest_session.BAD_TOKEN,
                                           guest_session.NO_OWNER}
    assert {x["route"] for x in auth} == set(READS)
    for secret in (TOKEN, OTHER_TOKEN, hashlib.sha256(TOKEN.encode()).hexdigest(), OWNER, T1, TEMPTY):
        assert secret not in out


@pytest.mark.asyncio
async def test_the_mutations_still_answer_401_and_403():
    """Unchanged by AA-909: the client's reset path depends on these exact answers."""
    conn = await _two_trips()
    stolen = await handler.route("POST", f"/trip/{T1}/components",
                                 {"session_id": OWNER, "component_id": "c2"},
                                 conn=conn, guest_token=OTHER_TOKEN)
    assert stolen["statusCode"] == 401
    other = await handler.route("POST", f"/trip/{T1}/components",
                                {"session_id": INTRUDER, "component_id": "c2"},
                                conn=conn, guest_token=OTHER_TOKEN)
    assert other["statusCode"] == 403
