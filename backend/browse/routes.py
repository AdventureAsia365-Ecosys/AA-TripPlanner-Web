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

AA-712 slice 6b: the tour graph comes from the CIS catalog views (migration 228, contract catalog.graph.v1:
v_tour_stop / v_tour_leg / v_tour_junction / v_tour_component, coordinates from v_destination, titles from
v_tour); since AA-712 X4 that is the only source (the frozen tripplanner tables are gone). Tours are keyed by
catalog source_id only (stable across re-uploads); a proposal's segment and day ``tour_id`` is the tour's
current catalog tour_id, with ``source_id`` alongside. The views are live-only and need no AA tenant.
"""
from __future__ import annotations

import json
from typing import Any, Optional


SLACK = 3
MAX_PROPOSALS = 5

# --- AA-712 slice 6b: the reads over the CIS catalog views ------------------------------------------
# The tour key is aliased ``tour_id`` so rank() / day_by_day() stay generic; proposals() relabels it to the current
# catalog tour_id at the end.
SINGLE_SQL = """
    WITH span AS MATERIALIZED (
        SELECT source_id, min(day_index) AS first_day, max(day_index) AS last_day
        FROM catalog.v_tour_stop GROUP BY source_id
    )
    SELECT DISTINCT ON (l.source_id)
           l.source_id AS tour_id, l.day_from, l.day_to, l.days, cardinality(l.destination_ids) AS places,
           (l.day_from = sp.first_day AND l.day_to = sp.last_day) AS whole
    FROM catalog.v_tour_leg l JOIN span sp USING (source_id)
    WHERE $1 = ANY(l.countries) AND l.days BETWEEN $2::int - $3::int AND $2::int
    ORDER BY l.source_id, (l.day_from = sp.first_day AND l.day_to = sp.last_day) DESC,
             abs(l.days - $2::int), l.day_from
"""

# The first stop of each tour is one span CTE (each view read joins catalog.v_tour, so it is computed once); the
# junction view is read once for the same reason.
# AA-847 (routes gate): the pair join is an EQUALITY. `hop` lists, per first leg a, every place a second
# leg may start at: a's own end (no transfer) and each junction from a's end (a transfer of j.km). The
# earlier form joined every leg pair on the days range alone and then kept the pairs that met at a's end
# or at a junction: 136,636 pairs and a disk-spilling sort on Dev Laos for 48,517 kept rows. The junction
# branch skips a self-pair (destination_b = a's end): the first branch already covers "starts where a
# ends" at 0 km, and the CIS junction table has no CHECK against self-pairs (only the graph builder).
# The ORDER BY ends on the pair's own key, so equal-rank candidates come back in one fixed order (the
# earlier form left ties to the plan, which changed when the join did; rank() keeps the first of a tie).
CHAIN_SQL = """
    WITH span AS MATERIALIZED (
        SELECT source_id, min(day_index) AS first_day FROM catalog.v_tour_stop GROUP BY source_id
    ), legs AS MATERIALIZED (
        SELECT l.source_id, l.day_from, l.day_to, l.days, l.start_destination_id, l.end_destination_id,
               l.destination_ids
        FROM catalog.v_tour_leg l WHERE $1 = ANY(l.countries) AND l.days < $2::int
    ), a AS MATERIALIZED (
        SELECT l.* FROM legs l JOIN span sp ON sp.source_id = l.source_id AND l.day_from = sp.first_day
    ), j AS MATERIALIZED (
        SELECT destination_a, destination_b, km FROM catalog.v_tour_junction
    ), hop AS (
        SELECT a.*, a.end_destination_id AS next_start, NULL::double precision AS km FROM a
        UNION ALL
        SELECT a.*, j.destination_b AS next_start, j.km
        FROM a JOIN j ON j.destination_a = a.end_destination_id AND j.destination_b <> a.end_destination_id
    )
    SELECT h.source_id AS a_tour, h.day_from AS a_from, h.day_to AS a_to, h.days AS a_days,
           b.source_id AS b_tour, b.day_from AS b_from, b.day_to AS b_to, b.days AS b_days,
           coalesce(h.km, 0) AS transfer_km,
           cardinality(h.destination_ids) + cardinality(b.destination_ids) AS places
    FROM hop h
    JOIN legs b ON b.start_destination_id = h.next_start AND b.source_id <> h.source_id
               AND h.days + b.days BETWEEN $2::int - $3::int AND $2::int
    ORDER BY abs(h.days + b.days - $2::int), coalesce(h.km, 0), places DESC,
             h.source_id, h.day_from, h.day_to, b.source_id, b.day_from, b.day_to
    LIMIT 40
"""

DAYS_SQL = """
    SELECT s.source_id AS tour_id, s.day_index, s.destination_id::text AS destination_id,
           d.name, d.lat, d.lng, d.country
    FROM catalog.v_tour_stop s JOIN catalog.v_destination d ON d.destination_id = s.destination_id
    WHERE s.source_id = ANY($1::text[])
    ORDER BY s.source_id, s.day_index
"""

COMPONENTS_SQL = """
    SELECT source_id AS tour_id, day_index, destination_id::text AS destination_id,
           component_id::text AS component_id
    FROM catalog.v_tour_component
    WHERE source_id = ANY($1::text[]) AND embedding IS NOT NULL
    ORDER BY source_id, day_index, component_id
"""

# Current tour_id + title per source_id (catalog.v_tour: no RLS for readers, no tenant needed).
TOURS_BY_SOURCE_SQL = """
    SELECT source_id, tour_id::text AS tour_id, title FROM catalog.v_tour
    WHERE source_id = ANY($1::text[])
"""
SOURCE_BY_TOUR_SQL = "SELECT source_id FROM catalog.v_tour WHERE tour_id = $1::uuid"
# Scoped to the cis candidate reads' transaction (see proposals()).
FORCE_CUSTOM_PLAN_SQL = "SET LOCAL plan_cache_mode = force_custom_plan"


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
    # AA-847: plan the two candidate reads for THESE arguments every time. asyncpg caches the
    # prepared statement, and after five runs Postgres may switch to a generic plan whose 1-row
    # estimates nest-loop over the catalog views (EXPLAIN 11.7 ms against 841 ms timed on Dev).
    # SET LOCAL ends with this transaction: the other readers and the rest of the pooled
    # connection's life keep the default plan_cache_mode.
    async with conn.transaction():
        await conn.execute(FORCE_CUSTOM_PLAN_SQL)
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
    current: dict[str, str] = {}  # source_id -> current catalog tour_id
    if tours:
        for r in await conn.fetch(TOURS_BY_SOURCE_SQL, tours):
            names[r["source_id"]], current[r["source_id"]] = r["title"], r["tour_id"]
    out = []
    for p in picked:
        segments = [{"tour_id": t, "tour_name": names.get(t), "day_from": a, "day_to": b,
                     "days": b - a + 1} for t, a, b in p["segments"]]
        day_list = day_by_day(p, stops, comps)
        for row in (*segments, *day_list):
            row["source_id"] = row["tour_id"]
            row["tour_id"] = current.get(row["source_id"], row["source_id"])
        out.append({"total_days": p["total_days"], "transfers": p["transfers"], "transfer_km": p["transfer_km"],
                    "whole_tour": p["whole"], "segments": segments, "days": day_list})
    return {"country": country, "days": days, "proposals": out}


# AA-812: the whole route of ONE tour, so its page can open the planner pre-filled ("Customise this
# trip"). Same proposal shape as `proposals()`; the title comes from the CIS catalog view (ADR 0001),
# not gold_aa_internal. catalog.v_tour has no RLS for readers (the view reads with its owner's rights).
TOUR_TITLE_SQL = "SELECT title FROM catalog.v_tour WHERE tour_id = $1::uuid"
# tour_id changes when CIS re-uploads a tour; source_id does not. A tour page's link carries both,
# so a cached or bookmarked "Customise" link still finds the CURRENT tour_id.
TOUR_ID_BY_SOURCE_SQL = "SELECT tour_id::text FROM catalog.v_tour WHERE source_id = $1 LIMIT 1"

# AA-812 hotfix (D2). Second way in when the Tour Graph has no route for a tour: the tour's own places from the
# catalog (catalog.v_tour_place, the same destination ids the planner's components use) and the planner's
# experiences at those places.
TOUR_ROUTE_PLACES_SQL = """
    SELECT day_number, position, destination_id::text AS destination_id, destination_name AS name,
           lat, lng, country
    FROM catalog.v_tour_place
    WHERE tour_id = $1::uuid AND day_number IS NOT NULL AND destination_id IS NOT NULL
      AND lat IS NOT NULL AND lng IS NOT NULL
    ORDER BY day_number, position
"""
# Planner experiences at those places; the tour's own components (its source_id) first.
PLACE_COMPONENTS_SQL = """
    SELECT destination_id::text AS destination_id, component_id::text AS component_id
    FROM catalog.v_tour_component
    WHERE destination_id = ANY($1::uuid[]) AND embedding IS NOT NULL
    ORDER BY destination_id, (source_id = ANY($2::text[])) DESC, source_id, day_index, component_id
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
              comps: dict[tuple, list[dict]], match: str, span: Optional[tuple[int, int]] = None,
              tour_days: Optional[int] = None) -> dict:
    """One tour as one proposal over `span` (default: its first to last stop day).

    AA-990 F2: `tour_days` is the tour's own length (TOUR_DAYS_COUNT_SQL), `pinned_days` the days that carry a
    component to pin; both are additive keys (the planner ignores them, the coverage rule and the audit read them)."""
    first, last = span or (min(r["day_index"] for r in stops_rows), max(r["day_index"] for r in stops_rows))
    p = {"segments": [(tour_id, first, last)]}
    days = day_by_day(p, {tour_id: stops_rows}, comps)
    return {"total_days": last - first + 1, "transfers": 0, "transfer_km": 0.0, "whole_tour": True,
            "match": match,
            "segments": [{"tour_id": tour_id, "tour_name": title, "day_from": first, "day_to": last,
                          "days": last - first + 1}],
            "days": days,
            "tour_days": tour_days or last - first + 1,
            "pinned_days": sum(1 for d in days if d["component_id"])}


def _graph_span(stops_rows: list[dict], tour_days: int) -> tuple[int, int]:
    """AA-990 F2: a Tour Graph route covers every tour day, 1..N. A stop-less arrival or departure day has no stop
    in the graph (stop = the overnight destination, else the day's main place), so sizing the route from the first
    to the last stop day cut those days off. Leading days before the first stop come out without a place, trailing
    days carry the last stop (day_by_day, as any stop-less day inside the tour); none of them is pinned unless the
    graph has a component on that day."""
    first = min(r["day_index"] for r in stops_rows)
    last = max(r["day_index"] for r in stops_rows)
    return min(1, first), max(tour_days, last)


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


async def _places_route(conn: Any, tour_id: str, keys: list[str],
                        tour_days: Optional[int] = None) -> Optional[tuple[list[dict], dict]]:
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
    if tour_days is None:
        tour_days = await conn.fetchval(TOUR_DAYS_COUNT_SQL, tour_id)
    total_days = tour_days or len(by_day)
    if len(comps) < PLACES_MIN_DAY_SHARE * total_days:
        return None
    return stops_rows, comps


async def tour_route(conn: Any, tour_id: str) -> Optional[dict]:
    """The tour's days as one proposal, or None when the planner has nothing for it.

    1. the Tour Graph under the tour's catalog source_id (it survives re-uploads) — match "tour_graph", over every
       tour day 1..N (AA-990 F2);
    2. else the tour's catalog places mapped onto planner experiences at those places — match
       "places".
    Both follow the S236 rule: a route must pin at least PLACES_MIN_DAY_SHARE of its days. A graph route under the
    share gives way to the places route when that one pins more days; else None (the planner shows the day list)."""
    source_id = await conn.fetchval(SOURCE_BY_TOUR_SQL, tour_id)
    keys = [source_id] if source_id else []
    tour_days = await conn.fetchval(TOUR_DAYS_COUNT_SQL, tour_id) or 0

    def _with_source(p: dict) -> dict:
        # segments / days carry the catalog source_id next to the current tour_id, as in proposals()
        if source_id:
            for row in (*p["segments"], *p["days"]):
                row["source_id"] = source_id
        return p

    async def _titled(p: dict) -> dict:
        p["segments"][0]["tour_name"] = await conn.fetchval(TOUR_TITLE_SQL, tour_id)
        return _with_source(p)

    graph = None
    for key in keys:
        found = await _graph_route(conn, key, tour_id)
        if found:
            graph = _proposal(tour_id, None, found[0], found[1], "tour_graph",
                              span=_graph_span(found[0], tour_days), tour_days=tour_days)
            break
    # AA-990 F2: the S236 share rule applies to the graph too (it used to accept any graph with one pin anywhere,
    # e.g. a 4-day tour whose only stop day had no component got a route that pinned nothing).
    if graph and graph["pinned_days"] >= PLACES_MIN_DAY_SHARE * graph["total_days"]:
        return await _titled(graph)
    found = await _places_route(conn, tour_id, keys, tour_days=tour_days)
    if found:
        places = _proposal(tour_id, None, found[0], found[1], "places", tour_days=tour_days)
        if graph is None or places["pinned_days"] > graph["pinned_days"]:
            return await _titled(places)
    return None


async def tour_fallback(conn: Any, tour_id: str) -> Optional[dict]:
    """What the planner shows when a tour has no route: its title, country, length and day titles."""
    row = await conn.fetchrow(TOUR_FALLBACK_SQL, tour_id)
    if not row:
        return None
    days = row["days"]
    return {"title": row["title"], "country": row["country"], "duration_days": row["duration_days"],
            "days": json.loads(days) if isinstance(days, str) else (days or [])}
