"""AA-1041 slice 2 — an invented catalog and the fakes the planning chat tests share (test_aa1041_plan_chat*.py).

Every name here is invented (countries Norland and Tavora, their places and experiences): no text from the catalog,
an audit or a prompt. TEST ONLY: backend/tests/ is dropped from the Lambda zip.
"""
from __future__ import annotations

from typing import Any

from backend.assembly import chat_tools
from backend.shared import monthly_spend


def _cid(n: int) -> str:
    return f"e0000000-0000-4000-8000-00000000000{n}"


EXPERIENCES = [
    {"component_id": _cid(1), "name": "Lantern Market Walk", "place": "Port Selby", "country": "Norland",
     "activity": "cultural_heritage"},
    {"component_id": _cid(2), "name": "Mistral Bay Kayaking", "place": "Mistral Bay", "country": "Norland",
     "activity": "water_activities"},
    {"component_id": _cid(3), "name": "Saffron Kitchen Cooking Class", "place": "Port Selby", "country": "Norland",
     "activity": "culinary"},
    {"component_id": _cid(4), "name": "Ember Hills Tea Trail", "place": "Ember Hills", "country": "Norland",
     "activity": "trekking"},
    {"component_id": _cid(5), "name": "Cloudgate Temple Visit", "place": "Cloudgate", "country": "Tavora",
     "activity": "cultural_heritage"},
    {"component_id": _cid(6), "name": "Opal River Cruise", "place": "Opal River", "country": "Tavora",
     "activity": "water_activities"},
]
BY_NAME = {e["name"]: e for e in EXPERIENCES}
BY_ID = {e["component_id"]: e for e in EXPERIENCES}

TOUR_COAST = "f0000000-0000-4000-8000-000000000001"
TOUR_TEMPLES = "f0000000-0000-4000-8000-000000000002"
TOURS = [
    {"tour_id": TOUR_COAST, "title": "Norland Coast and Hills", "country": "Norland", "duration_days": 7,
     "summary": "Lantern markets, coast kayaking and tea trails"},
    {"tour_id": TOUR_TEMPLES, "title": "Tavora Temples and River", "country": "Tavora", "duration_days": 5,
     "summary": "Temples and a river cruise"},
]
COUNTRIES = [
    {"country": "Norland", "tour_count": 14, "min_days": 3, "max_days": 12},
    {"country": "Tavora", "tour_count": 6, "min_days": 2, "max_days": 8},
]
OFFERED = [{"country": c["country"], "tours": c["tour_count"], "min_days": c["min_days"],
            "max_days": c["max_days"]} for c in COUNTRIES]

ROUTE_DAYS = {
    "Norland": [(1, "Port Selby", _cid(1)), (2, "Mistral Bay", _cid(2)), (3, "Ember Hills", _cid(4))],
    "Tavora": [(1, "Cloudgate", _cid(5)), (2, "Opal River", _cid(6))],
}
TOUR_DAYS = {
    TOUR_COAST: [(1, "Port Selby", _cid(1)), (2, "Mistral Bay", _cid(2)), (3, "Port Selby", _cid(3)),
                 (4, "Ember Hills", _cid(4))],
    TOUR_TEMPLES: [(1, "Cloudgate", _cid(5)), (2, "Opal River", _cid(6))],
}


def _proposal(days: list[tuple], title: str) -> dict:
    return {"total_days": len(days), "transfers": 0, "transfer_km": 0.0, "whole_tour": False,
            "segments": [{"tour_id": "x", "tour_name": title, "day_from": 1, "day_to": len(days),
                          "days": len(days)}],
            "days": [{"day": d, "name": place, "component_id": cid} for d, place, cid in days]}


async def fake_proposals(conn: Any, country: str, days: int) -> dict:
    found = ROUTE_DAYS.get(country, [])[:days]
    title = "Norland Coast and Hills" if country == "Norland" else "Tavora Temples and River"
    return {"country": country, "days": days, "proposals": [_proposal(found, title)] if found else []}


async def fake_tour_route(conn: Any, tour_id: str) -> Any:
    days = TOUR_DAYS.get(tour_id)
    return _proposal(days, "") if days else None


def itinerary(names: list[str]) -> list[dict]:
    """A trip projection (events.current_itinerary shape) of these experiences, one per day."""
    return [{"day": i + 1, "component_id": BY_NAME[n]["component_id"], "id": BY_NAME[n]["component_id"],
             "name": n, "destination_name": BY_NAME[n]["place"]} for i, n in enumerate(names)]


class CatalogConn:
    """Answers the chat tools' catalog reads and the monthly spend table from memory (the same filters as the SQL)."""

    def __init__(self) -> None:
        self.month: dict[tuple[str, str], tuple[float, int]] = {}
        self.month_unreadable = False
        self.month_write_fails = False
        self.sql: list[str] = []

    async def fetch(self, query: str, *args: Any) -> list[dict]:
        self.sql.append(query)
        if query == chat_tools.COUNTRIES_SQL:
            return [dict(c) for c in COUNTRIES]
        if query == chat_tools.FIND_TOURS_SQL:
            country, words, max_days, limit = args
            out = [t for t in TOURS
                   if (country is None or t["country"].lower() == country.lower())
                   and all(w in f"{t['title']} {t['summary']}".lower() for w in (words or []))
                   and (max_days is None or t["duration_days"] <= max_days)]
            return sorted(out, key=lambda t: (t["duration_days"], t["title"]))[:limit]
        if query == chat_tools.FIND_EXPERIENCES_SQL:
            country, words, limit = args
            out = [e for e in EXPERIENCES
                   if (country is None or e["country"].lower() == country.lower())
                   and all(w in f"{e['name']} {e['activity']} {e['place']}".lower() for w in words)]
            return sorted(out, key=lambda e: (e["name"].lower(), e["place"]))[:limit]
        if query == chat_tools.COMPONENT_NAMES_SQL:
            return [dict(BY_ID[i]) for i in args[0] if i in BY_ID]
        if query == monthly_spend.READ_SQL:
            if self.month_unreadable:
                raise RuntimeError("relation tripplanner.ai_spend_month does not exist")
            stage, scopes = args
            return [{"scope": s, "cost_usd": self.month[(stage, s)][0], "calls": self.month[(stage, s)][1]}
                    for s in scopes if (stage, s) in self.month]
        raise AssertionError(f"unexpected fetch: {query[:80]}")

    async def execute(self, query: str, *args: Any) -> str:
        self.sql.append(query)
        if query == monthly_spend.ADD_SQL:
            if self.month_write_fails:
                raise RuntimeError("write failed")
            stage, scopes, usd, calls = args
            for s in scopes:
                cost, n = self.month.get((stage, s), (0.0, 0))
                self.month[(stage, s)] = (cost + float(usd), n + int(calls))
            return "INSERT 0 1"
        raise AssertionError(f"unexpected execute: {query[:80]}")
