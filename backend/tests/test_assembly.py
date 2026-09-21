"""Offline unit tests for Lambda B (Trip Assembly). No DB, no Bedrock.

A FakeConn emulates just enough of asyncpg: an in-memory trip_events log,
a fixed component catalog (with lat/lng), a customers table, and a
sessions table, plus a no-op transaction context manager.
"""
from __future__ import annotations

import json

import pytest

from backend import config
from backend.assembly import (
    agent,
    events,
    handler,
    master_content,
    notify,
    registration,
    sequencing,
)


# --- sequencing -------------------------------------------------------------

def test_sequence_single_and_empty():
    assert sequencing.sequence([]) == []
    one = [{"id": "a", "lat": 1.0, "lng": 2.0}]
    assert sequencing.sequence(one) == one


def test_sequence_is_deterministic_and_nearest_neighbor():
    comps = [
        {"id": "far", "lat": 30.0, "lng": 30.0},
        {"id": "west", "lat": 0.0, "lng": 0.0},
        {"id": "mid", "lat": 0.0, "lng": 10.0},
    ]
    order = [c["id"] for c in sequencing.sequence(comps)]
    # anchor is west-most (lng=0) then nearest-neighbor
    assert order == ["west", "mid", "far"]
    # deterministic: same input -> same output
    assert order == [c["id"] for c in sequencing.sequence(list(reversed(comps)))]


def test_group_into_days_assigns_sequential_days():
    comps = [{"id": "a", "lat": 0, "lng": 0}, {"id": "b", "lat": 0, "lng": 1}]
    grouped = sequencing.sequence_and_group(comps)
    assert [(e["day"], e["component_id"]) for e in grouped] == [(1, "a"), (2, "b")]


# --- FakeConn ---------------------------------------------------------------

CATALOG = {
    "c1": {"id": "c1", "name": "Hanoi", "activity": "cultural_heritage",
           "duration_hint": "full_day", "lat": 21.0, "lng": 105.8},
    "c2": {"id": "c2", "name": "Sapa", "activity": "trekking",
           "duration_hint": "multi_night", "lat": 22.3, "lng": 103.8},
    "c3": {"id": "c3", "name": "Hoi An", "activity": "culinary",
           "duration_hint": "full_day", "lat": 15.9, "lng": 108.3},
}


class FakeTxn:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class FakeConn:
    def __init__(self):
        self.events: list[dict] = []
        self._eid = 0
        self.drafts: dict[str, dict] = {}
        self.customers: list[dict] = []
        self.sessions: dict[str, dict] = {}

    def transaction(self):
        return FakeTxn()

    async def execute(self, query, *args):
        q = " ".join(query.split())
        if q.startswith("INSERT INTO tripplanner.trip_events"):
            trip_id, session_id, etype, payload = args
            self._eid += 1
            self.events.append({
                "id": self._eid, "trip_id": trip_id, "session_id": session_id,
                "event_type": etype, "payload": payload,
            })
        elif q.startswith("INSERT INTO tripplanner.trip_drafts"):
            trip_id, session_id, status, itinerary_json = args
            existing = self.drafts.get(trip_id)
            new_status = status
            if existing and existing["status"] == "sent":
                new_status = "sent"
            self.drafts[trip_id] = {
                "id": trip_id, "session_id": session_id,
                "status": new_status, "itinerary": itinerary_json,
            }
        elif q.startswith("UPDATE tripplanner.sessions"):
            customer_id, session_id = args
            self.sessions.setdefault(session_id, {"id": session_id})
            self.sessions[session_id]["customer_id"] = customer_id
            self.sessions[session_id]["expires_at"] = None
        return "OK"

    async def fetch(self, query, *args):
        q = " ".join(query.split())
        if q.startswith("SELECT event_type, payload FROM tripplanner.trip_events") and "ORDER BY id ASC" in q:
            trip_id = args[0]
            return [
                {"event_type": e["event_type"], "payload": e["payload"]}
                for e in self.events if e["trip_id"] == trip_id
            ]
        if q.startswith("SELECT c.id, c.name, c.activity"):
            ids = args[0]
            return [dict(CATALOG[i]) for i in ids if i in CATALOG]
        return []

    async def fetchrow(self, query, *args):
        q = " ".join(query.split())
        if q.startswith("SELECT event_type, payload FROM tripplanner.trip_events") and "ORDER BY id DESC" in q:
            trip_id = args[0]
            rows = [e for e in self.events if e["trip_id"] == trip_id]
            if not rows:
                return None
            last = rows[-1]
            return {"event_type": last["event_type"], "payload": last["payload"]}
        if q.startswith("SELECT customer_id FROM tripplanner.sessions"):
            sid = args[0]
            return self.sessions.get(sid)
        if q.startswith("SELECT id FROM tripplanner.customers"):
            phone, email = args
            for c in self.customers:
                if (phone and c.get("phone") == phone) or (email and c.get("email") == email):
                    return {"id": c["id"]}
            return None
        if q.startswith("INSERT INTO tripplanner.customers"):
            name, phone, email = args
            cid = f"cust-{len(self.customers)+1}"
            self.customers.append({"id": cid, "name": name, "phone": phone, "email": email})
            return {"id": cid}
        return None


# --- events / projection ----------------------------------------------------

@pytest.mark.asyncio
async def test_add_components_builds_projection():
    conn = FakeConn()
    await events.append_event(conn, "trip1", "s1", "add_component", {"component_id": "c1"})
    itinerary = await events.append_event(
        conn, "trip1", "s1", "add_component", {"component_id": "c3"}
    )
    # two components, sequenced deterministically, days 1..2
    assert {e["component_id"] for e in itinerary} == {"c1", "c3"}
    assert sorted(e["day"] for e in itinerary) == [1, 2]
    # projection persisted
    assert "trip1" in conn.drafts


@pytest.mark.asyncio
async def test_remove_component_updates_projection():
    conn = FakeConn()
    await events.append_event(conn, "t", "s1", "add_component", {"component_id": "c1"})
    await events.append_event(conn, "t", "s1", "add_component", {"component_id": "c2"})
    itinerary = await events.append_event(conn, "t", "s1", "remove_component", {"component_id": "c1"})
    assert {e["component_id"] for e in itinerary} == {"c2"}


@pytest.mark.asyncio
async def test_reorder_pins_explicit_order():
    conn = FakeConn()
    for cid in ("c1", "c2", "c3"):
        await events.append_event(conn, "t", "s1", "add_component", {"component_id": cid})
    itinerary = await events.append_event(
        conn, "t", "s1", "reorder", {"ordered_component_ids": ["c3", "c1", "c2"]}
    )
    assert [e["component_id"] for e in itinerary] == ["c3", "c1", "c2"]
    assert [e["day"] for e in itinerary] == [1, 2, 3]


@pytest.mark.asyncio
async def test_add_after_reorder_invalidates_explicit_order():
    conn = FakeConn()
    await events.append_event(conn, "t", "s1", "add_component", {"component_id": "c3"})
    await events.append_event(conn, "t", "s1", "reorder", {"ordered_component_ids": ["c3"]})
    # adding a new component after reorder -> back to deterministic sequencing
    itinerary = await events.append_event(conn, "t", "s1", "add_component", {"component_id": "c2"})
    # sequencing anchors west-most: c2 (lng 103.8) before c3 (lng 108.3)
    assert [e["component_id"] for e in itinerary] == ["c2", "c3"]


# --- registration -----------------------------------------------------------

@pytest.mark.asyncio
async def test_registration_creates_and_claims():
    conn = FakeConn()
    conn.sessions["s1"] = {"id": "s1"}
    cid = await registration.register_and_claim(conn, "s1", "Jo", "123", "jo@x.com")
    assert cid == "cust-1"
    assert conn.sessions["s1"]["customer_id"] == "cust-1"


@pytest.mark.asyncio
async def test_registration_dedupes_existing_email():
    conn = FakeConn()
    conn.sessions["s1"] = {"id": "s1"}
    conn.customers.append({"id": "existing", "name": "Jo", "phone": None, "email": "jo@x.com"})
    cid = await registration.register_and_claim(conn, "s1", "Jo", "", "jo@x.com")
    assert cid == "existing"  # reused, not a new insert


@pytest.mark.asyncio
async def test_registration_requires_contact():
    conn = FakeConn()
    with pytest.raises(ValueError):
        await registration.register_and_claim(conn, "s1", "Jo", "", "")


# --- notify -----------------------------------------------------------------

@pytest.mark.asyncio
async def test_notify_uses_config_email():
    sender = notify.LoggingSender()
    await notify.notify_advisor("trip1", [{"event_type": "sent", "payload": {}}], sender=sender)
    assert len(sender.sent) == 1
    assert sender.sent[0]["to"] == config.ADVISOR_NOTIFY_EMAIL


# --- agent (compose / renarrate) --------------------------------------------

def _fake_invoker(_model, _body):
    # emulate a buffered Claude messages response
    return {"content": [
        {"type": "text", "text": "Day 1 narration. "},
        {"type": "text", "text": "Day 2 narration."},
    ]}


def test_compose_returns_text():
    itinerary = [{"day": 1, "name": "Hanoi", "text_extract": "x"}]
    out = "".join(agent.compose(itinerary, invoker=_fake_invoker))
    assert "Day 1 narration." in out


def test_renarrate_preserves_given_order_in_prompt():
    # renarrate must not reorder — we assert the prompt reflects the given
    # order by capturing the body passed to the invoker.
    captured = {}

    def capture_invoker(model, body):
        captured["body"] = body
        return {"content": []}

    itinerary = [
        {"day": 1, "name": "Hoi An", "text_extract": "a"},
        {"day": 2, "name": "Hanoi", "text_extract": "b"},
    ]
    list(agent.renarrate(itinerary, invoker=capture_invoker))
    user_msg = captured["body"]["messages"][0]["content"]
    assert user_msg.index("Day 1: Hoi An") < user_msg.index("Day 2: Hanoi")


# --- master content (authored aa_itineraries) -------------------------------

def test_parse_days_handles_format_variants():
    block = (
        "Day 1 — Arrival in Vientiane: Mekong Riverfront. On arrival, relax.\n"
        "Day 02: Discover Anuradhapura's Pagodas. Drive to the ancient city.\n"
        "DAY 3 - Kandy. Visit the Temple of the Tooth."
    )
    days = master_content.parse_days(block)
    assert set(days) == {1, 2, 3}
    assert "Mekong Riverfront" in days[1]
    assert "Anuradhapura" in days[2]


def test_parse_days_unstructured_returns_empty():
    assert master_content.parse_days("A lovely trip with no day markers.") == {}


def test_build_narration_prefers_master_and_flags_missing():
    itinerary = [
        {"day": 1, "name": "A", "source_tour_id": "t1", "source_day_index": 6},
        {"day": 2, "name": "B", "source_tour_id": "t1", "source_day_index": 7},
    ]
    day_texts = {("t1", 6): "Day 6 — Old town: wander the streets."}
    narration, missing = master_content.build_narration(itinerary, day_texts)
    # authored day relabelled to the trip's day number, source header stripped
    assert narration.startswith("Day 1: Old town: wander the streets.")
    assert "Day 6" not in narration
    # the day with no authored text is reported as missing (LLM fallback)
    assert [m["day"] for m in missing] == [2]


# --- suggestions ------------------------------------------------------------

def test_avg_vector_literal():
    from backend.assembly import suggestions
    lit = suggestions._avg_vector_literal([[0.0, 2.0], [2.0, 0.0]])
    assert lit == "[1.0,1.0]"
    assert suggestions._avg_vector_literal([]) is None


@pytest.mark.asyncio
async def test_suggest_empty_trip_returns_empty():
    from backend.assembly import suggestions
    conn = FakeConn()
    out = await suggestions.suggest(conn, "no-such-trip")
    assert out == {"suggestions": []}


@pytest.mark.asyncio
async def test_handler_suggestions_route_ok():
    conn = FakeConn()
    await events.append_event(conn, "t1", "s1", "add_component", {"component_id": "c1"})
    resp = await handler.route("GET", "/trip/t1/suggestions", {}, conn=conn)
    assert resp["statusCode"] == 200
    assert "suggestions" in json.loads(resp["body"])


# --- suggestion geography re-rank (#5) --------------------------------------

def test_rerank_without_anchor_preserves_taste_order():
    from backend.assembly import suggestions
    cands = [
        {"id": "a", "name": "A", "lat": 0, "lng": 0, "country": "X", "component_count": 1},
        {"id": "b", "name": "B", "lat": 0, "lng": 1, "country": "X", "component_count": 1},
        {"id": "c", "name": "C", "lat": 0, "lng": 2, "country": "X", "component_count": 1},
    ]
    out = suggestions._rerank_by_route(cands, None, 3)
    assert [s["id"] for s in out] == ["a", "b", "c"]
    assert all(s["why"] == "Similar to places you've pinned" for s in out)


def test_rerank_promotes_a_near_candidate_over_a_slightly_better_far_one():
    from backend.assembly import suggestions
    # Last stop at (0,0). Taste order is far0, far1, near, far2 (near is 3rd).
    # 'near' sits right next to the last stop while the far ones are ~30° away.
    # A small taste gap + a big proximity gap should let 'near' climb to the
    # top — geography breaking a near-tie in taste.
    cands = [
        {"id": "far0", "name": "F0", "lat": 30.0, "lng": 30.0, "country": "X", "component_count": 1},
        {"id": "near", "name": "N", "lat": 0.1, "lng": 0.1, "country": "X", "component_count": 1},
        {"id": "far1", "name": "F1", "lat": 30.0, "lng": 31.0, "country": "X", "component_count": 1},
        {"id": "far2", "name": "F2", "lat": 30.0, "lng": 32.0, "country": "X", "component_count": 1},
    ]
    # far0 is taste #1 but ~4700km away (geo=1.0): 0.6*0 + 0.4*1 = 0.40.
    # near is taste #2 but next-door (geo=0.0):    0.6*0.333 + 0.4*0 = 0.20.
    # near wins.
    out = suggestions._rerank_by_route(cands, (0.0, 0.0), 4)
    assert out[0]["id"] == "near"
    assert out[0]["why"].endswith("close to your last stop")


def test_rerank_keeps_top_taste_when_also_closest():
    from backend.assembly import suggestions
    cands = [
        {"id": "best", "name": "Best", "lat": 0.1, "lng": 0.1, "country": "X", "component_count": 1},
        {"id": "other", "name": "Other", "lat": 20.0, "lng": 20.0, "country": "X", "component_count": 1},
    ]
    out = suggestions._rerank_by_route(cands, (0.0, 0.0), 1)
    assert out[0]["id"] == "best"


# --- handler routing --------------------------------------------------------

@pytest.mark.asyncio
async def test_handler_add_component():
    conn = FakeConn()
    resp = await handler.route(
        "POST", "/trip/t1/components",
        {"session_id": "s1", "component_id": "c1"}, conn=conn,
    )
    assert resp["statusCode"] == 200
    assert resp["headers"]["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_handler_add_missing_fields_400():
    conn = FakeConn()
    resp = await handler.route("POST", "/trip/t1/components", {"session_id": "s1"}, conn=conn)
    assert resp["statusCode"] == 400


@pytest.mark.asyncio
async def test_handler_send_requires_registration_when_flag_on(monkeypatch):
    monkeypatch.setattr(config, "REQUIRE_REGISTRATION_BEFORE_HANDOFF", True)
    conn = FakeConn()
    conn.sessions["s1"] = {"id": "s1", "customer_id": None}  # guest
    resp = await handler.route(
        "POST", "/trip/t1/send-to-advisor", {"session_id": "s1"}, conn=conn
    )
    assert resp["statusCode"] == 422
    assert json.loads(resp["body"])["error"] == "registration_required"


@pytest.mark.asyncio
async def test_handler_send_registers_then_sends_and_notifies(monkeypatch):
    monkeypatch.setattr(config, "REQUIRE_REGISTRATION_BEFORE_HANDOFF", True)
    conn = FakeConn()
    conn.sessions["s1"] = {"id": "s1", "customer_id": None}
    await events.append_event(conn, "t1", "s1", "add_component", {"component_id": "c1"})
    sender = notify.LoggingSender()
    resp = await handler.route(
        "POST", "/trip/t1/send-to-advisor",
        {"session_id": "s1", "customer": {"name": "Jo", "email": "jo@x.com"}},
        conn=conn, sender=sender,
    )
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["status"] == "sent"
    assert conn.sessions["s1"]["customer_id"] == "cust-1"   # registered + claimed
    assert len(sender.sent) == 1                             # advisor notified
    assert conn.drafts["t1"]["status"] == "sent"


@pytest.mark.asyncio
async def test_handler_narrate_returns_narration():
    conn = FakeConn()
    await events.append_event(conn, "t1", "s1", "add_component", {"component_id": "c1"})
    await events.append_event(conn, "t1", "s1", "add_component", {"component_id": "c3"})

    def fake_narrator(itinerary):
        # asserts the handler passed a non-empty, day-ordered itinerary
        assert itinerary and itinerary[0]["day"] == 1
        yield "Composed narration for "
        yield f"{len(itinerary)} days."

    resp = await handler.route(
        "POST", "/trip/t1/narrate", {"session_id": "s1"},
        conn=conn, narrator=fake_narrator,
    )
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["mode"] == "compose"
    assert body["narration"] == "Composed narration for 2 days."
    assert len(body["itinerary"]) == 2


@pytest.mark.asyncio
async def test_handler_narrate_empty_trip_400():
    conn = FakeConn()
    resp = await handler.route(
        "POST", "/trip/empty/narrate", {"session_id": "s1"},
        conn=conn, narrator=lambda it: iter(["x"]),
    )
    assert resp["statusCode"] == 400
    assert json.loads(resp["body"])["error"] == "empty_trip"


@pytest.mark.asyncio
async def test_handler_narrate_requires_session_id():
    conn = FakeConn()
    resp = await handler.route("POST", "/trip/t1/narrate", {}, conn=conn)
    assert resp["statusCode"] == 400


@pytest.mark.asyncio
async def test_handler_narrate_bad_mode_400():
    conn = FakeConn()
    resp = await handler.route(
        "POST", "/trip/t1/narrate", {"session_id": "s1", "mode": "bogus"},
        conn=conn, narrator=lambda it: iter(["x"]),
    )
    assert resp["statusCode"] == 400


@pytest.mark.asyncio
async def test_handler_narrate_bedrock_failure_502():
    conn = FakeConn()
    await events.append_event(conn, "t1", "s1", "add_component", {"component_id": "c1"})

    def boom(itinerary):
        raise RuntimeError("bedrock down")
        yield  # pragma: no cover — make it a generator

    resp = await handler.route(
        "POST", "/trip/t1/narrate", {"session_id": "s1"},
        conn=conn, narrator=boom,
    )
    assert resp["statusCode"] == 502
    assert json.loads(resp["body"])["error"] == "narration_failed"


@pytest.mark.asyncio
async def test_handler_send_allows_edit_after_sent(monkeypatch):
    # after 'sent', another add keeps status 'sent' in projection (not locked)
    monkeypatch.setattr(config, "REQUIRE_REGISTRATION_BEFORE_HANDOFF", False)
    conn = FakeConn()
    conn.sessions["s1"] = {"id": "s1", "customer_id": "cust-x"}
    await events.append_event(conn, "t1", "s1", "add_component", {"component_id": "c1"})
    await handler.route("POST", "/trip/t1/send-to-advisor", {"session_id": "s1"}, conn=conn)
    # edit after send
    resp = await handler.route(
        "POST", "/trip/t1/components", {"session_id": "s1", "component_id": "c2"}, conn=conn
    )
    assert resp["statusCode"] == 200
    # projection stays 'sent' (edit allowed, not auto-re-notified)
    assert conn.drafts["t1"]["status"] == "sent"
