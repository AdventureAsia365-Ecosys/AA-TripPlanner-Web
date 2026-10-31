"""Offline unit tests for Lambda B (Trip Assembly). No DB, no Bedrock.

A FakeConn emulates just enough of asyncpg: an in-memory trip_events log,
a fixed component catalog (with lat/lng), a customers table, and a
sessions table, plus a no-op transaction context manager.
"""
from __future__ import annotations

import json
import uuid

import pytest

from backend import config
from backend.assembly import (
    agent,
    events,
    guest_session,
    handoff,
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
        self.outbox: list[dict] = []
        self.sql: list[str] = []          # every statement issued, normalised (AA-827 tests)

    def transaction(self):
        return FakeTxn()

    async def execute(self, query, *args):
        q = " ".join(query.split())
        self.sql.append(q)
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
        elif q.startswith("INSERT INTO tripplanner.outbox"):
            event, version, key, aggregate_id, payload = args
            if not any(r["idempotency_key"] == key for r in self.outbox):
                self.outbox.append({"event": event, "schema_version": version, "idempotency_key": key,
                                    "aggregate_id": aggregate_id, "payload": json.loads(payload)})
        elif q.startswith("UPDATE tripplanner.customers SET contact_consent"):
            for c in self.customers:
                if c["id"] == args[0] and not c.get("contact_consent"):
                    c["contact_consent"], c["contact_consent_at"] = True, "2026-10-11T00:00:00+00:00"
        elif q.startswith("INSERT INTO tripplanner.sessions (id, guest_token_hash"):
            session_id, token_hash, _days = args          # ON CONFLICT (id) DO NOTHING
            self.sessions.setdefault(session_id, {"id": session_id, "customer_id": None,
                                                  "guest_token_hash": token_hash})
        elif q.startswith("UPDATE tripplanner.sessions SET guest_token_hash"):
            token_hash, session_id = args                 # only while the hash IS NULL
            row = self.sessions.get(session_id)
            if row is not None and row.get("guest_token_hash") is None:
                row["guest_token_hash"] = token_hash
        elif q.startswith("UPDATE tripplanner.sessions SET customer_id"):
            customer_id, session_id = args
            self.sessions.setdefault(session_id, {"id": session_id})
            self.sessions[session_id]["customer_id"] = customer_id
            self.sessions[session_id]["expires_at"] = None
        return "OK"

    async def fetch(self, query, *args):
        q = " ".join(query.split())
        self.sql.append(q)
        if q.startswith("SELECT event_type, payload FROM tripplanner.trip_events") and "ORDER BY id ASC" in q:
            trip_id = args[0]
            return [
                {"event_type": e["event_type"], "payload": e["payload"]}
                for e in self.events if e["trip_id"] == trip_id
            ]
        if q.startswith("SELECT c.component_id AS id, c.name, c.activity"):
            ids = args[0]
            return [dict(CATALOG[i]) for i in ids if i in CATALOG]
        return []

    async def fetchrow(self, query, *args):
        q = " ".join(query.split())
        self.sql.append(q)
        if q.startswith("SELECT guest_token_hash FROM tripplanner.sessions"):
            row = self.sessions.get(args[0])
            return {"guest_token_hash": row.get("guest_token_hash")} if row is not None else None
        if q.startswith("SELECT event_type, payload FROM tripplanner.trip_events") and "ORDER BY id DESC" in q:
            trip_id = args[0]
            rows = [e for e in self.events if e["trip_id"] == trip_id]
            if "event_type IN ('reorder', 'add_component', 'remove_component')" in q:
                rows = [e for e in rows if e["event_type"] in ("reorder", "add_component",
                                                               "remove_component")]
            if not rows:
                return None
            last = rows[-1]
            return {"event_type": last["event_type"], "payload": last["payload"]}
        if q.startswith("SELECT id, event_type FROM tripplanner.trip_events") and "ORDER BY id DESC" in q:
            rows = [e for e in self.events if e["trip_id"] == args[0]]
            return {"id": rows[-1]["id"], "event_type": rows[-1]["event_type"]} if rows else None
        if q.startswith("SELECT c.id, c.name, c.phone, c.email, c.contact_consent"):
            cid = (self.sessions.get(args[0]) or {}).get("customer_id")
            for c in self.customers:
                if c["id"] == cid:
                    return {"id": c["id"], "name": c["name"], "phone": c.get("phone"),
                            "email": c.get("email"), "contact_consent": c.get("contact_consent", False),
                            "contact_consent_at": c.get("contact_consent_at")}
            return None
        if q.startswith("SELECT s.guest_token_hash FROM tripplanner.trip_drafts d JOIN tripplanner.sessions s"):
            d = self.drafts.get(args[0])                  # AA-909 read guard: the owner's hash
            row = self.sessions.get(d["session_id"]) if d else None
            return {"guest_token_hash": row.get("guest_token_hash")} if row is not None else None
        if q.startswith("SELECT session_id FROM tripplanner.trip_drafts"):
            d = self.drafts.get(args[0])
            return {"session_id": d["session_id"]} if d else None
        if q.startswith("SELECT customer_id FROM tripplanner.sessions"):
            sid = args[0]
            return self.sessions.get(sid)
        if q.startswith("SELECT id FROM tripplanner.customers"):
            phone, email = args
            for c in self.customers:
                # mirrors SQL: phone = $1 OR lower(email) = $2 (email arrives normalised)
                if (phone and c.get("phone") == phone) or (
                        email and c.get("email") and c["email"].lower() == email):
                    return {"id": c["id"]}
            return None
        if q.startswith("INSERT INTO tripplanner.customers"):
            name, phone, email = args
            cid = f"cust-{len(self.customers)+1}"
            self.customers.append({"id": cid, "name": name, "phone": phone, "email": email})
            return {"id": cid}
        return None


# --- AA-827: ids are UUIDs and every mutation carries the BFF guest token ------

TOKEN = "-0UxxnsV3WRRgs8YYFs7hikEinxIORd7SfjlpIeS-9Q"   # shape of a BFF token: 43 base64url chars
S1 = "00000000-0000-4000-8000-0000000000a1"
OWNER = "00000000-0000-4000-8000-0000000000a2"
INTRUDER = "00000000-0000-4000-8000-0000000000a3"
T1 = "00000000-0000-4000-8000-0000000000b1"
T9 = "00000000-0000-4000-8000-0000000000b9"
T235 = "00000000-0000-4000-8000-000000000235"
TEMPTY = "00000000-0000-4000-8000-0000000000be"


def _session(session_id: str, customer_id=None, token: str | None = TOKEN) -> dict:
    """A sessions row already bound to `token` (AA-827)."""
    return {"id": session_id, "customer_id": customer_id,
            "guest_token_hash": guest_session.hash_token(token) if token else None}


async def call(method, path, body, *, conn, guest_token=TOKEN, **kw):
    """handler.route with the BFF guest token. Every test session is token-bound, so no test here
    depends on the date of the legacy grace window (test_guest_session.py covers the window)."""
    return await handler.route(method, path, body, conn=conn, guest_token=guest_token, **kw)


# --- events / projection ----------------------------------------------------

@pytest.mark.asyncio
async def test_add_components_builds_projection():
    conn = FakeConn()
    await events.append_event(conn, "trip1", S1, "add_component", {"component_id": "c1"})
    itinerary = await events.append_event(
        conn, "trip1", S1, "add_component", {"component_id": "c3"}
    )
    # two components, sequenced deterministically, days 1..2
    assert {e["component_id"] for e in itinerary} == {"c1", "c3"}
    assert sorted(e["day"] for e in itinerary) == [1, 2]
    # projection persisted
    assert "trip1" in conn.drafts


@pytest.mark.asyncio
async def test_remove_component_updates_projection():
    conn = FakeConn()
    await events.append_event(conn, "t", S1, "add_component", {"component_id": "c1"})
    await events.append_event(conn, "t", S1, "add_component", {"component_id": "c2"})
    itinerary = await events.append_event(conn, "t", S1, "remove_component", {"component_id": "c1"})
    assert {e["component_id"] for e in itinerary} == {"c2"}


@pytest.mark.asyncio
async def test_reorder_pins_explicit_order():
    conn = FakeConn()
    for cid in ("c1", "c2", "c3"):
        await events.append_event(conn, "t", S1, "add_component", {"component_id": cid})
    itinerary = await events.append_event(
        conn, "t", S1, "reorder", {"ordered_component_ids": ["c3", "c1", "c2"]}
    )
    assert [e["component_id"] for e in itinerary] == ["c3", "c1", "c2"]
    assert [e["day"] for e in itinerary] == [1, 2, 3]


@pytest.mark.asyncio
async def test_add_after_reorder_invalidates_explicit_order():
    conn = FakeConn()
    await events.append_event(conn, "t", S1, "add_component", {"component_id": "c3"})
    await events.append_event(conn, "t", S1, "reorder", {"ordered_component_ids": ["c3"]})
    # adding a new component after reorder -> back to deterministic sequencing
    itinerary = await events.append_event(conn, "t", S1, "add_component", {"component_id": "c2"})
    # sequencing anchors west-most: c2 (lng 103.8) before c3 (lng 108.3)
    assert [e["component_id"] for e in itinerary] == ["c2", "c3"]


# --- registration -----------------------------------------------------------

@pytest.mark.asyncio
async def test_registration_creates_and_claims():
    conn = FakeConn()
    conn.sessions[S1] = {"id": S1}
    cid = await registration.register_and_claim(conn, S1, "Jo", "123", "jo@x.com")
    assert cid == "cust-1"
    assert conn.sessions[S1]["customer_id"] == "cust-1"


@pytest.mark.asyncio
async def test_registration_never_claims_an_existing_customer_on_unverified_match():
    """S235: typing someone else's email must not attach this session to their customer."""
    conn = FakeConn()
    conn.sessions[S1] = {"id": S1}
    conn.customers.append({"id": "existing", "name": "Victim", "phone": None, "email": "jo@x.com"})
    cid = await registration.register_and_claim(conn, S1, "Mallory", "", "jo@x.com", consent=True)
    assert cid is None
    assert conn.sessions[S1].get("customer_id") is None          # session stays a guest
    assert len(conn.customers) == 1                                # no insert (UNIQUE email)
    assert conn.customers[0]["name"] == "Victim"
    assert not conn.customers[0].get("contact_consent")            # stored customer untouched


@pytest.mark.asyncio
async def test_registration_email_match_is_case_insensitive_and_stored_lowercase():
    conn = FakeConn()
    conn.sessions[S1] = {"id": S1}
    cid = await registration.register_and_claim(conn, S1, "Jo", "", "  Jo@X.com ")
    assert cid == "cust-1" and conn.customers[0]["email"] == "jo@x.com"
    conn.sessions["s2"] = {"id": "s2"}
    # same address, different case → a match, so not claimed and no second row
    assert await registration.register_and_claim(conn, "s2", "Jo", "", "JO@x.COM") is None
    assert len(conn.customers) == 1
    # a legacy mixed-case row (written before normalisation) still matches
    conn.customers.append({"id": "legacy", "name": "Al", "phone": None, "email": "Al@Y.com"})
    conn.sessions["s3"] = {"id": "s3"}
    assert await registration.register_and_claim(conn, "s3", "Al", "", "al@y.com") is None


@pytest.mark.asyncio
async def test_registration_existing_phone_is_not_claimed():
    conn = FakeConn()
    conn.sessions[S1] = {"id": S1}
    conn.customers.append({"id": "existing", "name": "Victim", "phone": "123", "email": None})
    assert await registration.register_and_claim(conn, S1, "Mallory", " 123 ", "m@x.com") is None
    assert conn.sessions[S1].get("customer_id") is None


@pytest.mark.asyncio
async def test_registration_requires_contact():
    conn = FakeConn()
    with pytest.raises(ValueError):
        await registration.register_and_claim(conn, S1, "Jo", "", "")


# --- notify -----------------------------------------------------------------

@pytest.mark.asyncio
async def test_notify_uses_config_email():
    sender = notify.LoggingSender()
    await notify.notify_advisor("trip1", [{"event_type": "sent", "payload": {}}], sender=sender)
    assert len(sender.sent) == 1
    assert sender.sent[0]["to"] == config.ADVISOR_NOTIFY_EMAIL


def test_advisor_email_has_no_personal_default(monkeypatch):
    # AA-811: AA-Booking e-mails staff; TripPlanner keeps no (personal) address in code.
    import importlib

    monkeypatch.delenv("ADVISOR_NOTIFY_EMAIL", raising=False)
    try:
        assert importlib.reload(config).ADVISOR_NOTIFY_EMAIL == ""
    finally:
        monkeypatch.undo()
        importlib.reload(config)


@pytest.mark.asyncio
async def test_logging_sender_logs_and_sends_nothing(caplog):
    sender = notify.LoggingSender()
    with caplog.at_level("INFO", logger="backend.assembly.notify"):
        await notify.notify_advisor("trip1", [], sender=sender)
    assert "not e-mailed" in caplog.text and "trip1" in caplog.text


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
        {"day": 1, "name": "Old town", "source_tour_id": "t1", "source_day_index": 6},
        {"day": 2, "name": "Night market", "source_tour_id": "t1", "source_day_index": 7},
    ]
    day_texts = {("t1", 6): "Day 6 — Old town: wander the streets."}
    narration, missing = master_content.build_narration(itinerary, day_texts)
    # authored day relabelled to the trip's day number, source header stripped
    assert narration.startswith("Day 1: Old town: wander the streets.")
    assert "Day 6" not in narration
    # the day with no authored text is reported as missing (LLM fallback)
    assert [m["day"] for m in missing] == [2]


# AA-819 D1 (S239, live on Dev): a trip with Fatehpur Sikri (North India 22 days, day 16) and
# Amber Fort (component row says Rajasthan: Forts, Havelis & Desert 22 days, day 5) got day 5's
# "Khimsar to Jaisalmer" text under Day 2. The tour's own day 16 is the Amber Fort day; the frozen
# component row points at the wrong tour day. Texts below are the live CIS day texts.
_NI, _RAJ = "fbadec01-765a-46fc-96ce-c92a9db823e8", "e716b1eb-daa3-46b3-abf2-e8a6f4da4fcf"
_D1_TEXTS = {
    (_NI, 16): ("Day 16: Fatehpur Sikri Detour and Arrival in Agra\nA 2-3 hour private vehicle journey "
                "heads eastward to Agra, with an optional stop at Fatehpur Sikri, a 16th-century Mughal city."),
    (_RAJ, 4): ("Day 4: Nawalgarh to Khimsar — Fort and Desert\n240-kilometer journey to Khimsar. Visits "
                "include Khimsar Fort, the surrounding sand dunes."),
    (_RAJ, 5): ("Day 5: Khimsar to Jaisalmer\n290-kilometer drive to Jaisalmer, known as the Golden City "
                "for its honey-colored sandstone architecture. Overnight in Jaisalmer."),
    (_RAJ, 16): ("Day 16: Jaipur: Amber Fort, City Palace & Hawa Mahal\nVisit Amber Fort, City Palace, and "
                 "the well-known Hawa Mahal. Overnight in Jaipur."),
}
_D1_TRIP = [
    {"day": 1, "component_id": "fs", "name": "Fatehpur Sikri", "destination_name": "Fatehpur Sikri",
     "source_tour_id": _NI, "source_day_index": 16},
    {"day": 2, "component_id": "af", "name": "Amber Fort", "destination_name": "Amber Fort",
     "source_tour_id": _RAJ, "source_day_index": 5},
]


def test_build_narration_never_uses_a_tour_day_that_does_not_visit_the_stop():
    narration, missing = master_content.build_narration(_D1_TRIP, _D1_TEXTS)
    assert "Jaisalmer" not in narration and "Khimsar" not in narration
    # the same tour's day that does visit Amber Fort is used instead (still AA text, no model call)
    assert "Day 2: Jaipur: Amber Fort, City Palace & Hawa Mahal" in narration
    assert narration.startswith("Day 1: Fatehpur Sikri Detour and Arrival in Agra\nA 2-3 hour")
    assert missing == []


def test_build_narration_keeps_the_own_day_when_no_other_day_of_the_tour_names_the_stop():
    """Review of the S239 lane: a day that names no stop is more often a spelling variant than a
    wrong day, so it keeps its AA text instead of costing a model call."""
    texts = {k: v for k, v in _D1_TEXTS.items() if k != (_RAJ, 16)}
    narration, missing = master_content.build_narration(_D1_TRIP, texts)
    assert "Day 2: Khimsar to Jaisalmer" in narration
    assert missing == []


def test_build_narration_sends_a_day_with_no_text_at_all_to_the_fallback():
    trip = [dict(_D1_TRIP[1], day=1, source_day_index=30)]
    narration, missing = master_content.build_narration(trip, {(_RAJ, 5): _D1_TEXTS[(_RAJ, 5)]})
    assert narration == "" and [m["component_id"] for m in missing] == ["af"]


def _whole_tour(tour: str, pins: list[str]) -> list[dict]:
    """A 'Customise this trip' itinerary: one pinned component per tour day, in tour order."""
    return [{"day": i, "component_id": f"c{i}", "name": name, "destination_name": name,
             "source_tour_id": tour, "source_day_index": i} for i, name in enumerate(pins, start=1)]


def test_build_narration_never_repeats_a_day_another_stop_of_the_trip_already_has():
    """Review of the S239 lane, live EBC case: the last day (departure) pins 'Cho La Pass', which
    the tour crosses on day 2. Day 2 is that trip's own day 2, so day 3 keeps its own text."""
    texts = {("ebc", 1): "Day 1 — Arrival in Kathmandu\nTransfer to the hotel.",
             ("ebc", 2): "Day 2 — Ascent of Cho La Pass to Dzongla\nA long day over the pass.",
             ("ebc", 3): "Day 3 — Departure\nTransfer to the airport."}
    narration, missing = master_content.build_narration(
        _whole_tour("ebc", ["Kathmandu", "Cho La Pass", "Cho La Pass"]), texts)
    assert narration.count("Ascent of Cho La Pass") == 1
    assert "Day 3: Departure" in narration and missing == []


def test_build_narration_does_not_pull_a_neighbour_day_in_twice():
    """Live Phuket case: day 2 (elephant sanctuary) pins 'Phuket', named on day 1, which is the
    trip's own day 1; a tie must not copy day 1 under day 2."""
    texts = {("th", 1): "Day 1 — Phuket Day Tour\nViewpoint, Big Buddha and Old Town in Phuket.",
             ("th", 2): "Day 2 — Elephant Sanctuary\nA morning with rescued elephants.",
             ("th", 3): "Day 3 — Koh Phi Phi\nBoat to Koh Phi Phi."}
    narration, missing = master_content.build_narration(
        _whole_tour("th", ["Phuket", "Phuket", "Koh Phi Phi Don"]), texts)
    assert narration.count("Phuket Day Tour") == 1
    assert "Day 2: Elephant Sanctuary" in narration
    assert "Day 3: Koh Phi Phi" in narration and missing == []


def test_stop_names_match_without_a_generic_suffix():
    """'Pashupatinath Temple' is written 'Pashupatinath', 'Koh Phi Phi Don' 'Koh Phi Phi'."""
    assert master_content._mentions("Visit Pashupatinath at dusk.",
                                    master_content._stop_keys({"name": "Pashupatinath Temple"}))
    assert master_content._mentions("Boat to Koh Phi Phi.",
                                    master_content._stop_keys({"name": "Koh Phi Phi Don"}))
    # a suffix alone, or a too-short head, is not a key
    assert master_content._stop_keys({"name": "Temple"}) == [" temple "]
    assert master_content._stop_keys({"name": "Wat Fort"}) == [" wat fort "]


def test_build_narration_checks_every_entry_of_a_trip_day_before_moving_it():
    """A trip day with two activities of tour day 3: the first entry's name is not in day 3's
    text, the second's is. Day 3 is confirmed; day 1 (also naming the first) must not replace it."""
    texts = {("vn", 1): "Day 1 — Arrival\nCheck in to your hotel in the Old Quarter.",
             ("vn", 3): "Day 3 — Hanoi temples\nVisit the Temple of Literature."}
    trip = [{"day": 1, "component_id": "oq", "name": "Old Quarter", "destination_name": "Hanoi Old Quarter",
             "source_tour_id": "vn", "source_day_index": 3},
            {"day": 1, "component_id": "tl", "name": "Temple of Literature",
             "source_tour_id": "vn", "source_day_index": 3}]
    narration, missing = master_content.build_narration(trip, texts)
    assert narration == "Day 1: Hanoi temples\nVisit the Temple of Literature." and missing == []


def test_build_narration_matches_the_stop_by_its_place_name_and_ignores_case_and_accents():
    trip = [{"day": 1, "component_id": "x", "name": "Sunset boat ride", "destination_name": "Hội An",
             "source_tour_id": "t", "source_day_index": 3}]
    narration, missing = master_content.build_narration(
        trip, {("t", 3): "Day 3 — Old town: a lantern-lit evening in HOI AN."})
    assert narration == "Day 1: Old town: a lantern-lit evening in HOI AN." and missing == []


def test_merge_by_day_keeps_every_line_of_a_multi_line_day():
    """A master day is 'Day N: <title>' plus prose lines; the merge used to keep only the first line."""
    master = "Day 1: Arrival in Agra\nA 2-3 hour drive to Agra.\nOvernight in Agra."
    merged = handler._merge_by_day(master, "Day 2: Jaipur and its forts.")
    assert merged == "Day 1: Arrival in Agra\nA 2-3 hour drive to Agra.\nOvernight in Agra.\nDay 2: Jaipur and its forts."


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
    conn.sessions[S1] = _session(S1)                   # AA-909: reads need the owner's token
    await events.append_event(conn, T1, S1, "add_component", {"component_id": "c1"})
    resp = await call("GET", f"/trip/{T1}/suggestions", {}, conn=conn)
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
    resp = await call(
        "POST", f"/trip/{T1}/components",
        {"session_id": S1, "component_id": "c1"}, conn=conn,
    )
    assert resp["statusCode"] == 200
    assert resp["headers"]["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_handler_add_missing_fields_400():
    conn = FakeConn()
    resp = await call("POST", f"/trip/{T1}/components", {"session_id": S1}, conn=conn)
    assert resp["statusCode"] == 400


@pytest.mark.asyncio
async def test_handler_send_requires_registration_when_flag_on(monkeypatch):
    monkeypatch.setattr(config, "REQUIRE_REGISTRATION_BEFORE_HANDOFF", True)
    conn = FakeConn()
    conn.sessions[S1] = _session(S1, None)  # guest
    resp = await call(
        "POST", f"/trip/{T1}/send-to-advisor", {"session_id": S1}, conn=conn
    )
    assert resp["statusCode"] == 422
    assert json.loads(resp["body"])["error"] == "registration_required"


@pytest.mark.asyncio
async def test_handler_send_registers_then_sends_and_notifies(monkeypatch):
    monkeypatch.setattr(config, "REQUIRE_REGISTRATION_BEFORE_HANDOFF", True)
    conn = FakeConn()
    conn.sessions[S1] = _session(S1, None)
    await events.append_event(conn, T1, S1, "add_component", {"component_id": "c1"})
    sender = notify.LoggingSender()
    resp = await call(
        "POST", f"/trip/{T1}/send-to-advisor",
        {"session_id": S1, "customer": {"name": "Jo", "email": "jo@x.com"}},
        conn=conn, sender=sender,
    )
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["status"] == "sent"
    assert conn.sessions[S1]["customer_id"] == "cust-1"   # registered + claimed
    assert len(sender.sent) == 1                             # advisor notified
    assert conn.drafts[T1]["status"] == "sent"


@pytest.mark.asyncio
async def test_handler_send_twice_is_one_handoff_and_one_email(monkeypatch):
    """AA-731: a double click / retry after a lost response must not create a second hand-off."""
    monkeypatch.setattr(config, "REQUIRE_REGISTRATION_BEFORE_HANDOFF", True)
    conn = FakeConn()
    conn.sessions[S1] = _session(S1, None)
    await events.append_event(conn, T1, S1, "add_component", {"component_id": "c1"})
    sender = notify.LoggingSender()
    req = {"session_id": S1, "customer": {"name": "Jo", "email": "jo@x.com"}}
    first = json.loads((await call("POST", f"/trip/{T1}/send-to-advisor", req,
                                   conn=conn, sender=sender))["body"])
    second = json.loads((await call("POST", f"/trip/{T1}/send-to-advisor", req,
                                    conn=conn, sender=sender))["body"])
    assert first["already_sent"] is False and second["already_sent"] is True
    assert first["handoff_id"] == second["handoff_id"]
    assert len(sender.sent) == 1
    assert [e["event_type"] for e in conn.events].count("sent") == 1
    assert len(conn.customers) == 1


@pytest.mark.asyncio
async def test_handler_send_after_a_real_edit_is_a_new_handoff(monkeypatch):
    monkeypatch.setattr(config, "REQUIRE_REGISTRATION_BEFORE_HANDOFF", False)
    conn = FakeConn()
    await events.append_event(conn, T1, S1, "add_component", {"component_id": "c1"})
    sender = notify.LoggingSender()
    first = json.loads((await call("POST", f"/trip/{T1}/send-to-advisor", {"session_id": S1},
                                   conn=conn, sender=sender))["body"])
    await events.append_event(conn, T1, S1, "add_component", {"component_id": "c3"})
    second = json.loads((await call("POST", f"/trip/{T1}/send-to-advisor", {"session_id": S1},
                                    conn=conn, sender=sender))["body"])
    assert second["already_sent"] is False and second["handoff_id"] != first["handoff_id"]
    assert len(sender.sent) == 2


@pytest.mark.asyncio
async def test_handler_send_takes_the_per_trip_lock_first(monkeypatch):
    monkeypatch.setattr(config, "REQUIRE_REGISTRATION_BEFORE_HANDOFF", False)
    conn = FakeConn()
    conn.sessions[S1] = _session(S1)        # bound already: the guard only reads
    seen = []
    orig = conn.execute

    async def spy(query, *args):
        seen.append((" ".join(query.split()), args))
        return await orig(query, *args)

    conn.execute = spy
    await call("POST", f"/trip/{T1}/send-to-advisor", {"session_id": S1}, conn=conn,
               sender=notify.LoggingSender())
    assert seen[0] == ("SELECT pg_advisory_xact_lock(hashtext($1))", (f"tp_send:{T1}",))


@pytest.mark.asyncio
async def test_handler_narrate_replaces_a_day_with_an_invented_number():
    """AA-731: an LLM figure no component text supports never reaches the customer."""
    conn = FakeConn()
    await events.append_event(conn, T1, S1, "add_component", {"component_id": "c1"})
    itinerary = await events.current_itinerary(conn, T1)
    name = itinerary[0]["name"]

    def fake_narrator(it):
        yield "Day 1: Admire the 22-meter reclining Buddha, a 900-year-old marvel."

    resp = await call("POST", f"/trip/{T1}/narrate", {"session_id": S1},
                      conn=conn, narrator=fake_narrator)
    narration = json.loads(resp["body"])["narration"]
    assert "22" not in narration and "900" not in narration
    assert narration == f"Day 1: {name}."


@pytest.mark.asyncio
async def test_handler_narrate_returns_narration():
    conn = FakeConn()
    await events.append_event(conn, T1, S1, "add_component", {"component_id": "c1"})
    await events.append_event(conn, T1, S1, "add_component", {"component_id": "c3"})

    def fake_narrator(itinerary):
        # asserts the handler passed a non-empty, day-ordered itinerary
        assert itinerary and itinerary[0]["day"] == 1
        yield "Composed narration for "
        yield f"{len(itinerary)} days."

    resp = await call(
        "POST", f"/trip/{T1}/narrate", {"session_id": S1},
        conn=conn, narrator=fake_narrator,
    )
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["mode"] == "compose"
    assert body["narration"] == "Composed narration for 2 days."
    assert len(body["itinerary"]) == 2


@pytest.mark.asyncio
async def test_handler_narrate_logs_each_model_call(monkeypatch):
    """AA-685: without an injected narrator the real agent runs on the tp_compose gateway
    route, and every model call becomes one llm_call_log row."""
    from backend.shared import llm_gateway

    conn = FakeConn()
    await events.append_event(conn, T1, S1, "add_component", {"component_id": "c1"})
    recorded = []

    async def fake_load_route(db, stage):
        assert stage == "tp_compose"
        return llm_gateway.builtin_route(stage)

    def fake_generate_text(route, system, user, max_tokens, **kw):
        return "Day 1: Arrive.", llm_gateway.Call(route.stage, "writer", "sonnet", "acc3",
                                                  120, 30, 0.00081, False, "end_turn")

    async def fake_record(db, call, quality_signal, **kw):
        recorded.append((call.stage, call.cost_usd, quality_signal))

    monkeypatch.setattr(handler.llm_gateway, "load_route", fake_load_route)
    monkeypatch.setattr(agent.llm_gateway, "generate_text", fake_generate_text)
    monkeypatch.setattr(handler.llm_gateway, "record", fake_record)
    resp = await call("POST", f"/trip/{T1}/narrate", {"session_id": S1}, conn=conn)
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["narration"] == "Day 1: Arrive."
    assert recorded == [("tp_compose", 0.00081, {"mode": "compose", "source": "llm", "days": 1,
                                                  "output_len_chars": 14})]


@pytest.mark.asyncio
async def test_handler_narrate_empty_trip_400():
    conn = FakeConn()
    resp = await call(
        "POST", f"/trip/{TEMPTY}/narrate", {"session_id": S1},
        conn=conn, narrator=lambda it: iter(["x"]),
    )
    assert resp["statusCode"] == 400
    assert json.loads(resp["body"])["error"] == "empty_trip"


@pytest.mark.asyncio
async def test_handler_narrate_requires_session_id():
    conn = FakeConn()
    resp = await call("POST", f"/trip/{T1}/narrate", {}, conn=conn)
    assert resp["statusCode"] == 400


@pytest.mark.asyncio
async def test_handler_narrate_bad_mode_400():
    conn = FakeConn()
    resp = await call(
        "POST", f"/trip/{T1}/narrate", {"session_id": S1, "mode": "bogus"},
        conn=conn, narrator=lambda it: iter(["x"]),
    )
    assert resp["statusCode"] == 400


@pytest.mark.asyncio
async def test_handler_narrate_bedrock_failure_502():
    conn = FakeConn()
    await events.append_event(conn, T1, S1, "add_component", {"component_id": "c1"})

    def boom(itinerary):
        raise RuntimeError("bedrock down")
        yield  # pragma: no cover — make it a generator

    resp = await call(
        "POST", f"/trip/{T1}/narrate", {"session_id": S1},
        conn=conn, narrator=boom,
    )
    assert resp["statusCode"] == 502
    assert json.loads(resp["body"])["error"] == "narration_failed"


@pytest.mark.asyncio
async def test_handler_send_allows_edit_after_sent(monkeypatch):
    # after 'sent', another add keeps status 'sent' in projection (not locked)
    monkeypatch.setattr(config, "REQUIRE_REGISTRATION_BEFORE_HANDOFF", False)
    conn = FakeConn()
    conn.sessions[S1] = _session(S1, "cust-x")
    await events.append_event(conn, T1, S1, "add_component", {"component_id": "c1"})
    await call("POST", f"/trip/{T1}/send-to-advisor", {"session_id": S1}, conn=conn)
    # edit after send
    resp = await call(
        "POST", f"/trip/{T1}/components", {"session_id": S1, "component_id": "c2"}, conn=conn
    )
    assert resp["statusCode"] == 200
    # projection stays 'sent' (edit allowed, not auto-re-notified)
    assert conn.drafts[T1]["status"] == "sent"


# --- AA-674 / PR-11: added days and activity selection -----------------------

@pytest.mark.asyncio
async def test_add_and_remove_a_day_are_customizations_in_the_trip():
    conn = FakeConn()
    await events.append_event(conn, T1, S1, "add_component", {"component_id": "c1"})
    r = await call("POST", f"/trip/{T1}/days", {"session_id": S1, "kind": "extra_night",
                                                      "after_component_id": "c1"}, conn=conn)
    assert r["statusCode"] == 200
    day_id = json.loads(r["body"])["day_id"]
    await call("POST", f"/trip/{T1}/days", {"session_id": S1, "kind": "extend_end"}, conn=conn)
    trip = json.loads((await call("GET", f"/trip/{T1}", {}, conn=conn))["body"])
    assert [d["kind"] for d in trip["custom_days"]] == ["extra_night", "extend_end"]
    assert all(d["customization"] for d in trip["custom_days"])
    r = await call("DELETE", f"/trip/{T1}/days/{day_id}", {"session_id": S1}, conn=conn)
    assert [d["kind"] for d in json.loads(r["body"])["custom_days"]] == ["extend_end"]
    assert [e["event_type"] for e in conn.events][-3:] == ["add_day", "add_day", "remove_day"]  # reaches the advisor log


@pytest.mark.asyncio
async def test_add_day_rejects_unknown_kind_and_an_extra_night_off_the_trip():
    conn = FakeConn()
    bad = await call("POST", f"/trip/{T1}/days", {"session_id": S1, "kind": "holiday"}, conn=conn)
    assert bad["statusCode"] == 400
    off = await call("POST", f"/trip/{T1}/days", {"session_id": S1, "kind": "extra_night",
                                                        "after_component_id": "c9"}, conn=conn)
    assert off["statusCode"] == 400
    gone = await call("DELETE", f"/trip/{T1}/days/nope", {"session_id": S1}, conn=conn)
    assert gone["statusCode"] == 404


def test_suggestion_filters_ignore_unknown_values():
    from backend.assembly import suggestions
    assert suggestions.clean_filters({"activity": "trekking", "intensity_level": "extreme"}) == ("trekking", None)
    assert suggestions.clean_filters(None) == (None, None)


def test_query_string_reaches_get_routes():
    event = {"requestContext": {"http": {"method": "GET", "path": f"/trip/{T1}/suggestions"}},
             "queryStringParameters": {"activity": "trekking"}}
    assert handler._extract_request(event)[2] == {"activity": "trekking"}


@pytest.mark.asyncio
async def test_apply_route_pins_components_in_route_order():
    conn = FakeConn()
    ids = [k for k in CATALOG][:3]
    r = await call("POST", f"/trip/{T9}/apply-route",
                   {"session_id": S1, "component_ids": list(reversed(ids)) + [ids[0]]}, conn=conn)
    assert r["statusCode"] == 200
    assert [d["component_id"] for d in json.loads(r["body"])["itinerary"]] == list(reversed(ids))
    assert [e["event_type"] for e in conn.events] == ["add_component"] * 3 + ["reorder"]
    bad = await call("POST", f"/trip/{T9}/apply-route", {"session_id": S1, "component_ids": []}, conn=conn)
    assert bad["statusCode"] == 400


def test_components_of_one_tour_day_share_a_trip_day_and_are_narrated_once():
    from backend.assembly import sequencing
    comps = [{"id": "a", "source_tour_id": "t", "source_day_index": 2},
             {"id": "b", "source_tour_id": "t", "source_day_index": 2},
             {"id": "c", "source_tour_id": "t", "source_day_index": 3},
             {"id": "d", "source_tour_id": None, "source_day_index": None},
             {"id": "e", "source_tour_id": None, "source_day_index": None}]
    days = sequencing.group_into_days(comps)
    assert [d["day"] for d in days] == [1, 1, 2, 3, 4]
    narration, missing = master_content.build_narration(days, {("t", 2): "Day 2 — Haa: valley walk", ("t", 3): "Day 3 — Paro"})
    assert narration.count("Day 1:") == 1 and "Day 2: Paro" in narration
    assert [m["component_id"] for m in missing] == ["d", "e"]


# --- grounding (AA-731) --------------------------------------------------------

def test_novel_numbers_matches_cis_rules():
    from backend.assembly import grounding
    src = ["Trek to 4,460m base camp over 3 days"]
    assert grounding.novel_numbers("Day 2: reach the 4460 m camp in 3 days", src) == []
    assert grounding.novel_numbers("Day 2: a 5,000m pass", src) == ["5000"]
    assert grounding.novel_numbers("Day 7: free time", src) == []          # Day label is not a claim


def test_ground_narration_keeps_supported_lines_and_checks_each_day_against_its_own_text():
    from backend.assembly import grounding
    it = [{"day": 1, "name": "Hoi An", "text_extract": "Lantern walk at 6pm"},
          {"day": 2, "name": "My Son", "text_extract": "Temples from the 4th century"}]
    text = "Day 1: Lanterns glow from 6pm.\nDay 2: Ruins built 6 centuries ago.\nSee you on day 2!"
    out, removed = grounding.ground_narration(text, it)
    assert out.splitlines() == ["Day 1: Lanterns glow from 6pm.", "Day 2: My Son.", "See you on day 2!"]
    assert removed == [{"line": "Day 2: Ruins built 6 centuries ago.", "numbers": ["6"]}]


def test_ground_narration_drops_a_free_line_with_an_invented_figure():
    from backend.assembly import grounding
    it = [{"day": 1, "name": "Hoi An", "text_extract": "Old town"}]
    out, removed = grounding.ground_narration("Day 1: Old town.\nOver 1,000 shops await.", it)
    assert out == "Day 1: Old town." and removed[0]["numbers"] == ["1000"]


# --- AA-676: inquiry.submitted outbox ----------------------------------------

def test_leg_chain_groups_consecutive_days_of_the_same_tour():
    itinerary = [
        {"day": 1, "source_tour_id": "T1", "source_day_index": 2},
        {"day": 1, "source_tour_id": "T1", "source_day_index": 2},   # two activities, same tour day
        {"day": 2, "source_tour_id": "T1", "source_day_index": 3},
        {"day": 3, "source_tour_id": "T2", "source_day_index": 1},
        {"day": 4, "source_tour_id": "T1", "source_day_index": 7},   # back to T1, not contiguous
    ]
    assert handoff.leg_chain(itinerary) == [
        {"position": 1, "source_tour_id": "T1", "day_from": 2, "day_to": 3},
        {"position": 2, "source_tour_id": "T2", "day_from": 1, "day_to": 1},
        {"position": 3, "source_tour_id": "T1", "day_from": 7, "day_to": 7},
    ]


@pytest.mark.asyncio
async def test_send_writes_one_inquiry_outbox_row_with_consent(monkeypatch):
    monkeypatch.setattr(config, "REQUIRE_REGISTRATION_BEFORE_HANDOFF", True)
    conn = FakeConn()
    conn.sessions[S1] = _session(S1, None)
    await events.append_event(conn, T1, S1, "add_component", {"component_id": "c1"})
    req = {"session_id": S1, "customer": {"name": "Jo", "email": "jo@x.com", "consent": True}}
    first = json.loads((await call("POST", f"/trip/{T1}/send-to-advisor", req, conn=conn,
                                   sender=notify.LoggingSender()))["body"])
    await call("POST", f"/trip/{T1}/send-to-advisor", req, conn=conn,
               sender=notify.LoggingSender())                       # double click
    assert len(conn.outbox) == 1
    row = conn.outbox[0]
    assert row["event"] == "inquiry.submitted" and row["schema_version"] == 1
    assert row["idempotency_key"] == f"{T1}:{first['handoff_id']}"
    p = row["payload"]
    assert p["contract"] == "inquiry.submitted" and p["trip_id"] == T1
    assert p["version"] == 1.4                     # AA-908: contract v1.4 (number, enum [1.4])
    assert p["customer"]["email"] == "jo@x.com" and p["customer"]["consent"] is True
    assert [d["component_id"] for d in p["itinerary"]["days"]] == ["c1"]


@pytest.mark.asyncio
async def test_send_resolves_each_leg_to_the_catalog_key(monkeypatch):
    """AA-830: a leg keyed by a version tour_id goes out with its catalog source_id; an unknown key
    goes out with source_system / source_id null (never refused)."""
    monkeypatch.setattr(config, "REQUIRE_REGISTRATION_BEFORE_HANDOFF", True)
    monkeypatch.setitem(CATALOG, "c1", {**CATALOG["c1"], "source_tour_id": "tour-v2", "source_day_index": 1})
    monkeypatch.setitem(CATALOG, "c2", {**CATALOG["c2"], "source_tour_id": "gone", "source_day_index": 4})

    class CatalogConn(FakeConn):
        async def fetch(self, query, *args):
            if query == handoff.TOUR_KEYS_SQL:
                self.asked = args[0]
                return [{"source_system": "cis", "source_id": "src-1", "tour_id": "tour-v2"}]
            return await super().fetch(query, *args)

    conn = CatalogConn()
    conn.sessions[S1] = _session(S1, None)
    for cid in ("c1", "c2"):
        await events.append_event(conn, T1, S1, "add_component", {"component_id": cid})
    req = {"session_id": S1, "customer": {"name": "Jo", "email": "jo@x.com", "consent": True}}
    await call("POST", f"/trip/{T1}/send-to-advisor", req, conn=conn, sender=notify.LoggingSender())
    legs = conn.outbox[0]["payload"]["itinerary"]["legs"]
    assert conn.asked == ["gone", "tour-v2"]
    assert {leg["source_tour_id"]: (leg["source_system"], leg["source_id"]) for leg in legs} == {
        "tour-v2": ("cis", "src-1"), "gone": (None, None)}


@pytest.mark.asyncio
async def test_send_without_consent_records_false(monkeypatch):
    monkeypatch.setattr(config, "REQUIRE_REGISTRATION_BEFORE_HANDOFF", True)
    conn = FakeConn()
    conn.sessions[S1] = _session(S1, None)
    await events.append_event(conn, T1, S1, "add_component", {"component_id": "c1"})
    await call("POST", f"/trip/{T1}/send-to-advisor",
               {"session_id": S1, "customer": {"name": "Jo", "email": "jo@x.com"}},
               conn=conn, sender=notify.LoggingSender())
    assert conn.outbox[0]["payload"]["customer"]["consent"] is False


@pytest.mark.asyncio
async def test_send_with_someone_elses_email_hands_off_only_the_submitted_contact(monkeypatch):
    """S235: the payload carries exactly what this request submitted, flagged unverified —
    never the stored name/phone/consent of the existing customer with that email."""
    monkeypatch.setattr(config, "REQUIRE_REGISTRATION_BEFORE_HANDOFF", True)
    conn = FakeConn()
    conn.customers.append({"id": "victim", "name": "Victim", "phone": "999", "email": "jo@x.com",
                           "contact_consent": True, "contact_consent_at": "2026-01-01T00:00:00+00:00"})
    conn.sessions[S1] = _session(S1, None)
    await events.append_event(conn, T1, S1, "add_component", {"component_id": "c1"})
    resp = await call(
        "POST", f"/trip/{T1}/send-to-advisor",
        {"session_id": S1, "customer": {"name": "Mallory", "email": "JO@x.com", "consent": False}},
        conn=conn, sender=notify.LoggingSender(),
    )
    assert resp["statusCode"] == 200
    assert conn.sessions[S1]["customer_id"] is None               # not linked to the victim
    c = conn.outbox[0]["payload"]["customer"]
    assert c == {"customer_id": None, "name": "Mallory", "phone": None, "email": "jo@x.com",
                 "consent": False, "consent_at": None, "verified": False, "identity": None}
    assert conn.customers[0]["name"] == "Victim" and len(conn.customers) == 1


@pytest.mark.asyncio
async def test_first_time_registration_payload_is_the_new_customer_unverified(monkeypatch):
    monkeypatch.setattr(config, "REQUIRE_REGISTRATION_BEFORE_HANDOFF", True)
    conn = FakeConn()
    conn.sessions[S1] = _session(S1, None)
    await events.append_event(conn, T1, S1, "add_component", {"component_id": "c1"})
    await call("POST", f"/trip/{T1}/send-to-advisor",
               {"session_id": S1, "customer": {"name": "Jo", "email": "Jo@X.com"}},
               conn=conn, sender=notify.LoggingSender())
    assert conn.sessions[S1]["customer_id"] == "cust-1"
    c = conn.outbox[0]["payload"]["customer"]
    assert c["customer_id"] == "cust-1" and c["name"] == "Jo" and c["email"] == "jo@x.com"
    assert c["verified"] is False


@pytest.mark.asyncio
async def test_resend_after_edit_is_a_new_outbox_row(monkeypatch):
    monkeypatch.setattr(config, "REQUIRE_REGISTRATION_BEFORE_HANDOFF", False)
    conn = FakeConn()
    await events.append_event(conn, T1, S1, "add_component", {"component_id": "c1"})
    await call("POST", f"/trip/{T1}/send-to-advisor", {"session_id": S1}, conn=conn,
               sender=notify.LoggingSender())
    await events.append_event(conn, T1, S1, "add_component", {"component_id": "c3"})
    await call("POST", f"/trip/{T1}/send-to-advisor", {"session_id": S1}, conn=conn,
               sender=notify.LoggingSender())
    assert len(conn.outbox) == 2
    assert conn.outbox[0]["idempotency_key"] != conn.outbox[1]["idempotency_key"]
    assert conn.outbox[1]["payload"]["customer"] is None          # guest hand-off


# --- AA-805: leg order (S235 E2E sent one tour as days 3-4, 7, 2, 1) ---------

# One Bhutan-like tour whose geography runs AGAINST its day order: the west-most stop is day 3,
# so the old west-first nearest-neighbour projection produced days 3, 4, 7, 2, 1.
S235_TOUR = "22174c35-6cab-408d-b0f1-1d5c6e6c2885"
S235_COMPONENTS = {
    "b1": {"id": "b1", "name": "Paro arrival", "source_tour_id": S235_TOUR, "source_day_index": 1,
           "lat": 27.40, "lng": 89.90},
    "b2": {"id": "b2", "name": "Thimphu", "source_tour_id": S235_TOUR, "source_day_index": 2,
           "lat": 27.47, "lng": 89.64},
    "b3": {"id": "b3", "name": "Haa valley", "source_tour_id": S235_TOUR, "source_day_index": 3,
           "lat": 27.37, "lng": 89.00},
    "b4": {"id": "b4", "name": "Chele La", "source_tour_id": S235_TOUR, "source_day_index": 4,
           "lat": 27.38, "lng": 89.10},
    "b7": {"id": "b7", "name": "Tiger's Nest", "source_tour_id": S235_TOUR, "source_day_index": 7,
           "lat": 27.49, "lng": 89.36},
}


@pytest.fixture
def s235_catalog(monkeypatch):
    for k, v in S235_COMPONENTS.items():
        monkeypatch.setitem(CATALOG, k, v)


def _legs(itinerary):
    return [(leg["day_from"], leg["day_to"]) for leg in handoff.leg_chain(itinerary)]


def test_old_geography_order_reproduces_the_s235_legs():
    """Guard the fixture: plain west-first nearest neighbour over single pins gives S235's order."""
    from backend.assembly.sequencing import _anchor_key, _haversine
    comps = list(S235_COMPONENTS.values())
    order = [min(comps, key=_anchor_key)]
    rest = [c for c in comps if c is not order[0]]
    while rest:
        nxt = min(rest, key=lambda c: (_haversine(order[-1]["lat"], order[-1]["lng"], c["lat"], c["lng"]),
                                       c["id"]))
        rest.remove(nxt)
        order.append(nxt)
    assert [c["source_day_index"] for c in order] == [3, 4, 7, 2, 1]


def test_sequence_keeps_a_tour_in_its_own_day_order():
    shuffled = [S235_COMPONENTS[k] for k in ("b7", "b3", "b1", "b4", "b2")]
    days = sequencing.sequence_and_group(shuffled)
    assert [d["source_day_index"] for d in days] == [1, 2, 3, 4, 7]
    assert _legs(days) == [(1, 4), (7, 7)]


def test_sequence_places_free_pins_and_other_tours_as_whole_blocks():
    comps = [
        {"id": "x", "lat": 27.45, "lng": 90.50},                                    # free pin, east
        {"id": "t2b", "source_tour_id": "T2", "source_day_index": 2, "lat": 27.0, "lng": 88.0},
        {"id": "t2a", "source_tour_id": "T2", "source_day_index": 1, "lat": 27.0, "lng": 88.5},
        *S235_COMPONENTS.values(),
    ]
    ids = [c["id"] for c in sequencing.sequence(comps)]
    # T2's first stop is the west-most block start; T2 stays day 1 -> 2, then the S235 tour in its
    # day order, then the free pin. Deterministic across input order.
    assert ids == ["t2a", "t2b", "b1", "b2", "b3", "b4", "b7", "x"]
    assert ids == [c["id"] for c in sequencing.sequence(list(reversed(comps)))]


@pytest.mark.asyncio
async def test_apply_route_order_survives_send_to_advisor(monkeypatch, s235_catalog):
    """The S235 path: apply-route (reorder event in day order), then send-to-advisor. The `sent`
    event must not drop the route's order, so the outbox carries the tour's legs in day order."""
    monkeypatch.setattr(config, "REQUIRE_REGISTRATION_BEFORE_HANDOFF", False)
    conn = FakeConn()
    ids = ["b1", "b2", "b3", "b4", "b7"]
    r = await call("POST", f"/trip/{T235}/apply-route", {"session_id": S1, "component_ids": ids},
                   conn=conn)
    assert [d["component_id"] for d in json.loads(r["body"])["itinerary"]] == ids
    sent = await call("POST", f"/trip/{T235}/send-to-advisor", {"session_id": S1}, conn=conn,
                      sender=notify.LoggingSender())
    assert [d["component_id"] for d in json.loads(sent["body"])["itinerary"]] == ids
    legs = conn.outbox[0]["payload"]["itinerary"]["legs"]
    assert [(leg["day_from"], leg["day_to"]) for leg in legs] == [(1, 4), (7, 7)]
    assert {leg["source_tour_id"] for leg in legs} == {S235_TOUR}


@pytest.mark.asyncio
async def test_manual_order_survives_added_days_but_not_a_new_pin(s235_catalog):
    conn = FakeConn()
    for cid in ("b1", "b2", "b3"):
        await events.append_event(conn, "t", S1, "add_component", {"component_id": cid})
    await events.append_event(conn, "t", S1, "reorder", {"ordered_component_ids": ["b3", "b1", "b2"]})
    await events.append_event(conn, "t", S1, "add_day", {"day_id": "d1", "kind": "free_day"})
    assert [e["component_id"] for e in await events.current_itinerary(conn, "t")] == ["b3", "b1", "b2"]
    # a set change invalidates the manual order; the tour falls back to its own day order
    await events.append_event(conn, "t", S1, "add_component", {"component_id": "b4"})
    assert [e["component_id"] for e in await events.current_itinerary(conn, "t")] == ["b1", "b2", "b3", "b4"]


# --- AA-805: /narrate ownership, cache, spend cap, failed-call logging ---------

class AiConn(FakeConn):
    """FakeConn plus the tp-010 tables: narration_cache and the daily spend total."""

    def __init__(self, spent: float = 0.0):
        super().__init__()
        self.cache: dict[str, dict] = {}
        self.spent = spent

    async def execute(self, query, *args):
        q = " ".join(query.split())
        if q.startswith("INSERT INTO tripplanner.narration_cache"):
            key, mode, narration, source, days = args
            self.cache[key] = {"narration": narration, "source": source, "mode": mode}
            return "OK"
        return await super().execute(query, *args)

    async def fetchrow(self, query, *args):
        q = " ".join(query.split())
        if q.startswith("UPDATE tripplanner.narration_cache"):
            return self.cache.get(args[0])
        if "FROM tripplanner.llm_spend_daily" in q:
            return {"spent": self.spent}
        return await super().fetchrow(query, *args)


def _counting_narrator(counter):
    def narrator(it):
        counter.append(len(it))
        yield "\n".join(f"Day {e['day']}: lovely." for e in it)
    return narrator


@pytest.mark.asyncio
async def test_narrate_rejects_another_session_with_403():
    conn = AiConn()
    await events.append_event(conn, T1, OWNER, "add_component", {"component_id": "c1"})
    resp = await call("POST", f"/trip/{T1}/narrate", {"session_id": INTRUDER}, conn=conn,
                      narrator=lambda it: iter(["Day 1: x."]))
    assert resp["statusCode"] == 403
    ok = await call("POST", f"/trip/{T1}/narrate", {"session_id": OWNER}, conn=conn,
                    narrator=lambda it: iter(["Day 1: x."]))
    assert ok["statusCode"] == 200


@pytest.mark.asyncio
async def test_narrate_reuses_the_cached_narration_for_the_same_itinerary():
    conn = AiConn()
    await events.append_event(conn, T1, S1, "add_component", {"component_id": "c1"})
    calls: list = []
    first = json.loads((await call("POST", f"/trip/{T1}/narrate", {"session_id": S1},
                                   conn=conn, narrator=_counting_narrator(calls)))["body"])
    second = json.loads((await call("POST", f"/trip/{T1}/narrate", {"session_id": S1},
                                    conn=conn, narrator=_counting_narrator(calls)))["body"])
    assert calls == [1]                                      # the model ran once
    assert first["cached"] is False and second["cached"] is True
    assert second["narration"] == first["narration"] and second["ai_generated"] is True
    # a different itinerary is a different key
    await events.append_event(conn, T1, S1, "add_component", {"component_id": "c3"})
    await call("POST", f"/trip/{T1}/narrate", {"session_id": S1}, conn=conn,
               narrator=_counting_narrator(calls))
    assert calls == [1, 2]


def test_itinerary_hash_depends_on_content_and_mode_not_on_trip():
    from backend.assembly import narration_cache
    it = [{"day": 1, "component_id": "c1", "name": "Hanoi", "text_extract": "Old quarter"}]
    h = narration_cache.itinerary_hash(it, "compose")
    assert h == narration_cache.itinerary_hash([dict(it[0], trip_id="other")], "compose")
    assert h != narration_cache.itinerary_hash(it, "renarrate")
    assert h != narration_cache.itinerary_hash([dict(it[0], text_extract="New text")], "compose")


def test_itinerary_hash_changes_when_the_master_day_text_changes():
    """AA-805 review: a CIS edit to a tour's authored itinerary must miss the cache, not serve a
    stale 'mixed' narration until the TTL ends."""
    from backend.assembly import narration_cache
    it = [{"day": 1, "component_id": "c1", "name": "Hanoi", "text_extract": "Old quarter",
           "source_tour_id": "T1", "source_day_index": 2}]
    before = narration_cache.itinerary_hash(it, "compose", {("T1", 2): "Day 2: Hanoi walk"})
    after = narration_cache.itinerary_hash(it, "compose", {("T1", 2): "Day 2: Hanoi cyclo"})
    assert before != after
    # Text of days this itinerary does not use does not change the key.
    assert before == narration_cache.itinerary_hash(
        it, "compose", {("T1", 2): "Day 2: Hanoi walk", ("T1", 9): "Day 9: other"})
    assert narration_cache.itinerary_hash(it, "compose") == narration_cache.itinerary_hash(
        it, "compose", {})


def test_itinerary_hash_follows_the_tour_day_the_narration_really_uses():
    """AA-819: when the entry's own day does not visit the stop, another day of the tour is used,
    so an edit to THAT day must miss the cache too."""
    from backend.assembly import narration_cache
    before = narration_cache.itinerary_hash(_D1_TRIP, "compose", _D1_TEXTS)
    edited = {**_D1_TEXTS, (_RAJ, 16): _D1_TEXTS[(_RAJ, 16)] + " Elephant rides are not offered."}
    assert narration_cache.itinerary_hash(_D1_TRIP, "compose", edited) != before


def test_basic_narration_keeps_a_day_with_no_names_through_the_merge():
    """AA-805 review: a nameless day used to render 'Day N.' (no colon) and the merge dropped it."""
    basic = handler._basic_narration([{"day": 2, "name": ""}, {"day": 3, "name": "Hue"}])
    assert basic == "Day 2: free day.\nDay 3: Hue."
    merged = handler._merge_by_day("Day 1: Hanoi.", basic)
    assert merged.splitlines() == ["Day 1: Hanoi.", "Day 2: free day.", "Day 3: Hue."]


@pytest.mark.asyncio
async def test_narrate_over_the_daily_cap_makes_no_model_call(monkeypatch):
    monkeypatch.setattr(config, "TP_DAILY_SPEND_CAP_USD", 5.0)
    conn = AiConn(spent=5.0)
    await events.append_event(conn, T1, S1, "add_component", {"component_id": "c1"})
    calls: list = []
    body = json.loads((await call("POST", f"/trip/{T1}/narrate", {"session_id": S1},
                                  conn=conn, narrator=_counting_narrator(calls)))["body"])
    assert calls == []
    assert body["source"] == "basic" and body["ai_generated"] is False
    assert body["ai_budget_exhausted"] is True
    assert body["narration"] == "Day 1: Hanoi."
    assert conn.cache == {}                                  # a fallback is never cached


@pytest.mark.asyncio
async def test_narrate_over_the_cap_with_master_days_is_labelled_master_basic():
    """Review of the S239 lane: AA master text is AI-assisted, so a budget fallback that still
    carries master days is not 'basic' (which has no label) but 'master_basic'."""
    itinerary = [{"day": 1, "name": "Old town", "source_tour_id": "t1", "source_day_index": 6},
                 {"day": 2, "name": "Night market", "source_tour_id": "t1", "source_day_index": 7}]
    narration, source, _ = await handler._narrate(
        None, itinerary, None, day_texts={("t1", 6): "Day 6 — Old town: wander the streets."})
    assert source == "master_basic"
    assert narration.splitlines() == ["Day 1: Old town: wander the streets.", "Day 2: Night market."]
    assert "master_basic" in handler._AI_SOURCES and "master" in handler._AI_SOURCES
    assert "basic" not in handler._AI_SOURCES


@pytest.mark.asyncio
async def test_narrate_degrades_to_basic_when_every_model_fails(monkeypatch):
    """AA-642 S0 (root ADR 0009 decision 5): a provider outage is 200 basic narration with ai_paused,
    not a 502; every failed attempt is still logged, and the fallback is never cached."""
    from backend.shared import llm_gateway

    conn = AiConn()
    await events.append_event(conn, T1, S1, "add_component", {"component_id": "c1"})
    recorded = []

    async def fake_load_route(db, stage):
        return llm_gateway.builtin_route(stage)

    def failing_generate_text(route, system, user, max_tokens, failures=None):
        for position in (0, 1):  # e.g. two models of the route, both failing on acc3
            failures.append(llm_gateway._failed(route, route.models[0], "acc3", position, "throttled"))
        raise llm_gateway.BedrockError("All models failed")

    async def fake_record(db, call, quality_signal, **kw):
        recorded.append((call.account, call.failed, call.stop_reason))

    monkeypatch.setattr(handler.llm_gateway, "load_route", fake_load_route)
    monkeypatch.setattr(agent.llm_gateway, "generate_text", failing_generate_text)
    monkeypatch.setattr(handler.llm_gateway, "record", fake_record)
    resp = await call("POST", f"/trip/{T1}/narrate", {"session_id": S1}, conn=conn)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["source"] == "basic" and body["ai_paused"] is True and body["ai_generated"] is False
    assert "ai_budget_exhausted" not in body
    assert body["narration"] == "Day 1: Hanoi."
    assert recorded == [("acc3", True, "error"), ("acc3", True, "error")]
    assert conn.cache == {}


@pytest.mark.asyncio
async def test_narrate_pauses_ai_when_the_spend_counter_is_unreadable(monkeypatch):
    """AA-642 S0 (root ADR 0009 decision 6): spend unreadable = no model call, 200 basic + ai_paused."""
    class UnreadableSpend(AiConn):
        async def fetchrow(self, query, *args):
            if "FROM tripplanner.llm_spend_daily" in " ".join(query.split()):
                raise RuntimeError("permission denied for table llm_spend_daily")
            return await super().fetchrow(query, *args)

    conn = UnreadableSpend()
    await events.append_event(conn, T1, S1, "add_component", {"component_id": "c1"})
    calls: list = []
    body = json.loads((await call("POST", f"/trip/{T1}/narrate", {"session_id": S1},
                                  conn=conn, narrator=_counting_narrator(calls)))["body"])
    assert calls == []
    assert body["source"] == "basic" and body["ai_paused"] is True
    assert "ai_budget_exhausted" not in body and conn.cache == {}


@pytest.mark.asyncio
async def test_narrate_pauses_ai_when_the_route_is_unreadable(monkeypatch, capsys):
    from backend.shared import llm_gateway

    async def paused(db, stage):
        raise llm_gateway.AiPaused("route_unreadable", "permission denied")

    def no_model(*a, **kw):
        raise AssertionError("no model call when the route is unreadable")

    monkeypatch.setattr(handler.llm_gateway, "load_route", paused)
    monkeypatch.setattr(agent.llm_gateway, "generate_text", no_model)
    conn = AiConn()
    await events.append_event(conn, T1, S1, "add_component", {"component_id": "c1"})
    resp = await call("POST", f"/trip/{T1}/narrate", {"session_id": S1}, conn=conn)
    body = json.loads(resp["body"])
    assert resp["statusCode"] == 200 and body["source"] == "basic" and body["ai_paused"] is True
    evt = [json.loads(x) for x in capsys.readouterr().out.splitlines()
           if x.startswith('{"evt": "narration_degraded"')]
    assert evt and evt[-1]["reason"] == "route_unreadable"


@pytest.mark.asyncio
async def test_narrate_paused_with_master_days_is_master_basic(monkeypatch):
    """AA-642 S0: the degrade keeps AA master days (AI-assisted label) and fills the rest by name."""
    from backend.shared import llm_gateway

    async def paused(db, stage):
        raise llm_gateway.AiPaused("route_unreadable")

    def build(itinerary, day_texts):
        # day 1 from AA master text, the other stop has none and needs the (paused) model
        return "Day 1: Old town walk.", [e for e in itinerary if e["day"] != 1]

    monkeypatch.setattr(handler.llm_gateway, "load_route", paused)
    monkeypatch.setattr(handler.master_content, "build_narration", build)
    conn = AiConn()
    await events.append_event(conn, T1, S1, "add_component", {"component_id": "c1"})
    await events.append_event(conn, T1, S1, "add_component", {"component_id": "c3"})
    itinerary = await events.current_itinerary(conn, T1)
    assert {e["day"] for e in itinerary} != {1}               # the fixture spans 2 days
    body = json.loads((await call("POST", f"/trip/{T1}/narrate", {"session_id": S1}, conn=conn))["body"])
    assert body["source"] == "master_basic" and body["ai_paused"] is True


@pytest.mark.asyncio
async def test_an_all_master_narration_needs_no_cache_budget_route_or_counter(monkeypatch):
    """AA-1015 (S262 live check: every AA-text narration answered cached=false): a trip whose every day has AA's own
    text is answered before the cache, the spend counter, the route config and the per-session limit. Two identical
    calls give the same text, nothing is stored, and even a spent budget does not flag a degrade that did not happen."""
    def build(itinerary, day_texts):
        return "Day 1: Old town walk.\nDay 2: River market.", []         # every day has AA text

    async def must_not_run(*a, **k):
        raise AssertionError("an all-master narration must not read the budget or the route")

    monkeypatch.setattr(handler.master_content, "build_narration", build)
    monkeypatch.setattr(handler.llm_gateway, "budget_status", must_not_run)
    monkeypatch.setattr(handler.llm_gateway, "load_route", must_not_run)
    conn = RateConn()
    conn.spent = 99.0
    await events.append_event(conn, T1, S1, "add_component", {"component_id": "c1"})
    calls: list = []
    bodies = [json.loads((await call("POST", f"/trip/{T1}/narrate", {"session_id": S1}, conn=conn,
                                     narrator=_counting_narrator(calls)))["body"]) for _ in range(12)]
    assert calls == [] and conn.cache == {} and conn.counters == {}
    assert {b["narration"] for b in bodies} == {"Day 1: Old town walk.\nDay 2: River market."}
    first = bodies[0]
    assert first["source"] == "master" and first["ai_generated"] is True and first["cached"] is False
    assert "ai_budget_exhausted" not in first and "ai_paused" not in first


@pytest.mark.asyncio
async def test_a_mixed_narration_is_cached_and_the_second_identical_call_is_a_hit(monkeypatch):
    """The other side of the master shortcut: a trip with one day of AA text and one day for the model is cached."""
    def build(itinerary, day_texts):
        return "Day 1: Old town walk.", [e for e in itinerary if e["day"] != 1]

    monkeypatch.setattr(handler.master_content, "build_narration", build)
    conn = AiConn()
    await events.append_event(conn, T1, S1, "add_component", {"component_id": "c1"})
    await events.append_event(conn, T1, S1, "add_component", {"component_id": "c3"})
    calls: list = []
    first, second = [json.loads((await call("POST", f"/trip/{T1}/narrate", {"session_id": S1}, conn=conn,
                                            narrator=_counting_narrator(calls)))["body"]) for _ in range(2)]
    assert len(calls) == 1
    assert first["source"] == "mixed" and first["cached"] is False
    assert second["source"] == "mixed" and second["cached"] is True and second["narration"] == first["narration"]


@pytest.mark.asyncio
async def test_narrate_logs_failed_attempts_of_a_defect_and_returns_502(monkeypatch):
    """A non-provider error (a defect, not an outage) is still a 502, with failed attempts logged."""
    from backend.shared import llm_gateway

    conn = AiConn()
    await events.append_event(conn, T1, S1, "add_component", {"component_id": "c1"})
    recorded = []

    async def fake_load_route(db, stage):
        return llm_gateway.builtin_route(stage)

    def broken_generate_text(route, system, user, max_tokens, failures=None):
        failures.append(llm_gateway._failed(route, route.models[0], "acc3", 0, "throttled"))
        raise KeyError("output")

    async def fake_record(db, call, quality_signal, **kw):
        recorded.append((call.account, call.failed))

    monkeypatch.setattr(handler.llm_gateway, "load_route", fake_load_route)
    monkeypatch.setattr(agent.llm_gateway, "generate_text", broken_generate_text)
    monkeypatch.setattr(handler.llm_gateway, "record", fake_record)
    resp = await call("POST", f"/trip/{T1}/narrate", {"session_id": S1}, conn=conn)
    assert resp["statusCode"] == 502
    assert recorded == [("acc3", True)]


# --- AA-1015: per-session narration limit ------------------------------------------------------------------------

class RateConn(AiConn):
    """AiConn without the narration cache (every call is a miss) plus the tp-023 counter, one window."""

    def __init__(self):
        super().__init__()
        self.counters: dict[str, int] = {}

    async def execute(self, query, *args):
        if "INSERT INTO tripplanner.narration_cache" in " ".join(query.split()):
            return "OK"
        return await super().execute(query, *args)

    async def fetchrow(self, query, *args):
        if "INSERT INTO tripplanner.ai_rate_counter" in " ".join(query.split()):
            self.counters[args[0]] = self.counters.get(args[0], 0) + 1
            return {"count": self.counters[args[0]], "retry_after_s": 1800}
        return await super().fetchrow(query, *args)


@pytest.mark.asyncio
async def test_the_11th_narration_in_an_hour_is_429_and_another_session_still_works(capsys):
    conn = RateConn()
    await events.append_event(conn, T1, S1, "add_component", {"component_id": "c1"})
    calls: list = []
    statuses = [(await call("POST", f"/trip/{T1}/narrate", {"session_id": S1}, conn=conn,
                            narrator=_counting_narrator(calls)))["statusCode"] for _ in range(11)]
    assert statuses == [200] * 10 + [429] and len(calls) == 10            # the 11th never reached the model
    limited = await call("POST", f"/trip/{T1}/narrate", {"session_id": S1}, conn=conn,
                         narrator=_counting_narrator(calls))
    assert limited["headers"]["retry-after"] == "1800"
    assert json.loads(limited["body"]) == {"error": "rate_limited", "scope": "narrate", "limit": 10,
                                           "window_s": 3600, "retry_after_s": 1800}
    other_trip, other_session = str(uuid.uuid4()), str(uuid.uuid4())
    conn.sessions[other_session] = _session(other_session)
    await events.append_event(conn, other_trip, other_session, "add_component", {"component_id": "c1"})
    ok = await call("POST", f"/trip/{other_trip}/narrate", {"session_id": other_session}, conn=conn,
                    narrator=_counting_narrator(calls))
    assert ok["statusCode"] == 200
    assert S1 not in json.dumps(conn.counters) and other_session not in json.dumps(conn.counters)  # hashed keys
    line = [json.loads(x) for x in capsys.readouterr().out.splitlines() if x.startswith('{"evt": "ai_rate_limited"')]
    assert line and line[0]["scope"] == "narrate" and S1 not in json.dumps(line)


@pytest.mark.asyncio
async def test_narrations_that_need_no_model_are_not_counted():
    """Over the daily cap (no model call) the limiter is not touched, so it never refuses a basic answer."""
    conn = RateConn()
    conn.spent = 99.0
    await events.append_event(conn, T1, S1, "add_component", {"component_id": "c1"})
    for _ in range(12):
        resp = await call("POST", f"/trip/{T1}/narrate", {"session_id": S1}, conn=conn,
                          narrator=_counting_narrator([]))
        assert resp["statusCode"] == 200
    assert conn.counters == {}


@pytest.mark.asyncio
async def test_an_unwritable_counter_lets_the_narration_through(capsys):
    """AA-1015 fail mode: the limiter fails open (ERROR line); the spend cap still guards the budget."""
    conn = AiConn()   # no ai_rate_counter: the upsert returns no row
    await events.append_event(conn, T1, S1, "add_component", {"component_id": "c1"})
    resp = await call("POST", f"/trip/{T1}/narrate", {"session_id": S1}, conn=conn,
                      narrator=_counting_narrator([]))
    assert resp["statusCode"] == 200
    assert '"evt": "ai_rate_counter_error"' in capsys.readouterr().out
