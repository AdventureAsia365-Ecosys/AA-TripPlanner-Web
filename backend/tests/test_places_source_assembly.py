"""AA-847 (AA-712 slice 6c) — the assembly readers behind PLACES_SOURCE=legacy|cis. Offline: a recording fake.

On the cis path every assembly place read comes from the CIS catalog views and tripplanner.component_id_map (tp-017);
the trip's own state (trip_events, trip_drafts, sessions) is still tripplanner's. Never the frozen place tables,
shared.destinations, the gold / silver tables or the AA tenant GUC. Ids from before the flip (events of an older
draft, a browse answer cached for 30 minutes) resolve through the id-map, in one place (backend/shared/component_ids).
"""
from __future__ import annotations

import json
import re

import pytest

from backend import config
from backend.assembly import activities, coverage, events, handoff, master_content, suggestions
from backend.shared import component_ids, vector_topk
from backend.tests.test_places_source import RecordingPool

# Place data the cis path must not read (trip state under tripplanner.* is fine).
PLACE_LEGACY = re.compile(r"tripplanner\.(?:itinerary_components|tour_stop|tour_leg|tour_junction|tour_graph_\w+"
                          r"|tour_day)\b|shared\.destinations|gold_aa_internal|silver_aa_internal|app\.tenant_id")

OLD_A = "11111111-1111-4111-8111-111111111111"
OLD_B = "22222222-2222-4222-8222-222222222222"
NEW_A = "aaaaaaaa-aaaa-5aaa-8aaa-aaaaaaaaaaaa"
NEW_B = "bbbbbbbb-bbbb-5bbb-8bbb-bbbbbbbbbbbb"
NEW_C = "cccccccc-cccc-5ccc-8ccc-cccccccccccc"
VTE = "5c6d7e8f-9a0b-4c1d-8e2f-3a4b5c6d7e8f"
LPB = "6d7e8f9a-0b1c-4d2e-8f3a-4b5c6d7e8f9a"
SRC = "664086a9-df2d-481c-a4fa-af67f782a6a8"

CIS_SQL = {
    "events.COMPONENTS_SQL_CIS": events.COMPONENTS_SQL_CIS,
    "activities.ACTIVITIES_SQL_CIS": activities.ACTIVITIES_SQL_CIS,
    "coverage.OPTIONS_SQL_CIS": coverage.OPTIONS_SQL_CIS,
    "coverage.TOUR_NAMES_SQL_CIS": coverage.TOUR_NAMES_SQL_CIS,
    "suggestions.ROUTE_CANDIDATES_SQL_CIS": suggestions.ROUTE_CANDIDATES_SQL_CIS,
    "suggestions.TASTE_SQL_CIS": suggestions.TASTE_SQL_CIS,
    "suggestions.COUNTRY_CLAUSE_CIS": suggestions.COUNTRY_CLAUSE_CIS,
    "master_content.CIS_TEXTS_SQL": master_content.CIS_TEXTS_SQL,
    "vector_topk.topk(CIS)": vector_topk.topk_sql("c.embedding IS NOT NULL", "", ("id", "name"), 2, 6,
                                                  vector_topk.CIS),
    "vector_topk.exact(CIS)": vector_topk.exact_sql("c.embedding IS NOT NULL", "", ("id", "name"), 6,
                                                    vector_topk.CIS),
    "component_ids.MAP_SQL": component_ids.MAP_SQL,
}


@pytest.fixture
def cis(monkeypatch):
    monkeypatch.setattr(config, "PLACES_SOURCE", "cis")


def _cis_only(pool: RecordingPool):
    assert pool.sql
    for q in pool.sql:
        assert not PLACE_LEGACY.search(q), q


def _event(etype, cid):
    return {"event_type": etype, "payload": json.dumps({"component_id": cid})}


def _comp(cid, dest, day, name, lat=17.97, lng=102.6):
    return {"id": cid, "name": name, "activity": "culinary", "duration_hint": "half_day", "text_extract": name,
            "source_tour_id": SRC, "source_day_index": day, "destination_id": dest, "destination_name": name,
            "lat": lat, "lng": lng, "located_by": "geoapify", "precision": "point"}


@pytest.mark.parametrize("name", sorted(CIS_SQL))
def test_every_cis_reader_reads_only_catalog_views_and_the_id_map(name):
    q = CIS_SQL[name]
    assert not PLACE_LEGACY.search(q), q
    assert "catalog.v_" in q or "tripplanner.component_id_map" in q
    assert not re.search(r"tripplanner\.(?!component_id_map\b)", q), q


async def test_events_fold_resolves_ids_from_before_the_flip(cis):
    """add OLD_A, add OLD_B, add NEW_C, remove NEW_A (the new id of OLD_A): OLD_B and NEW_C remain, as catalog ids."""
    pool = RecordingPool([
        ("FROM tripplanner.trip_events", [_event("add_component", OLD_A), _event("add_component", OLD_B),
                                          _event("add_component", NEW_C), _event("remove_component", NEW_A)]),
        ("tripplanner.component_id_map", [{"old_id": OLD_A, "new_id": NEW_A}, {"old_id": OLD_B, "new_id": NEW_B}]),
        ("FROM catalog.v_tour_component", [_comp(NEW_B, VTE, 1, "Vientiane"), _comp(NEW_C, LPB, 2, "Luang Prabang")]),
    ])
    got = await events._current_components(pool, "trip")  # noqa: SLF001
    assert [c["id"] for c in got] == [NEW_B, NEW_C]
    _cis_only(pool)


async def test_events_on_legacy_never_read_the_id_map():
    pool = RecordingPool([("FROM tripplanner.trip_events", [_event("add_component", OLD_A)]),
                          ("FROM tripplanner.itinerary_components", [_comp(OLD_A, VTE, 1, "Vientiane")])])
    got = await events._current_components(pool, "trip")  # noqa: SLF001
    assert [c["id"] for c in got] == [OLD_A]
    assert not any("component_id_map" in q for q in pool.sql)


async def test_a_reorder_from_before_the_flip_keeps_its_order(cis):
    pool = RecordingPool([
        ("event_type IN ('reorder'", [{"event_type": "reorder",
                                       "payload": json.dumps({"ordered_component_ids": [OLD_B, OLD_A]})}]),
        ("tripplanner.component_id_map", [{"old_id": OLD_A, "new_id": NEW_A}, {"old_id": OLD_B, "new_id": NEW_B}]),
    ])
    assert await events._latest_explicit_order(pool, "trip") == [NEW_B, NEW_A]  # noqa: SLF001


async def test_current_itinerary_carries_located_by_and_precision(cis):
    pool = RecordingPool([
        ("FROM tripplanner.trip_events", [_event("add_component", NEW_C)]),
        ("FROM catalog.v_tour_component", [_comp(NEW_C, LPB, 2, "Luang Prabang") | {"precision": "area",
                                                                                   "located_by": "llm"}]),
    ])
    (entry,) = await events.current_itinerary(pool, "trip")
    assert entry["component_id"] == NEW_C and entry["precision"] == "area" and entry["located_by"] == "llm"


async def test_activities_at_a_stop_read_the_catalog(cis):
    pool = RecordingPool([
        ("FROM tripplanner.trip_events", [_event("add_component", OLD_B)]),
        ("tripplanner.component_id_map", [{"old_id": OLD_B, "new_id": NEW_B}]),
        ("SELECT c.component_id AS id", [_comp(NEW_B, VTE, 1, "Vientiane")]),
        ("GROUP BY c.name, c.activity", [{"component_id": NEW_A, "component_ids": [NEW_A, NEW_B],
                                          "name": "Night market", "activity": "culinary",
                                          "intensity_level": "leisurely", "duration_hint": "half_day",
                                          "text_extract": "x", "tour_count": 2}]),
    ])
    out = await activities.at_stop(pool, "trip", VTE)
    assert out["activities"][0]["component_id"] == NEW_B and out["activities"][0]["selected"]
    _cis_only(pool)


async def test_coverage_names_legs_from_catalog_v_tour_without_the_tenant(cis):
    pool = RecordingPool([
        ("FROM tripplanner.trip_events", [_event("add_component", NEW_B), _event("add_component", NEW_C)]),
        ("SELECT c.component_id AS id", [_comp(NEW_B, VTE, 1, "Vientiane"),
                                         _comp(NEW_C, LPB, 2, "Luang Prabang", 19.88, 102.13)]),
        ("SELECT DISTINCT destination_id::text", [{"destination_id": VTE, "source_tour_id": SRC,
                                                   "source_day_index": 1},
                                                  {"destination_id": LPB, "source_tour_id": SRC,
                                                   "source_day_index": 2}]),
        ("SELECT source_id AS tour_id, title AS aa_name", [{"tour_id": SRC, "aa_name": "Mekong Valley"}]),
    ])
    out = await coverage.coverage(pool, "trip")
    assert out["coverable"] and [(leg["tour_id"], leg["tour_name"]) for leg in out["legs"]] == [
        (SRC, "Mekong Valley")]
    _cis_only(pool)


async def test_suggestions_read_the_catalog_route_candidates(cis):
    pool = RecordingPool([
        ("FROM tripplanner.trip_events", [_event("add_component", NEW_B)]),
        ("SELECT c.component_id AS id", [_comp(NEW_B, VTE, 1, "Vientiane")]),
        ("SELECT c.embedding, d.country", [{"embedding": "[1,0,0]", "country": "Laos"}]),
        ("FROM catalog.v_graph_node n", [{"id": LPB, "name": "Luang Prabang", "lat": 19.88, "lng": 102.13,
                                          "country": "Laos", "component_count": 3, "best_distance": 0.1,
                                          "transfer_km": 0.0, "tour_count": 1}]),
    ])
    out = await suggestions.suggest(pool, "trip")
    assert out["mode"] == "route" and out["suggestions"][0]["id"] == LPB
    _cis_only(pool)


async def test_suggestion_fallback_is_the_cis_top_k(cis):
    pool = RecordingPool([
        ("FROM tripplanner.trip_events", [_event("add_component", NEW_B)]),
        ("SELECT c.component_id AS id", [_comp(NEW_B, VTE, 1, "Vientiane")]),
        ("SELECT c.embedding, d.country", [{"embedding": "[1,0,0]", "country": "Laos"}]),
        ("FROM catalog.v_tour_component) c", [{"id": LPB, "name": "Luang Prabang", "lat": 19.88, "lng": 102.13,
                                               "country": "Laos", "component_count": 3, "best_distance": 0.1}]),
    ])
    out = await suggestions.suggest(pool, "trip")
    assert out["mode"] == "taste" and out["suggestions"][0]["id"] == LPB
    exact = next(q for q in pool.sql if "FROM catalog.v_tour_component) c" in q)
    assert "FROM catalog.v_destination dc WHERE dc.country = ANY($3::text[])" in exact
    _cis_only(pool)


async def test_master_texts_on_cis_come_from_catalog_v_tour(cis):
    pool = RecordingPool([("FROM catalog.v_tour", [{"tour_id": SRC,
                                                    "aa_itineraries": "Day 1 — Vientiane\nMarket.\nDay 2 — LPB\nX."}])])
    texts = await master_content.load_for_tours(pool, [SRC, SRC, None])
    assert set(texts) == {(SRC, 1), (SRC, 2)}
    _cis_only(pool)


# ── hand-off day guard (TP #86, the narration rule on the legs) ──────────────────────────────────────────────────

DAY_TEXTS = {
    ("t", 4): "Day 4 — Khimsar to Jaisalmer\nDrive across the desert.",
    ("t", 5): "Day 5 — Jaisalmer\nThe golden fort.",
    ("t", 16): "Day 16 — Jaipur\nMorning at Amber Fort, then the city palace.",
}


def _entry(day, name, idx, tour="t"):
    return {"day": day, "component_id": f"c{day}", "name": name, "destination_name": name,
            "source_tour_id": tour, "source_day_index": idx}


def test_guarded_days_moves_a_stop_to_the_day_that_names_it():
    itin = [_entry(1, "Jaisalmer Fort", 5), _entry(2, "Amber Fort", 4)]
    out = master_content.guarded_days(itin, DAY_TEXTS)
    assert [e["source_day_index"] for e in out] == [5, 16]
    assert out[1]["listed_day_index"] == 4 and "listed_day_index" not in out[0]
    assert itin[1]["source_day_index"] == 4                       # the input is not changed


def test_payload_legs_use_the_guarded_days_and_days_keep_the_row():
    itin = [_entry(1, "Jaisalmer Fort", 5), _entry(2, "Amber Fort", 4)]
    p = handoff.build_payload(trip_id="trip", handoff_id=7, customer=None, itinerary=itin, custom_days=[],
                              day_texts=DAY_TEXTS)
    assert [(leg["day_from"], leg["day_to"]) for leg in p["itinerary"]["legs"]] == [(5, 5), (16, 16)]
    assert [d["source_day_index"] for d in p["itinerary"]["days"]] == [5, 4]
    plain = handoff.build_payload(trip_id="trip", handoff_id=7, customer=None, itinerary=itin, custom_days=[])
    assert [(leg["day_from"], leg["day_to"]) for leg in plain["itinerary"]["legs"]] == [(5, 5), (4, 4)]


async def test_unreadable_day_texts_never_refuse_a_hand_off(monkeypatch):
    async def boom(conn, keys):
        raise RuntimeError("no grant")
    monkeypatch.setattr(master_content, "load_for_tours", boom)
    assert await handoff.load_day_texts(RecordingPool(), ["t"]) == {}
