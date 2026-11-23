"""AA-1152 (offline): the AA add-ons of each day card (backend/assembly/day_addons.py), the traveller's picks and the
lead's day_plan[].addons (lead.submitted.v1 1.2), and the agency's own prices (backend/shared/agency_prices.py,
tripplanner.agency_prices.v1: the M2M PUT, the reads on the agency's pages). Invented places, tours and agencies only.
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import jsonschema
import pytest

from backend import config
from backend.assembly import day_addons, events, leads, planner_admin
from backend.shared import agency_prices, m2m_auth

TENANT = "5c6d7e8f-0000-4000-8000-000000001152"
TRIP = "6d7e8f90-0000-4000-8000-000000001152"
SV1 = "sv:7d9f8b00-0000-4000-8000-0000000000c1"
SV2 = "sv:7d9f8b00-0000-4000-8000-0000000000c2"
SV3 = "sv:7d9f8b00-0000-4000-8000-0000000000c3"
BAY, HILL, ISLE = ("d1520000-0000-4000-8000-0000000000d1", "d1520000-0000-4000-8000-0000000000d2",
                   "d1520000-0000-4000-8000-0000000000d3")
T0 = datetime(2027, 2, 1, 8, tzinfo=timezone.utc)

ITINERARY = [
    {"day": 1, "component_id": "c1", "destination_id": HILL, "source_tour_id": "SYN-TOUR-0042", "source_day_index": 1},
    {"day": 1, "component_id": "c2", "destination_id": ISLE, "source_tour_id": "SYN-TOUR-0042", "source_day_index": 1},
    {"day": 2, "component_id": "c3", "destination_id": ISLE, "source_tour_id": None, "source_day_index": None},
]


def _addon(cid, dest, title, **over):
    row = {"component_id": cid, "destination_id": dest, "title": title, "unit": "person", "duration_minutes": 180,
           "min_pax": 1, "operating_weekdays": [1, 3, 5], "start_times": ["17:30"], "featured": False, "sort_order": 0,
           "service_date": None}
    row.update(over)
    return row


ROWS = [_addon(SV1, BAY, "Evening lantern walk"), _addon(SV2, ISLE, "Kelp island kayak"),
        _addon(SV3, ISLE, "Reef snorkel", featured=True)]


class _Conn:
    def __init__(self, events_rows=(), overnight=None, addons=None, prices=None, addons_error=None):
        self.events_rows, self.added = list(events_rows), []
        self.overnight = [{"source_id": "SYN-TOUR-0042", "day_index": 1, "overnight_destination_id": BAY}] \
            if overnight is None else overnight
        self.addons, self.prices, self.addons_error = ROWS if addons is None else addons, prices, addons_error

    def transaction(self):
        @asynccontextmanager
        async def tx():
            yield

        return tx()

    async def fetch(self, sql, *args):
        if sql == day_addons.OVERNIGHT_SQL:
            return self.overnight
        if sql == day_addons.ADDONS_SQL:
            if self.addons_error:
                raise self.addons_error
            return [r for r in self.addons if r["destination_id"] in args[0]]
        if sql == day_addons.PICKS_SQL:
            return self.events_rows
        raise AssertionError(sql)

    async def fetchrow(self, sql, *args):
        if sql == agency_prices.READ_SQL:
            return self.prices
        raise AssertionError(sql)


class _Missing(Exception):
    sqlstate = "42703"


def test_day_places_sleep_first_then_visited():
    places = day_addons.day_places(ITINERARY, {("SYN-TOUR-0042", 1): BAY})
    assert places == {1: [(BAY, "sleep"), (HILL, "visited"), (ISLE, "visited")], 2: [(ISLE, "sleep")]}
    # No AA tour day for the stop: the day's last stop is where the traveller sleeps.
    assert day_addons.day_places(ITINERARY, {})[1] == [(ISLE, "sleep"), (HILL, "visited")]


@pytest.mark.asyncio
async def test_each_day_lists_the_sleep_place_add_ons_first_each_once(monkeypatch):
    days = await day_addons.offered(_Conn(), ITINERARY)
    assert [a["component_id"] for a in days[1]] == [SV1, SV3, SV2]  # sleep place, then visited (featured first)
    assert [a["at"] for a in days[1]] == ["sleep", "visited", "visited"]
    assert [a["component_id"] for a in days[2]] == [SV3, SV2]
    # Before Booking bk-070 (no title column) or bk-068 (no view): no add-on, never an error.
    assert await day_addons.offered(_Conn(addons_error=_Missing()), ITINERARY) == {1: [], 2: []}


@pytest.mark.asyncio
async def test_picks_fold_and_reach_the_lead(monkeypatch):
    rows = [{"event_type": "add_addon", "payload": json.dumps({"day": 1, "component_id": SV1, "title": "Evening lantern walk"})},
            {"event_type": "add_addon", "payload": {"day": 2, "component_id": SV2, "title": "Kelp island kayak"}},
            {"event_type": "remove_addon", "payload": {"day": 2, "component_id": SV2}}]
    conn = _Conn(events_rows=rows)
    assert await day_addons.picks(conn, TRIP) == {(1, SV1): "Evening lantern walk"}
    summary = {"days": 2, "countries": ["Vietnam"], "tours": [],
               "day_plan": [{"day": 1, "stops": ["Harbour Hill"]}, {"day": 2, "stops": ["Kelp Island"]}]}
    out = await day_addons.attach(conn, TRIP, summary)
    assert out["day_plan"][0]["addons"] == [{"component_id": SV1, "title": "Evening lantern walk"}]
    assert "addons" not in out["day_plan"][1]


def test_a_lead_with_an_add_on_says_1_2_and_validates():
    schema = json.loads((Path(__file__).parent / "contracts" / "lead.submitted.v1.json").read_text())
    answers = {"name": "Ada Fernwick", "email": "ada@example.test", "phone": None, "locale": "en", "adults": 2,
               "child_ages": [], "start_date": None, "budget_band": None, "message": None, "marketing": False}
    with_addon = {"days": 2, "countries": ["Vietnam"], "tours": [],
                  "day_plan": [{"day": 1, "stops": ["Harbour Hill"],
                                "addons": [{"component_id": SV1, "title": "Evening lantern walk"}]}]}
    route = leads.Route(tenant_id=TENANT, slug="lantern-travel", name="Lantern Travel", channel="email")
    lead = leads.build_lead(route=route, trip_id=TRIP, answers=answers, summary=with_addon, now=T0)
    assert lead["version"] == "1.2"
    jsonschema.validate(lead, schema)
    plain = {**with_addon, "day_plan": [{"day": 1, "stops": ["Harbour Hill"]}]}
    assert leads.build_lead(route=route, trip_id=TRIP, answers=answers, summary=plain, now=T0)["version"] == "1.0"
    subject, text = leads.email_text(lead, "Lantern Travel")
    assert f"  Add-on: Evening lantern walk ({SV1})" in text


def test_the_add_on_events_are_valid_trip_events():
    assert {"add_addon", "remove_addon"} <= events.VALID_EVENTS


# --- the agency's own prices -------------------------------------------------------------------------------------------

BODY = {"show_prices": True, "currency": "USD", "computed_at": "2027-02-01T08:00:00Z",
        "tours": [{"source_id": "SYN-TOUR-0042", "from_per_person": "1235"}],
        "addons": [{"component_id": SV1, "from_per_person": "42"}]}


def test_the_price_list_is_read_field_by_field():
    show, cur, tours, addons, at = agency_prices.parse(BODY)
    assert (show, cur, tours, addons, at) == (True, "USD", {"SYN-TOUR-0042": "1235"}, {SV1: "42"}, T0)
    for bad in ({**BODY, "b2b_price": "1"}, {**BODY, "currency": "usd"}, {**BODY, "computed_at": "2027-02-01"},
                {**BODY, "tours": [{"source_id": "SYN", "from_per_person": "12.345"}]},
                {**BODY, "addons": [{"component_id": "tp:1", "from_per_person": "4"}]},
                {**BODY, "addons": [{"component_id": SV1, "from_per_person": "4", "markup_pct": "20"}]},
                {**BODY, "show_prices": "yes"}):
        with pytest.raises(agency_prices.Invalid):
            agency_prices.parse(bad)


class _PlannerConn:
    def __init__(self, kept=True):
        self.kept, self.stored = kept, []

    async def fetchrow(self, sql, *args):
        assert sql == planner_admin.TENANT_SQL
        return {"tenant_id": TENANT, "ai_sessions_monthly": None}

    async def fetchval(self, sql, *args):
        assert sql == agency_prices.UPSERT_SQL
        self.stored.append(args)
        return T0 if self.kept else None

    async def execute(self, sql, *args):
        pass

    async def fetch(self, sql, *args):
        return []

    def transaction(self):
        @asynccontextmanager
        async def tx():
            yield

        return tx()


async def _put(body, conn):
    @asynccontextmanager
    async def acquire():
        yield conn

    async def verify(_auth, scope=None):
        assert scope == m2m_auth.SCOPE_LEADS_MANAGE
        return "crm-client"

    out = await planner_admin.serve("PUT", f"/trip/m2m/v1/tenants/{TENANT}/planner/prices", body, "Bearer x",
                                    acquire=acquire, verify=verify)
    return out["statusCode"], json.loads(out["body"])


@pytest.mark.asyncio
async def test_the_crm_puts_its_list_and_an_older_one_is_stale():
    conn = _PlannerConn()
    status, body = await _put(BODY, conn)
    assert status == 200 and body["tour_count"] == 1 and body["addon_count"] == 1 and body["show_prices"] is True
    scope, show, cur, tours, addons, at = conn.stored[0]
    assert scope == f"tenant:{TENANT}" and json.loads(addons) == {SV1: "42"} and at == T0
    assert (await _put(BODY, _PlannerConn(kept=False)))[0:2] == (409, {"error": "stale"})
    assert (await _put({**BODY, "currency": "dollars"}, _PlannerConn()))[0:2] == (422, {"error": "invalid"})
    assert planner_admin.METHODS["prices"] == "PUT"


class _ReadConn:
    def __init__(self, row=None, error=None):
        self.row, self.error = row, error

    async def fetchrow(self, sql, *args):
        if self.error:
            raise self.error
        return self.row


class _Absent(Exception):
    sqlstate = "42P01"


@pytest.mark.asyncio
async def test_pages_show_the_agency_price_only_when_switched_on():
    row = {"show_prices": True, "currency": "USD", "tours": json.dumps({"SYN-TOUR-0042": "1235"}),
           "addons": {SV1: "42"}}
    prices = await agency_prices.read(_ReadConn(row), TENANT)
    assert prices.tour("SYN-TOUR-0042") == {"from_per_person": "1235", "currency": "USD"}
    assert prices.addon(SV1) == {"from_per_person": "42", "currency": "USD"} and prices.addon(SV2) is None
    assert await agency_prices.read(_ReadConn({**row, "show_prices": False}), TENANT) is None
    assert await agency_prices.read(_ReadConn(None), TENANT) is None
    assert await agency_prices.read(_ReadConn(error=_Absent()), TENANT) is None
    assert await agency_prices.read(_ReadConn(row), config.DEMO_TENANT_ID) is None  # AA's planner: never a price
    assert await agency_prices.read(_ReadConn(row), None) is None


# --- the routes (day_addons.serve, the handler's guards passed in) ---------------------------------------------------

def _resp(status, body):
    return {"statusCode": status, "body": json.dumps(body, default=str)}


async def _no_guard(*_a, **_k):
    return None


async def _deny(*_a, **_k):
    return _resp(404, {"error": "not_found"})


@pytest.mark.asyncio
async def test_routes_read_pick_and_unpick(monkeypatch):
    conn = _Conn()

    async def itinerary(_conn, _trip):
        return ITINERARY

    appended = []

    async def append(_conn, trip, session, kind, payload):
        appended.append((kind, payload))
        conn.events_rows.append({"event_type": kind, "payload": payload})
        return ITINERARY

    async def fallback(method, path, body, **kw):
        return _resp(418, {"path": path})

    monkeypatch.setattr(day_addons.events_mod, "current_itinerary", itinerary)
    monkeypatch.setattr(day_addons.events_mod, "append_event", append)
    monkeypatch.setattr(day_addons.tenant, "current_id", lambda: TENANT)

    async def call(method, path, body=None, guard=_no_guard, read_guard=_no_guard):
        out = await day_addons.serve(fallback, guard, read_guard, _resp, method, path, body or {}, conn=conn,
                                     guest_token="g", caller=None)
        return out["statusCode"], json.loads(out["body"])

    status, view = await call("GET", f"/trip/{TRIP}/addons")
    assert status == 200 and [d["day"] for d in view["days"]] == [1, 2] and view["prices_shown"] is False
    assert view["days"][0]["addons"][0] == {**view["days"][0]["addons"][0], "component_id": SV1, "selected": False,
                                            "price": None}
    status, view = await call("POST", f"/trip/{TRIP}/addons", {"session_id": "s1", "day": 1, "component_id": SV1})
    assert status == 200 and view["days"][0]["addons"][0]["selected"] is True
    assert appended[-1] == ("add_addon", {"day": 1, "component_id": SV1, "title": "Evening lantern walk"})
    # Not sold at day 2's places: refused, nothing written; a malformed pick is 400.
    assert (await call("POST", f"/trip/{TRIP}/addons", {"session_id": "s1", "day": 2, "component_id": SV1}))[0] == 409
    assert (await call("POST", f"/trip/{TRIP}/addons", {"session_id": "s1", "day": 2, "component_id": "x"}))[0] == 400
    assert len(appended) == 1
    status, view = await call("DELETE", f"/trip/{TRIP}/addons/1/{SV1}", {"session_id": "s1"})
    assert status == 200 and appended[-1] == ("remove_addon", {"day": 1, "component_id": SV1})
    assert view["days"][0]["addons"][0]["selected"] is False
    # The guards answer first; any other path goes on to the handler's route().
    assert (await call("GET", f"/trip/{TRIP}/addons", read_guard=_deny))[0] == 404
    assert (await call("POST", f"/trip/{TRIP}/addons", {"session_id": "s1", "day": 1, "component_id": SV1},
                       guard=_deny))[0] == 404
    assert await call("GET", f"/trip/{TRIP}") == (418, {"path": f"/trip/{TRIP}"})
