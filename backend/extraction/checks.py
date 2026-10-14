"""Read-only checks over the frozen TripPlanner place tables (AA-712 slice 6a, ADR 0007).

CIS owns the place pipeline now (itinerary places, geocoding, tour graph: AA-CIS-App
services/places, schema geo). This repo no longer writes shared.destinations,
tripplanner.itinerary_components, tour_day, tour_stop, tour_graph_*, tour_leg or tour_junction;
those tables stay frozen (readers keep using them until the PLACES_SOURCE flip, slices 6b/7).
What is left here only reads them, to show how far the frozen copy lags the live catalog:

  list_tours   active tours, `stale` when tour_day is missing or was built from other text;
  orphans      component / tour_day rows of tours that are no longer active (AA-731);
  neighbours   the AA-673 "where next" sample query over the frozen graph, with its latency.
"""
from __future__ import annotations

import hashlib

from backend.shared import catalog_rls


def source_hash(text: str) -> str:
    """sha256 of a tour's aa_itineraries, as stored in tour_day.source_hash when it was built."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


async def list_tours(db) -> list[dict]:
    """Active tours with `stale` = True when tour_day is missing or was built from other text."""
    rows = await catalog_rls.fetch_catalog(db, catalog_rls.ACTIVE_TOURS_SQL)
    out = []
    for r in rows:
        prev = await db.fetchval(
            "SELECT min(source_hash) FROM tripplanner.tour_day WHERE source_tour_id = $1", r["tour_id"])
        out.append({"tour_id": r["tour_id"], "country": r["country"], "name": r["aa_name"],
                    "stale": prev != source_hash(r["aa_itineraries"] or "")})
    return out


async def orphans(db) -> dict:
    """AA-731 read-only check: rows still pointing at tours that are no longer active (e.g. after a
    rerun wave or a Master trash). Nothing here deletes them any more; they disappear with the
    frozen tables once readers use the CIS views."""
    active = [r["tour_id"] for r in await catalog_rls.fetch_catalog(db, catalog_rls.ACTIVE_TOURS_SQL)]
    if not active:
        return {"error": "no active tours visible"}
    rows = await db.fetch(
        "SELECT source_tour_id, count(*) AS n FROM tripplanner.itinerary_components "
        "WHERE NOT (source_tour_id = ANY($1::text[])) GROUP BY source_tour_id ORDER BY n DESC", active)
    tour_day = await db.fetchval(
        "SELECT count(*) FROM tripplanner.tour_day WHERE NOT (source_tour_id = ANY($1::text[]))", active)
    return {"active_tours": len(active), "orphan_components": sum(r["n"] for r in rows),
            "orphan_component_tours": [r["source_tour_id"] for r in rows][:50],
            "orphan_tour_day": tour_day}


# "Where can a trip at D go next and stay coverable by real tours?" A tour steps from D (via = D)
# or from a destination within junction distance of D (via = that destination, the traveller
# transfers there first). One row per next destination, best route first.
NEIGHBOURS_SQL = """
    WITH origins AS (
        SELECT $1::uuid AS via_id, 0::float8 AS transfer_km
        UNION ALL
        SELECT j.destination_b, j.km FROM tripplanner.tour_junction j WHERE j.destination_a = $1::uuid
    ), steps AS (
        SELECT e.to_destination_id, o.via_id, o.transfer_km, e.tour_count, e.tour_ids, e.km,
               row_number() OVER (PARTITION BY e.to_destination_id
                                  ORDER BY o.transfer_km, e.tour_count DESC) AS rn
        FROM origins o
        JOIN tripplanner.tour_graph_edge e ON e.from_destination_id = o.via_id
        WHERE e.to_destination_id <> $1::uuid
    )
    SELECT s.to_destination_id AS destination_id, n.name, n.country, s.km AS step_km,
           nullif(s.via_id, $1::uuid) AS via_destination_id, v.name AS via_name, s.transfer_km,
           s.tour_count, s.tour_ids
    FROM steps s
    JOIN tripplanner.tour_graph_node n ON n.destination_id = s.to_destination_id
    LEFT JOIN tripplanner.tour_graph_node v ON v.destination_id = s.via_id AND s.via_id <> $1::uuid
    WHERE s.rn = 1
    ORDER BY s.transfer_km, s.tour_count DESC, n.name
    LIMIT $2
"""


async def neighbours(db, destination_id: str, limit: int = 20) -> list[dict]:
    return [dict(r) for r in await db.fetch(NEIGHBOURS_SQL, destination_id, limit)]


def jsonable(rows: list[dict]) -> list[dict]:
    """UUIDs -> str for the Lambda response."""
    return [{k: (str(v) if v is not None and type(v).__name__ == "UUID" else v) for k, v in r.items()}
            for r in rows]
