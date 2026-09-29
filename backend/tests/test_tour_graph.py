"""AA-673 — Tour Graph build: stops, edges, legs, junctions, nodes, stats, persist.

No DB: pure functions on a small Bhutan fixture, and a recorder DB for persist_graph."""
from __future__ import annotations

import json
from contextlib import asynccontextmanager

from backend.extraction import tour_graph as tg

DESTS = {
    "paro": {"id": "paro", "name": "Paro", "country": "Bhutan", "lat": 27.43, "lng": 89.41},
    "thimphu": {"id": "thimphu", "name": "Thimphu", "country": "Bhutan", "lat": 27.47, "lng": 89.64},
    "punakha": {"id": "punakha", "name": "Punakha", "country": "Bhutan", "lat": 27.59, "lng": 89.87},
    "phuentsholing": {"id": "phuentsholing", "name": "Phuentsholing", "country": "Bhutan", "lat": 26.85, "lng": 89.39},
    "jaigaon": {"id": "jaigaon", "name": "Jaigaon", "country": "India", "lat": 26.85, "lng": 89.38},
    "leh": {"id": "leh", "name": "Leh", "country": "India", "lat": 34.16, "lng": 77.58},
}


def _row(tour, day, overnight=None, component=None):
    return {"tour_id": tour, "day_index": day, "overnight_destination_id": overnight,
            "component_destination_id": component}


# t1: Paro -> Thimphu (2 nights) -> Punakha, departure day without a stop
# t2: Thimphu (component only) -> Punakha -> Paro
# t3: Phuentsholing -> Jaigaon (cross the border)
ROWS = [
    _row("t1", 1, "paro"), _row("t1", 2, "thimphu"), _row("t1", 3, "thimphu"), _row("t1", 4, "punakha"),
    _row("t1", 5),
    _row("t2", 1, None, "thimphu"), _row("t2", 2, "punakha", "thimphu"), _row("t2", 3, "paro"),
    _row("t3", 1, "phuentsholing"), _row("t3", 2, "jaigaon"),
]
COMPONENTS = [
    {"tour_id": "t1", "day_index": 1, "destination_id": "paro", "activity": "cultural_heritage",
     "intensity_level": "leisurely", "season_months": [3, 4]},
    {"tour_id": "t1", "day_index": 4, "destination_id": "punakha", "activity": "trekking",
     "intensity_level": "active", "season_months": [10]},
    {"tour_id": "t2", "day_index": 3, "destination_id": "paro", "activity": "trekking",
     "intensity_level": "strenuous", "season_months": [4, 5]},
]


def test_choose_stops_prefers_overnight_then_component_and_skips_empty_days():
    stops = {(s.tour_id, s.day_index): s for s in tg.choose_stops(ROWS)}
    assert stops[("t2", 1)].destination_id == "thimphu" and stops[("t2", 1)].source == "component"
    assert stops[("t2", 2)].destination_id == "punakha" and stops[("t2", 2)].source == "overnight"
    assert ("t1", 5) not in stops


def test_edges_skip_stays_and_count_tours_per_step():
    edges = {(e["from_destination_id"], e["to_destination_id"]): e
             for e in tg.build_edges(tg.choose_stops(ROWS), DESTS)}
    assert set(edges) == {("paro", "thimphu"), ("thimphu", "punakha"), ("punakha", "paro"),
                          ("phuentsholing", "jaigaon")}
    assert edges[("thimphu", "punakha")]["tour_count"] == 2
    assert edges[("thimphu", "punakha")]["tour_ids"] == ["t1", "t2"]
    assert 20 < edges[("paro", "thimphu")]["km"] < 30


def test_edges_bridge_a_day_without_a_stop():
    rows = [_row("t", 1, "paro"), _row("t", 2), _row("t", 3, "punakha")]
    edges = tg.build_edges(tg.choose_stops(rows), DESTS)
    assert [(e["from_destination_id"], e["to_destination_id"]) for e in edges] == [("paro", "punakha")]


def test_legs_are_every_span_between_stop_days():
    legs = {(leg["source_tour_id"], leg["day_from"], leg["day_to"]): leg
            for leg in tg.build_legs(tg.choose_stops(ROWS), DESTS, COMPONENTS)}
    assert sorted(k for k in legs if k[0] == "t1") == [
        ("t1", 1, 2), ("t1", 1, 3), ("t1", 1, 4), ("t1", 2, 3), ("t1", 2, 4), ("t1", 3, 4)]
    whole = legs[("t1", 1, 4)]
    assert whole["days"] == 4
    assert whole["destination_ids"] == ["paro", "thimphu", "punakha"]  # the 2-night stay counted once
    assert whole["start_destination_id"] == "paro" and whole["end_destination_id"] == "punakha"
    assert whole["activities"] == ["cultural_heritage", "trekking"]
    assert legs[("t1", 2, 3)]["destination_ids"] == ["thimphu"]
    assert legs[("t3", 1, 2)]["countries"] == ["Bhutan", "India"]


def test_junctions_are_symmetric_within_the_threshold_and_flag_the_border():
    js = tg.build_junctions(DESTS, DESTS, 150)
    pairs = {(j["destination_a"], j["destination_b"]): j for j in js}
    assert ("paro", "thimphu") in pairs and ("thimphu", "paro") in pairs
    assert pairs[("phuentsholing", "jaigaon")]["cross_border"] is True
    assert pairs[("paro", "thimphu")]["cross_border"] is False
    assert not any("leh" in k for k in pairs)                     # > 1,000 km from everything
    assert ("paro", "phuentsholing") in pairs                     # ~65 km
    assert ("paro", "phuentsholing") not in {(j["destination_a"], j["destination_b"])
                                             for j in tg.build_junctions(DESTS, DESTS, 50)}


def test_nodes_aggregate_tours_activities_intensity_and_seasons():
    nodes = {n["destination_id"]: n for n in tg.build_nodes(tg.choose_stops(ROWS), DESTS, COMPONENTS)}
    paro = nodes["paro"]
    assert paro["tour_ids"] == ["t1", "t2"] and paro["stop_count"] == 2
    assert paro["activities"] == ["cultural_heritage", "trekking"]
    assert (paro["intensity_min"], paro["intensity_max"]) == ("leisurely", "strenuous")
    assert paro["season_months"] == [3, 4, 5]
    assert nodes["thimphu"]["stop_count"] == 3 and nodes["thimphu"]["intensity_min"] is None
    assert "leh" not in nodes                                     # no stop there


def test_build_graph_stats_per_country():
    g = tg.build_graph(ROWS, DESTS, COMPONENTS, 150)
    s = g.stats
    assert (s["tours"], s["stops"], s["stops_from_components"]) == (3, 9, 1)
    assert (s["nodes"], s["edges"]) == (5, 4)
    assert s["cross_border_pairs"] == 4                           # Jaigaon is < 150 km from all 4 Bhutan nodes
    assert s["per_country"]["India"] == {"nodes": 1, "edges": 0, "legs": 1, "junctions": 4}
    assert s["per_country"]["Bhutan"]["legs"] == 6 + 3 + 1


def test_build_graph_drops_stops_whose_destination_is_missing():
    g = tg.build_graph([_row("t", 1, "paro"), _row("t", 2, "gone")], DESTS, [], 150)
    assert [s.destination_id for s in g.stops] == ["paro"] and g.legs == []


class _Db:
    def __init__(self):
        self.log = []
        self.in_tx = False

    @asynccontextmanager
    async def transaction(self):
        self.in_tx = True
        yield
        self.in_tx = False

    async def execute(self, sql, *args):
        self.log.append(("execute", " ".join(sql.split()), args, self.in_tx))

    async def executemany(self, sql, rows):
        self.log.append(("executemany", " ".join(sql.split()), list(rows), self.in_tx))


async def test_persist_replaces_every_table_in_one_transaction():
    g = tg.build_graph(ROWS, DESTS, COMPONENTS, 150)
    db = _Db()
    await tg.persist_graph(db, g, DESTS, {"junction_km": 150})
    assert all(entry[3] for entry in db.log)                      # everything inside the transaction
    deletes = [e[1] for e in db.log if e[1].startswith("DELETE")]
    assert deletes == [f"DELETE FROM tripplanner.{t}" for t in
                       ["tour_junction", "tour_leg", "tour_graph_edge", "tour_graph_node", "tour_stop"]]
    inserted = {e[1].split()[2]: len(e[2]) for e in db.log if e[0] == "executemany"}
    assert inserted == {"tripplanner.tour_stop": 9, "tripplanner.tour_graph_node": 5,
                        "tripplanner.tour_graph_edge": 4, "tripplanner.tour_leg": len(g.legs),
                        "tripplanner.tour_junction": len(g.junctions)}
    build = db.log[-1]
    assert build[1].startswith("INSERT INTO tripplanner.tour_graph_build")
    assert json.loads(build[2][0]) == {"junction_km": 150} and json.loads(build[2][1])["nodes"] == 5


def test_jsonable_turns_uuids_into_strings():
    import uuid
    u = uuid.uuid4()
    assert tg.jsonable([{"a": u, "b": None, "c": 1}]) == [{"a": str(u), "b": None, "c": 1}]


async def test_rebuild_keeps_the_previous_graph_when_no_active_tour_is_visible():
    class _NoTenantDb(_Db):
        async def fetch(self, sql, *args):
            return []                                             # RLS without app.tenant_id

    db = _NoTenantDb()
    assert "error" in await tg.rebuild(db)
    assert not [e for e in db.log if e[1].startswith("DELETE")]
