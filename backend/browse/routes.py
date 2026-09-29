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

from typing import Any

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


def day_by_day(proposal: dict, stops: dict[str, list[dict]]) -> list[dict]:
    """The proposal's days in order, each with where the traveller is (for the map polyline).
    A day without a stop of its own keeps the previous place."""
    out, day, last = [], 0, None
    for tour, d_from, d_to in proposal["segments"]:
        by_day = {s["day_index"]: s for s in stops.get(tour, [])}
        for d in range(d_from, d_to + 1):
            day += 1
            s = by_day.get(d) or last
            last = s or last
            out.append({"day": day, "tour_id": tour, "tour_day": d,
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
                    "days": day_by_day(p, stops)})
    return {"country": country, "days": days, "proposals": out}
