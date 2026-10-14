"""AA-674 / PR-11 — whole-route proposals: leg chains of real AA tours for a country and duration.

"Recommend trip routes that exist in AA data" (Jira PR-11, 26/09): the traveller gives where they
are heading and how many days, and gets complete routes to start from, drawn on the map day by
day. A proposal is either:
  - one leg of one tour (consecutive days of a real tour), or
  - two legs of two different tours chained at a junction (the first leg ends where, or within
    150 km of where, the second begins; AA-673 tour_junction), the first leg starting at its
    tour's first stop.
Its total days fall between `days - SLACK` and `days`: the traveller can add days later (PR-11
point 3). A whole tour (its first to last stop, sold as is) ranks first; then closeness to the
asked duration, fewer transfers, more places seen. Stateless read, cacheable per (country, days).
"""
from __future__ import annotations

import json
from typing import Any, Optional

SLACK = 3
MAX_PROPOSALS = 5

SINGLE_SQL = """
    WITH span AS (
        SELECT source_tour_id, min(day_index) AS first_day, max(day_index) AS last_day
        FROM tripplanner.tour_stop GROUP BY source_tour_id
    )
    SELECT DISTINCT ON (l.source_tour_id)
           l.source_tour_id AS tour_id, l.day_from, l.day_to, l.days, cardinality(l.destination_ids) AS places,
           (l.day_from = sp.first_day AND l.day_to = sp.last_day) AS whole
    FROM tripplanner.tour_leg l JOIN span sp USING (source_tour_id)
    WHERE $1 = ANY(l.countries) AND l.days BETWEEN $2::int - $3::int AND $2::int
    ORDER BY l.source_tour_id, (l.day_from = sp.first_day AND l.day_to = sp.last_day) DESC,
             abs(l.days - $2::int), l.day_from
"""

CHAIN_SQL = """
    WITH a AS (
        SELECT l.* FROM tripplanner.tour_leg l
        WHERE $1 = ANY(l.countries) AND l.days < $2::int
          AND l.day_from = (SELECT min(s.day_index) FROM tripplanner.tour_stop s
                            WHERE s.source_tour_id = l.source_tour_id)
    ), b AS (
        SELECT l.* FROM tripplanner.tour_leg l WHERE $1 = ANY(l.countries) AND l.days < $2::int
    )
    SELECT a.source_tour_id AS a_tour, a.day_from AS a_from, a.day_to AS a_to, a.days AS a_days,
           b.source_tour_id AS b_tour, b.day_from AS b_from, b.day_to AS b_to, b.days AS b_days,
           coalesce(j.km, 0) AS transfer_km,
           cardinality(a.destination_ids) + cardinality(b.destination_ids) AS places
    FROM a
    JOIN b ON b.source_tour_id <> a.source_tour_id AND a.days + b.days BETWEEN $2::int - $3::int AND $2::int
    LEFT JOIN tripplanner.tour_junction j
           ON j.destination_a = a.end_destination_id AND j.destination_b = b.start_destination_id
    WHERE b.start_destination_id = a.end_destination_id OR j.destination_a IS NOT NULL
    ORDER BY abs(a.days + b.days - $2::int), coalesce(j.km, 0), places DESC
    LIMIT 40
"""

DAYS_SQL = """
    SELECT s.source_tour_id AS tour_id, s.day_index, s.destination_id::text AS destination_id,
           d.name, d.lat, d.lng, d.country
    FROM tripplanner.tour_stop s JOIN shared.destinations d ON d.id = s.destination_id
    WHERE s.source_tour_id = ANY($1::text[])
    ORDER BY s.source_tour_id, s.day_index
"""

COMPONENTS_SQL = """
    SELECT source_tour_id AS tour_id, source_day_index AS day_index, destination_id::text AS destination_id,
           id::text AS component_id
    FROM tripplanner.itinerary_components
    WHERE source_tour_id = ANY($1::text[]) AND embedding IS NOT NULL
    ORDER BY source_tour_id, source_day_index, id
"""

TOUR_NAMES_SQL = """
    SELECT tour_id::text AS tour_id, aa_name FROM gold_aa_internal.published_tours
    WHERE tour_id::text = ANY($1::text[])
"""


def rank(singles: list[dict], chains: list[dict], days: int, limit: int = MAX_PROPOSALS) -> list[dict]:
    """Merge single-tour and two-tour candidates into proposals, best first, one per tour set."""
    cands = []
    for s in singles:
        cands.append({"segments": [(s["tour_id"], s["day_from"], s["day_to"])], "total_days": s["days"],
                      "transfers": 0, "transfer_km": 0.0, "places": s["places"], "whole": bool(s.get("whole"))})
    for c in chains:
        cands.append({"segments": [(c["a_tour"], c["a_from"], c["a_to"]), (c["b_tour"], c["b_from"], c["b_to"])],
                      "total_days": c["a_days"] + c["b_days"], "transfers": 1,
                      "transfer_km": round(float(c["transfer_km"]), 1), "places": c["places"], "whole": False})
    # A whole tour first (sold as is), then closeness to the asked days, fewer transfers, more places.
    cands.sort(key=lambda p: (not p["whole"], abs(p["total_days"] - days), p["transfers"], -p["places"],
                              p["transfer_km"]))
    out, seen = [], set()
    for p in cands:
        key = frozenset(t for t, _, _ in p["segments"])
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
        if len(out) == limit:
            break
    return out


def day_by_day(proposal: dict, stops: dict[str, list[dict]],
               components: Optional[dict[tuple, list[dict]]] = None) -> list[dict]:
    """The proposal's days in order, each with where the traveller is (for the map polyline).
    A day without a stop of its own keeps the previous place. `component_id` is a component of
    that tour day to pin when the traveller starts from this route (one at the day's stop when
    there is one), or None when the day has no component."""
    components = components or {}
    out, day, last = [], 0, None
    for tour, d_from, d_to in proposal["segments"]:
        by_day = {s["day_index"]: s for s in stops.get(tour, [])}
        for d in range(d_from, d_to + 1):
            day += 1
            s = by_day.get(d) or last
            last = s or last
            comps = components.get((tour, d), [])
            here = [c for c in comps if s and c["destination_id"] == s["destination_id"]]
            pick = (here or comps or [None])[0]
            out.append({"day": day, "tour_id": tour, "tour_day": d,
                        "component_id": pick["component_id"] if pick else None,
                        **({k: s[k] for k in ("destination_id", "name", "lat", "lng", "country")} if s else {})})
    return out


async def proposals(conn: Any, country: str, days: int) -> dict:
    singles = [dict(r) for r in await conn.fetch(SINGLE_SQL, country, days, SLACK)]
    chains = [dict(r) for r in await conn.fetch(CHAIN_SQL, country, days, SLACK)]
    picked = rank(singles, chains, days)
    tours = sorted({t for p in picked for t, _, _ in p["segments"]})
    stops: dict[str, list[dict]] = {}
    for r in await conn.fetch(DAYS_SQL, tours) if tours else []:
        stops.setdefault(r["tour_id"], []).append(dict(r))
    comps: dict[tuple, list[dict]] = {}
    for r in await conn.fetch(COMPONENTS_SQL, tours) if tours else []:
        comps.setdefault((r["tour_id"], r["day_index"]), []).append(dict(r))
    names: dict[str, str] = {}
    if tours:
        from backend.extraction.tour_days import fetch_catalog  # tenant-scoped (RLS)
        names = {r["tour_id"]: r["aa_name"] for r in await fetch_catalog(conn, TOUR_NAMES_SQL, tours)}
    out = []
    for p in picked:
        out.append({"total_days": p["total_days"], "transfers": p["transfers"], "transfer_km": p["transfer_km"],
                    "whole_tour": p["whole"],
                    "segments": [{"tour_id": t, "tour_name": names.get(t), "day_from": a, "day_to": b,
                                  "days": b - a + 1} for t, a, b in p["segments"]],
                    "days": day_by_day(p, stops, comps)})
    return {"country": country, "days": days, "proposals": out}


# AA-812: the whole route of ONE tour, so its page can open the planner pre-filled ("Customise this
# trip"). Same proposal shape as `proposals()`; the title comes from the CIS catalog view (ADR 0001),
# not gold_aa_internal. catalog.v_tour has no RLS for readers (the view reads with its owner's rights).
TOUR_TITLE_SQL = "SELECT title FROM catalog.v_tour WHERE tour_id = $1::uuid"
# tour_id changes when CIS re-uploads a tour; source_id does not. A tour page's link carries both,
# so a cached or bookmarked "Customise" link still finds the CURRENT tour_id.
TOUR_ID_BY_SOURCE_SQL = "SELECT tour_id::text FROM catalog.v_tour WHERE source_id = $1 LIMIT 1"

# AA-812 hotfix (D2). The Tour Graph (tour_stop / itinerary_components) is keyed by the published
# tour_id of the day it was extracted, and covers only the tours extracted so far (~650 tour_day
# rows against ~890 live catalog tours); a CIS re-upload gives the tour a NEW tour_id the graph
# does not know. So the graph lookup alone answered 404 for most tours. Second way in: the tour's
# own places from the catalog (catalog.v_tour_place, destination_id = shared.destinations, the same
# ids the planner's components use) and the planner's experiences at those places.
TOUR_ROUTE_PLACES_SQL = """
    SELECT day_number, position, destination_id::text AS destination_id, destination_name AS name,
           lat, lng, country
    FROM catalog.v_tour_place
    WHERE tour_id = $1::uuid AND day_number IS NOT NULL AND destination_id IS NOT NULL
      AND lat IS NOT NULL AND lng IS NOT NULL
    ORDER BY day_number, position
"""
# Planner experiences at those places; the tour's own components (any of its known ids) first.
PLACE_COMPONENTS_SQL = """
    SELECT destination_id::text AS destination_id, id::text AS component_id
    FROM tripplanner.itinerary_components
    WHERE destination_id = ANY($1::uuid[]) AND embedding IS NOT NULL
    ORDER BY destination_id, (source_tour_id = ANY($2::text[])) DESC, source_tour_id, source_day_index, id
"""
# A places-built route must pin at least this share of the tour's days (AA-712 fills the rest).
PLACES_MIN_DAY_SHARE = 0.5
TOUR_DAYS_COUNT_SQL = """
    SELECT GREATEST(COALESCE(t.duration_days, 0),
                    (SELECT count(*) FROM catalog.v_tour_day d WHERE d.tour_id = t.tour_id))
    FROM catalog.v_tour t WHERE t.tour_id = $1::uuid
"""
# The tour's day titles, for the planner's "not on the map yet" message.
TOUR_FALLBACK_SQL = """
    SELECT t.title, t.country, t.duration_days,
           COALESCE((SELECT json_agg(json_build_object('day', d.day_number, 'title', d.title)
                                     ORDER BY d.day_number)
                     FROM catalog.v_tour_day d WHERE d.tour_id = t.tour_id), '[]'::json)::text AS days
    FROM catalog.v_tour t WHERE t.tour_id = $1::uuid
"""


async def resolve_tour_id(conn: Any, tour_id: Optional[str], source_id: Optional[str]) -> Optional[str]:
    """The current catalog tour_id: by source_id when given and known, else the tour_id passed."""
    if source_id:
        current = await conn.fetchval(TOUR_ID_BY_SOURCE_SQL, source_id)
        if current:
            return current
    return tour_id


def _proposal(tour_id: str, title: Optional[str], stops_rows: list[dict],
              comps: dict[tuple, list[dict]], match: str) -> dict:
    first = min(r["day_index"] for r in stops_rows)
    last = max(r["day_index"] for r in stops_rows)
    p = {"segments": [(tour_id, first, last)]}
    return {"total_days": last - first + 1, "transfers": 0, "transfer_km": 0.0, "whole_tour": True,
            "match": match,
            "segments": [{"tour_id": tour_id, "tour_name": title, "day_from": first, "day_to": last,
                          "days": last - first + 1}],
            "days": day_by_day(p, {tour_id: stops_rows}, comps)}


async def _graph_route(conn: Any, key: str, tour_id: str) -> Optional[tuple[list[dict], dict]]:
    """Stops + components from the Tour Graph under one tour key, relabelled to `tour_id`."""
    stops_rows = [dict(r) for r in await conn.fetch(DAYS_SQL, [key])]
    if not stops_rows:
        return None
    comps: dict[tuple, list[dict]] = {}
    for r in await conn.fetch(COMPONENTS_SQL, [key]):
        comps.setdefault((tour_id, r["day_index"]), []).append(dict(r))
    if not comps:
        return None
    for r in stops_rows:
        r["tour_id"] = tour_id
    return stops_rows, comps


async def _places_route(conn: Any, tour_id: str, keys: list[str]) -> Optional[tuple[list[dict], dict]]:
    """Stops from the catalog's places per day; each day pinned to a planner experience at its
    place. None when no place of the tour has a planner experience."""
    places = [dict(r) for r in await conn.fetch(TOUR_ROUTE_PLACES_SQL, tour_id)]
    if not places:
        return None
    dest_ids = list(dict.fromkeys(p["destination_id"] for p in places))
    by_dest: dict[str, list[str]] = {}
    for r in await conn.fetch(PLACE_COMPONENTS_SQL, dest_ids, keys):
        by_dest.setdefault(r["destination_id"], []).append(r["component_id"])
    if not by_dest:
        return None
    by_day: dict[int, list[dict]] = {}
    for p in places:
        by_day.setdefault(p["day_number"], []).append(p)
    stops_rows: list[dict] = []
    comps: dict[tuple, list[dict]] = {}
    used: set[str] = set()
    for day in sorted(by_day):
        # The day's first place the planner has experiences at, else its first place (shown, unpinned).
        ps = by_day[day]
        stop = next((p for p in ps if p["destination_id"] in by_dest), ps[0])
        stops_rows.append({"tour_id": tour_id, "day_index": day, "destination_id": stop["destination_id"],
                           "name": stop["name"], "lat": stop["lat"], "lng": stop["lng"],
                           "country": stop["country"]})
        cands = by_dest.get(stop["destination_id"], [])
        # A different experience per day where the place has several (a 3-night stay is not one pin).
        pick = next((c for c in cands if c not in used), cands[0] if cands else None)
        if pick:
            used.add(pick)
            comps[(tour_id, day)] = [{"destination_id": stop["destination_id"], "component_id": pick}]
    # S236 (Nghiệp): only offer a places-built route when it pins at least half of the tour's days;
    # a thinner one (e.g. 2 of 10) misrepresents the tour, so the planner shows the day list instead.
    total_days = await conn.fetchval(TOUR_DAYS_COUNT_SQL, tour_id) or len(by_day)
    if len(comps) < PLACES_MIN_DAY_SHARE * total_days:
        return None
    return stops_rows, comps


async def tour_route(conn: Any, tour_id: str, also: tuple[Optional[str], ...] = ()) -> Optional[dict]:
    """The tour's first to last day as one proposal, or None when the planner has nothing for it.

    1. the Tour Graph under the current tour_id, then under the other ids the link carried (an older
       tour_id the graph was extracted under) — match "tour_graph";
    2. else the tour's catalog places mapped onto planner experiences at those places — match
       "places"."""
    keys = list(dict.fromkeys(k for k in (tour_id, *also) if k))
    title = None
    for key in keys:
        found = await _graph_route(conn, key, tour_id)
        if found:
            title = await conn.fetchval(TOUR_TITLE_SQL, tour_id)
            return _proposal(tour_id, title, found[0], found[1], "tour_graph")
    found = await _places_route(conn, tour_id, keys)
    if found:
        title = await conn.fetchval(TOUR_TITLE_SQL, tour_id)
        return _proposal(tour_id, title, found[0], found[1], "places")
    return None


async def tour_fallback(conn: Any, tour_id: str) -> Optional[dict]:
    """What the planner shows when a tour has no route: its title, country, length and day titles."""
    row = await conn.fetchrow(TOUR_FALLBACK_SQL, tour_id)
    if not row:
        return None
    days = row["days"]
    return {"title": row["title"], "country": row["country"], "duration_days": row["duration_days"],
            "days": json.loads(days) if isinstance(days, str) else (days or [])}
