"""AA-673 — Tour Graph read model, rebuilt from tripplanner.tour_day (AA-675).

Suggestions must follow routes of tours we actually sell, and a trip may chain several tours
(PR-11). This module turns the day-by-day skeleton into a graph:

  stop      a (tour, day) and where the traveller is: the overnight destination, else the
            itinerary_components destination with the most components that day
  node      a destination with at least one stop
  edge      A -> B when a tour goes from stop A to a different stop B next (days without a stop
            are skipped over); weighted by the number of tours taking that step
  leg       every span [day_from, day_to] (day_to > day_from) of one tour whose first and last
            days have a stop — the unit an advisor can sell
  junction  two destinations within `junction_km` (default 150): where one tour's leg can end and
            another's begin. Same destination = implicit junction, not stored.

Built in Python (pure functions below, ~1.5k stops) and written in full in one transaction. Runs in
the assembly Lambda as extraction op "tour_graph"; no model call, no cost.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable

from backend.assembly.sequencing import _haversine  # noqa: PLC2701 — same great-circle km everywhere

DEFAULT_JUNCTION_KM = 150.0
INTENSITY_ORDER = ["leisurely", "moderate", "active", "strenuous"]

# Every day of an active tour, with its overnight destination and the main component destination
# (most components that day, ties by name). Components of unpublished tours are ignored.
STOP_ROWS_SQL = """
    WITH active AS (
        SELECT pt.tour_id::text AS tour_id FROM gold_aa_internal.published_tours pt
        WHERE pt.master_status = 'active'
    ), comp AS (
        SELECT c.source_tour_id, c.source_day_index, c.destination_id,
               row_number() OVER (PARTITION BY c.source_tour_id, c.source_day_index
                                  ORDER BY count(*) DESC, d.name) AS rn
        FROM tripplanner.itinerary_components c
        JOIN shared.destinations d ON d.id = c.destination_id
        JOIN active a ON a.tour_id = c.source_tour_id
        GROUP BY c.source_tour_id, c.source_day_index, c.destination_id, d.name
    )
    SELECT td.source_tour_id AS tour_id, td.day_index, td.overnight_destination_id,
           cp.destination_id AS component_destination_id
    FROM tripplanner.tour_day td
    JOIN active a ON a.tour_id = td.source_tour_id
    LEFT JOIN comp cp ON cp.source_tour_id = td.source_tour_id
                     AND cp.source_day_index = td.day_index AND cp.rn = 1
    ORDER BY td.source_tour_id, td.day_index
"""

COMPONENT_ROWS_SQL = """
    SELECT c.source_tour_id AS tour_id, c.source_day_index AS day_index, c.destination_id,
           c.activity, c.intensity_level, c.season_months
    FROM tripplanner.itinerary_components c
    JOIN gold_aa_internal.published_tours pt
      ON pt.tour_id::text = c.source_tour_id AND pt.master_status = 'active'
"""

DESTINATIONS_SQL = """
    SELECT id, name, country, lat, lng FROM shared.destinations WHERE id = ANY($1::uuid[])
"""


@dataclass(frozen=True)
class Stop:
    tour_id: str
    day_index: int
    destination_id: object
    source: str  # overnight | component


@dataclass
class Graph:
    stops: list[Stop] = field(default_factory=list)
    nodes: list[dict] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)
    legs: list[dict] = field(default_factory=list)
    junctions: list[dict] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


def choose_stops(rows: Iterable[dict]) -> list[Stop]:
    """One stop per (tour, day): the overnight destination, else the main component destination."""
    out = []
    for r in rows:
        if r["overnight_destination_id"] is not None:
            out.append(Stop(r["tour_id"], r["day_index"], r["overnight_destination_id"], "overnight"))
        elif r.get("component_destination_id") is not None:
            out.append(Stop(r["tour_id"], r["day_index"], r["component_destination_id"], "component"))
    return out


def _by_tour(stops: Iterable[Stop]) -> dict[str, list[Stop]]:
    tours: dict[str, list[Stop]] = defaultdict(list)
    for s in stops:
        tours[s.tour_id].append(s)
    for seq in tours.values():
        seq.sort(key=lambda s: s.day_index)
    return tours


def _km(dests: dict, a, b) -> float:
    da, db = dests[a], dests[b]
    return _haversine(da["lat"], da["lng"], db["lat"], db["lng"])


def build_edges(stops: Iterable[Stop], dests: dict) -> list[dict]:
    steps: dict[tuple, set] = defaultdict(set)
    for tour_id, seq in _by_tour(stops).items():
        for prev, nxt in zip(seq, seq[1:]):
            if prev.destination_id != nxt.destination_id:
                steps[(prev.destination_id, nxt.destination_id)].add(tour_id)
    return [{"from_destination_id": a, "to_destination_id": b, "tour_count": len(t),
             "tour_ids": sorted(t), "km": round(_km(dests, a, b), 1)}
            for (a, b), t in sorted(steps.items(), key=lambda kv: (-len(kv[1]), str(kv[0])))]


def _dedupe_consecutive(values: Iterable) -> list:
    out: list = []
    for v in values:
        if not out or out[-1] != v:
            out.append(v)
    return out


def build_legs(stops: Iterable[Stop], dests: dict, components: Iterable[dict]) -> list[dict]:
    """Every span between two stop days of the same tour (day_to > day_from)."""
    acts: dict[tuple, set] = defaultdict(set)  # (tour, day) -> activities
    for c in components:
        acts[(c["tour_id"], c["day_index"])].add(c["activity"])
    legs = []
    for tour_id, seq in _by_tour(stops).items():
        for i in range(len(seq)):
            for j in range(i + 1, len(seq)):
                span = seq[i:j + 1]
                day_from, day_to = span[0].day_index, span[-1].day_index
                dest_ids = _dedupe_consecutive(s.destination_id for s in span)
                countries = list(dict.fromkeys(dests[d]["country"] for d in dest_ids))
                activities = sorted(set().union(*(acts.get((tour_id, d), set())
                                                  for d in range(day_from, day_to + 1))))
                legs.append({"source_tour_id": tour_id, "day_from": day_from, "day_to": day_to,
                             "days": day_to - day_from + 1,
                             "start_destination_id": span[0].destination_id,
                             "end_destination_id": span[-1].destination_id,
                             "destination_ids": dest_ids, "countries": countries,
                             "activities": activities})
    return legs


def build_junctions(dest_ids: Iterable, dests: dict, max_km: float) -> list[dict]:
    """Destination pairs within `max_km`, both directions."""
    ids = sorted(set(dest_ids), key=str)
    out = []
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            km = _km(dests, a, b)
            if km <= max_km:
                cross = dests[a]["country"] != dests[b]["country"]
                out.append({"destination_a": a, "destination_b": b, "km": round(km, 1), "cross_border": cross})
                out.append({"destination_a": b, "destination_b": a, "km": round(km, 1), "cross_border": cross})
    return out


def build_nodes(stops: Iterable[Stop], dests: dict, components: Iterable[dict]) -> list[dict]:
    tours: dict = defaultdict(set)
    count: dict = defaultdict(int)
    for s in stops:
        tours[s.destination_id].add(s.tour_id)
        count[s.destination_id] += 1
    acts: dict = defaultdict(set)
    levels: dict = defaultdict(set)
    months: dict = defaultdict(set)
    for c in components:
        d = c["destination_id"]
        if d in tours:
            acts[d].add(c["activity"])
            if c.get("intensity_level") in INTENSITY_ORDER:
                levels[d].add(c["intensity_level"])
            months[d].update(c.get("season_months") or [])
    nodes = []
    for d in sorted(tours, key=str):
        lv = sorted(levels[d], key=INTENSITY_ORDER.index)
        nodes.append({"destination_id": d, "name": dests[d]["name"], "country": dests[d]["country"],
                      "lat": dests[d]["lat"], "lng": dests[d]["lng"], "tour_ids": sorted(tours[d]),
                      "stop_count": count[d], "activities": sorted(acts[d]),
                      "intensity_min": lv[0] if lv else None, "intensity_max": lv[-1] if lv else None,
                      "season_months": sorted(months[d])})
    return nodes


def _per_country(g: Graph, dests: dict) -> dict:
    stats: dict = defaultdict(lambda: {"nodes": 0, "edges": 0, "legs": 0, "junctions": 0})
    for n in g.nodes:
        stats[n["country"]]["nodes"] += 1
    for e in g.edges:
        stats[dests[e["from_destination_id"]]["country"]]["edges"] += 1
    for leg in g.legs:
        for c in leg["countries"]:
            stats[c]["legs"] += 1
    for j in g.junctions:
        stats[dests[j["destination_a"]]["country"]]["junctions"] += 1  # directed rows
    return dict(sorted(stats.items()))


def build_graph(stop_rows: Iterable[dict], dests: dict, components: list[dict],
                junction_km: float = DEFAULT_JUNCTION_KM) -> Graph:
    stops = [s for s in choose_stops(stop_rows) if s.destination_id in dests]
    g = Graph(stops=stops)
    g.nodes = build_nodes(stops, dests, components)
    g.edges = build_edges(stops, dests)
    g.legs = build_legs(stops, dests, components)
    g.junctions = build_junctions((n["destination_id"] for n in g.nodes), dests, junction_km)
    g.stats = {"tours": len({s.tour_id for s in stops}), "stops": len(stops),
               "stops_from_components": sum(1 for s in stops if s.source == "component"),
               "nodes": len(g.nodes), "edges": len(g.edges), "legs": len(g.legs),
               "junction_pairs": len(g.junctions) // 2,
               "cross_border_pairs": sum(1 for j in g.junctions if j["cross_border"]) // 2,
               "per_country": _per_country(g, dests)}
    return g


_TABLES = ["tour_junction", "tour_leg", "tour_graph_edge", "tour_graph_node", "tour_stop"]


async def persist_graph(db, g: Graph, dests: dict, params: dict) -> None:
    """Replaces the whole graph in one transaction (DELETE, not TRUNCATE: the role has arwd only)."""
    async with db.transaction():
        for t in _TABLES:
            await db.execute(f"DELETE FROM tripplanner.{t}")
        if g.stops:
            await db.executemany(
                "INSERT INTO tripplanner.tour_stop (source_tour_id, day_index, destination_id, country, source) "
                "VALUES ($1, $2, $3, $4, $5)",
                [(s.tour_id, s.day_index, s.destination_id, dests[s.destination_id]["country"], s.source)
                 for s in g.stops])
        if g.nodes:
            await db.executemany(
                "INSERT INTO tripplanner.tour_graph_node (destination_id, name, country, lat, lng, tour_ids, "
                "stop_count, activities, intensity_min, intensity_max, season_months) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)",
                [(n["destination_id"], n["name"], n["country"], n["lat"], n["lng"], n["tour_ids"],
                  n["stop_count"], n["activities"], n["intensity_min"], n["intensity_max"],
                  n["season_months"]) for n in g.nodes])
        if g.edges:
            await db.executemany(
                "INSERT INTO tripplanner.tour_graph_edge (from_destination_id, to_destination_id, tour_count, "
                "tour_ids, km) VALUES ($1, $2, $3, $4, $5)",
                [(e["from_destination_id"], e["to_destination_id"], e["tour_count"], e["tour_ids"], e["km"])
                 for e in g.edges])
        if g.legs:
            await db.executemany(
                "INSERT INTO tripplanner.tour_leg (source_tour_id, day_from, day_to, days, start_destination_id, "
                "end_destination_id, destination_ids, countries, activities) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
                [(leg["source_tour_id"], leg["day_from"], leg["day_to"], leg["days"],
                  leg["start_destination_id"], leg["end_destination_id"], leg["destination_ids"],
                  leg["countries"], leg["activities"]) for leg in g.legs])
        if g.junctions:
            await db.executemany(
                "INSERT INTO tripplanner.tour_junction (destination_a, destination_b, km, cross_border) "
                "VALUES ($1, $2, $3, $4)",
                [(j["destination_a"], j["destination_b"], j["km"], j["cross_border"]) for j in g.junctions])
        await db.execute("INSERT INTO tripplanner.tour_graph_build (params, stats) VALUES ($1::jsonb, $2::jsonb)",
                         json.dumps(params), json.dumps(g.stats))


async def rebuild(db, junction_km: float = DEFAULT_JUNCTION_KM) -> dict:
    stop_rows = [dict(r) for r in await db.fetch(STOP_ROWS_SQL)]
    components = [dict(r) for r in await db.fetch(COMPONENT_ROWS_SQL)]
    ids = {r["overnight_destination_id"] for r in stop_rows} | {r["component_destination_id"] for r in stop_rows}
    ids.discard(None)
    dests = {r["id"]: dict(r) for r in await db.fetch(DESTINATIONS_SQL, list(ids))} if ids else {}
    g = build_graph(stop_rows, dests, components, junction_km)
    await persist_graph(db, g, dests, {"junction_km": junction_km})
    return g.stats


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

