"""AA-1152 (S285 advisory PR-29 T6, Jira PR-29) — the AA add-ons of each day card, and the traveller's picks.

An add-on is an optional activity AA sells at a place on the days it runs (Booking's contract view
``booking.v_place_addons``, supply.place_addons.v1, SELECT to role tripplanner: an offer with an in-force price under a
published contract, never a price or a supplier). Each day of the trip lists the add-ons of its places: **the place the
traveller sleeps first** (the overnight place of the day's AA tour day, ``catalog.v_tour_day_route``; else the day's
last stop), **then the places visited** that day (its stops), each add-on once per day.

  GET    /trip/{id}/addons                        the days with their add-ons (and the traveller's picks)
  POST   /trip/{id}/addons                        {session_id, day, component_id}: pick one (trip event add_addon)
  DELETE /trip/{id}/addons/{day}/{component_id}   ?session_id=: unpick it (trip event remove_addon)
  GET    /trip/agency-price                       ?source_id=: the agency's own "from" price of one tour (its tour
                                                  page; no trip, no-store, so the switch and a new list show at once)

A pick is a trip event (tp-037), so it survives a reload and goes with the lead (lead.submitted.v1 1.2
``day_plan[].addons``: the sv: id and AA's name, never a price: :func:`attach`). A pick is accepted only for an add-on
offered at that day's places, and its name is AA's (read again here, never the browser's).

On an agency's planner each add-on carries the agency's own "from" price when its CRM published one and its switch is
on (``backend/shared/agency_prices.py``, tripplanner.agency_prices.v1); AA's own planner shows none (root ADR 0019 b5).

Fail soft: the view absent, not granted or without the ``title`` column (before Booking bk-068 / bk-070) = no add-on
(one log line), never an error. The routes run the handler's guards (passed in by :func:`serve`: no import cycle).
"""
from __future__ import annotations

import json
import re
from typing import Any, Awaitable, Callable, Optional

from backend.assembly import events as events_mod
from backend.shared import agency_prices, observability, tenant

ADDONS_RE = re.compile(r"^/trip/([^/]+)/addons$")
ADDON_ITEM_RE = re.compile(r"^/trip/([^/]+)/addons/([0-9]{1,2})/(sv:[0-9a-f-]{36})$")
AGENCY_PRICE_PATH = "/trip/agency-price"
_SV = re.compile(r"^sv:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
MAX_PER_DAY = 12  # technical-constant: the lead's DayPlan.addons maxItems (lead.submitted.v1 1.2)
MAX_DAY = 90  # technical-constant: the lead's DayPlan.day maximum
SOFT_SQLSTATES = ("42P01", "42501", "42703")  # the view absent / not granted / no title column yet

OVERNIGHT_SQL = """
    SELECT r.source_id, r.day_index, r.overnight_destination_id::text AS overnight_destination_id
    FROM catalog.v_tour_day_route r
    JOIN unnest($1::text[], $2::int[]) AS k (source_id, day_index)
      ON k.source_id = r.source_id AND k.day_index = r.day_index
    WHERE r.overnight_destination_id IS NOT NULL
"""
# Each offer once per place, with its first day it can be sold (the view lists today onwards).
ADDONS_SQL = """
    SELECT DISTINCT ON (v.offer_id, v.destination_id)
           v.component_id, v.destination_id::text AS destination_id, v.title, v.unit, v.duration_minutes, v.min_pax,
           v.operating_weekdays, v.start_times, v.featured, v.sort_order, v.service_date
    FROM booking.v_place_addons v
    WHERE v.destination_id = ANY($1::uuid[])
    ORDER BY v.offer_id, v.destination_id, v.service_date
"""
PICKS_SQL = ("SELECT event_type, payload FROM tripplanner.trip_events "
             "WHERE trip_id = $1 AND event_type IN ('add_addon', 'remove_addon') ORDER BY id ASC")


async def _soft_fetch(conn: Any, sql: str, *args: Any, evt: str) -> list:
    """Rows, or [] when the view is absent, not granted or older than this code (logged once per call)."""
    try:
        if not hasattr(conn, "transaction"):
            return list(await conn.fetch(sql, *args))
        async with conn.transaction():
            return list(await conn.fetch(sql, *args))
    except Exception as exc:  # noqa: BLE001 — only the type and SQLSTATE are logged
        if getattr(exc, "sqlstate", None) not in SOFT_SQLSTATES:
            raise
        observability.emit(evt, error=type(exc).__name__)
        return []


def day_places(itinerary: list[dict], overnight: dict[tuple[str, int], str]) -> dict[int, list[tuple[str, str]]]:
    """``{day: [(destination_id, "sleep" | "visited")]}``: the sleep place first (the overnight place of the day's last
    stop on an AA tour day, else the last stop), then the stops in order, each place once."""
    by_day: dict[int, list[dict]] = {}
    for e in itinerary:
        by_day.setdefault(int(e["day"]), []).append(e)
    out: dict[int, list[tuple[str, str]]] = {}
    for day, stops in sorted(by_day.items()):
        sleep = None
        for e in reversed(stops):
            key = (str(e.get("source_tour_id") or ""), int(e.get("source_day_index") or -1))
            sleep = overnight.get(key) or sleep
            if sleep:
                break
        sleep = sleep or next((str(e["destination_id"]) for e in reversed(stops) if e.get("destination_id")), None)
        places: list[tuple[str, str]] = [(sleep, "sleep")] if sleep else []
        for e in stops:
            dest = str(e["destination_id"]) if e.get("destination_id") else None
            if dest and dest not in {p for p, _ in places}:
                places.append((dest, "visited"))
        out[day] = places
    return out


def _order(row: Any) -> tuple:
    return (not row["featured"], int(row["sort_order"]), str(row["title"]).casefold())


def per_day(places: dict[int, list[tuple[str, str]]], rows: list) -> dict[int, list[dict]]:
    """The add-ons of each day: those of the sleep place first, then of each visited place; each add-on once a day."""
    by_place: dict[str, list] = {}
    for r in rows:
        by_place.setdefault(str(r["destination_id"]), []).append(r)
    out: dict[int, list[dict]] = {}
    for day, day_places_ in places.items():
        seen: set[str] = set()
        items: list[dict] = []
        for dest, at in day_places_:
            for r in sorted(by_place.get(dest, []), key=_order):
                if r["component_id"] in seen or len(items) >= MAX_PER_DAY:
                    continue
                seen.add(r["component_id"])
                items.append({"component_id": r["component_id"], "title": r["title"], "unit": r["unit"],
                              "duration_minutes": r["duration_minutes"], "min_pax": r["min_pax"],
                              "operating_weekdays": list(r["operating_weekdays"] or []),
                              "start_times": list(r["start_times"] or []), "destination_id": dest, "at": at})
        out[day] = items
    return out


async def picks(conn: Any, trip_id: str) -> dict[tuple[int, str], str]:
    """``{(day, component_id): title}`` the traveller picked (the trip events folded, the last one wins)."""
    out: dict[tuple[int, str], str] = {}
    for r in await conn.fetch(PICKS_SQL, trip_id):
        p = r["payload"]
        p = json.loads(p) if isinstance(p, str) else (p or {})
        key = (int(p.get("day", 0)), str(p.get("component_id", "")))
        if r["event_type"] == "add_addon":
            out[key] = str(p.get("title") or "")
        else:
            out.pop(key, None)
    return out


async def offered(conn: Any, itinerary: list[dict]) -> dict[int, list[dict]]:
    """The add-ons of every day of ``itinerary`` (the current projection)."""
    keys = sorted({(str(e["source_tour_id"]), int(e["source_day_index"])) for e in itinerary
                   if e.get("source_tour_id") and e.get("source_day_index") is not None})
    rows = await _soft_fetch(conn, OVERNIGHT_SQL, [k[0] for k in keys], [k[1] for k in keys],
                             evt="day_route_unreadable") if keys else []
    overnight = {(str(r["source_id"]), int(r["day_index"])): r["overnight_destination_id"] for r in rows}
    places = day_places(itinerary, overnight)
    dests = sorted({d for ps in places.values() for d, _ in ps})
    addons = await _soft_fetch(conn, ADDONS_SQL, dests, evt="place_addons_unreadable") if dests else []
    return per_day(places, addons)


async def view(conn: Any, trip_id: str) -> dict:
    """GET /trip/{id}/addons: every day with its add-ons, the picks and (an agency's planner) its own prices."""
    days = await offered(conn, await events_mod.current_itinerary(conn, trip_id))
    picked = await picks(conn, trip_id)
    prices = await agency_prices.read(conn, tenant.current_id())
    out = []
    for day, items in sorted(days.items()):
        for item in items:
            item["selected"] = (day, item["component_id"]) in picked
            item["price"] = prices.addon(item["component_id"]) if prices else None
        out.append({"day": day, "addons": items})
    return {"trip_id": trip_id, "days": out, "prices_shown": prices is not None}


async def attach(conn: Any, trip_id: str, summary: dict) -> dict:
    """The lead summary with each day's picks (lead.submitted.v1 1.2 ``day_plan[].addons``, at most
    :data:`MAX_PER_DAY` a day, AA's name; never a price). A day without a pick keeps the 1.0 shape."""
    picked = await picks(conn, trip_id)
    for day in summary.get("day_plan") or []:
        chosen = [{"component_id": cid, "title": (title or cid)[:300]} for (d, cid), title in sorted(picked.items())
                  if d == day["day"] and _SV.match(cid)][:MAX_PER_DAY]
        if chosen:
            day["addons"] = chosen
    return summary


async def _pick(conn: Any, trip_id: str, body: dict, guard: Callable[..., Awaitable[Optional[dict]]],
                caller: Any, guest_token: Optional[str], resp: Callable[[int, dict], dict]) -> dict:
    session_id, day, cid = body.get("session_id"), body.get("day"), body.get("component_id")
    if not session_id or not str(day).isdigit() or not 1 <= int(day) <= MAX_DAY or not _SV.match(str(cid or "")):
        return resp(400, {"error": "session_id, day (1-90) and component_id (sv:<uuid>) required"})
    if denied := await guard(conn, trip_id, session_id, guest_token, "addon_add", caller):
        return denied
    days = await offered(conn, await events_mod.current_itinerary(conn, trip_id))
    item = next((a for a in days.get(int(day), []) if a["component_id"] == cid), None)
    if item is None:
        return resp(409, {"error": "addon_not_offered", "detail": "this add-on is not sold at this day's places"})
    await events_mod.append_event(conn, trip_id, session_id, "add_addon",
                                  {"day": int(day), "component_id": cid, "title": str(item["title"])[:300]})
    return resp(200, await view(conn, trip_id))


async def _unpick(conn: Any, trip_id: str, day: int, cid: str, body: dict,
                  guard: Callable[..., Awaitable[Optional[dict]]], caller: Any, guest_token: Optional[str],
                  resp: Callable[[int, dict], dict]) -> dict:
    session_id = body.get("session_id")
    if not session_id:
        return resp(400, {"error": "session_id required"})
    if denied := await guard(conn, trip_id, session_id, guest_token, "addon_remove", caller):
        return denied
    if (day, cid) in await picks(conn, trip_id):
        await events_mod.append_event(conn, trip_id, session_id, "remove_addon", {"day": day, "component_id": cid})
    return resp(200, await view(conn, trip_id))


async def tour_price(conn: Any, source_id: Any) -> Optional[dict]:
    """GET /trip/agency-price: ``{"price": {from_per_person, currency} | null}`` of one tour on the page's agency
    (tenant assertion); None = a malformed ``source_id`` (400)."""
    if not isinstance(source_id, str) or not agency_prices.SOURCE_RE.match(source_id):
        return None
    prices = await agency_prices.read(conn, tenant.current_id())
    return {"price": prices.tour(source_id) if prices else None}


async def serve(fallback: Callable[..., Awaitable[dict]], guard: Callable[..., Awaitable[Optional[dict]]],
                read_guard: Callable[..., Awaitable[Optional[dict]]], resp: Callable[[int, dict], dict],
                method: str, path: str, body: dict, *, conn: Any, guest_token: Optional[str] = None,
                caller: Any = None, **kw: Any) -> dict:
    """The three add-on routes and the tour page's agency price; any other path goes on to ``fallback`` (the
    handler's route())."""
    if path == AGENCY_PRICE_PATH and method == "GET":
        answer = await tour_price(conn, body.get("source_id"))
        return resp(200, answer) if answer is not None else resp(400, {"error": "source_id required"})
    m, item = ADDONS_RE.match(path), ADDON_ITEM_RE.match(path)
    if m and method == "GET":
        if denied := await read_guard(conn, m.group(1), guest_token, "addons", caller):
            return denied
        return resp(200, await view(conn, m.group(1)))
    if m and method == "POST":
        return await _pick(conn, m.group(1), body, guard, caller, guest_token, resp)
    if item and method == "DELETE":
        return await _unpick(conn, item.group(1), int(item.group(2)), item.group(3), body, guard, caller,
                             guest_token, resp)
    return await fallback(method, path, body, conn=conn, guest_token=guest_token, caller=caller, **kw)
