"""AA-712 slice 6b / X4 — the browse readers over the CIS catalog views. Offline: a recording fake pool.

Every browse place / tour-graph read must come from the CIS catalog views (migration 228 and later) and never
touch a tripplanner place table (dropped by tp-018), shared.destinations, the gold tables or the AA tenant GUC
(the views are live-only and read with their owner's rights). The PLACES_SOURCE=legacy|cis switch this file was
named after is gone (AA-712 X4).
"""
from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager

import pytest

from backend.browse import handler as h
from backend.browse import routes as rt
from backend.browse import search as search_mod
from backend.browse.filters import BrowseFilters

SRC_A = "664086a9-df2d-481c-a4fa-af67f782a6a8"
SRC_B = "bd183835-73ef-494e-baf7-fb88e74b57f4"
TOUR_A = "fde65bf0-e521-4ca5-8c4a-f0e6cc28b76e"
TOUR_B = "2bc45c55-fb32-4ca1-818a-62c3353ec556"
VTE = "5c6d7e8f-9a0b-4c1d-8e2f-3a4b5c6d7e8f"
LPB = "6d7e8f9a-0b1c-4d2e-8f3a-4b5c6d7e8f9a"

# Anything a browse reader must not read.
LEGACY_OBJECTS = re.compile(r"tripplanner\.|shared\.destinations|gold_aa_internal|silver_aa_internal|app\.tenant_id")


class _Txn:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class RecordingPool:
    """Answers by the first matching (needle, rows) rule; records every statement."""

    def __init__(self, rules=()):
        self.rules = list(rules)
        self.sql: list[str] = []

    def _rows(self, query):
        self.sql.append(query)
        for needle, rows in self.rules:
            if needle in query:
                return rows
        return []

    async def fetch(self, query, *args):
        return self._rows(query)

    async def fetchrow(self, query, *args):
        rows = self._rows(query)
        return rows[0] if rows else None

    async def fetchval(self, query, *args):
        rows = self._rows(query)
        return next(iter(rows[0].values())) if rows else None

    async def execute(self, query, *args):
        self.sql.append(query)
        return "OK"

    def transaction(self):
        return _Txn()

    @asynccontextmanager
    async def acquire(self):
        yield self


def _assert_cis_only(pool: RecordingPool):
    assert pool.sql, "no statement issued"
    for q in pool.sql:
        assert not LEGACY_OBJECTS.search(q), q
    assert any("catalog.v_" in q for q in pool.sql)


# --- one test per browse reader ----------------------------------------------

DEST_ROW = {"id": VTE, "name": "Vientiane", "lat": 17.97, "lng": 102.6, "located_by": "geoapify",
            "precision": "point", "component_count": 4}


async def test_tiles_read_the_catalog_views_and_carry_located_by():
    pool = RecordingPool([("catalog.v_tour_component", [DEST_ROW])])
    resp = await h.route("GET", "/browse/tiles/17_102", {"activity": "culinary", "country": "Laos"}, pool=pool)
    assert resp["statusCode"] == 200 and "x-places-source" not in resp["headers"]
    body = json.loads(resp["body"])
    assert body["destinations"] == [DEST_ROW | {"id": VTE}]
    _assert_cis_only(pool)
    q = pool.sql[0]
    assert "c.activity = $5" in q and "d.country = $6" in q   # same filter columns on the view


async def test_countries_and_by_country():
    pool = RecordingPool([("GROUP BY d.country", [{"country": "Laos", "component_count": 120}]),
                          ("WHERE d.country = $1", [DEST_ROW])])
    resp = await h.route("GET", "/browse/countries", {}, pool=pool)
    assert json.loads(resp["body"]) == {"countries": [{"country": "Laos", "component_count": 120}]}
    resp = await h.route("GET", "/browse/by-country", {"country": "Laos"}, pool=pool)
    assert json.loads(resp["body"])["destinations"][0]["precision"] == "point"
    _assert_cis_only(pool)


async def test_destination_detail_lists_view_components():
    pool = RecordingPool([
        ("FROM catalog.v_destination d", [
            {"id": VTE, "name": "Vientiane", "country": "Laos", "cover_image_url": None, "geo_status": "ok",
             "located_by": "llm", "precision": "area"}]),
        ("FROM catalog.v_tour_component", [
            {"id": "0f1e2d3c-4b5a-5968-8776-a5b4c3d2e1f0", "name": "That Luang", "activity": "cultural_heritage",
             "intensity_level": "leisurely", "duration_hint": "half_day", "text_extract": "Golden stupa."}]),
    ])
    resp = await h.route("GET", f"/browse/destinations/{VTE}", {}, pool=pool)
    body = json.loads(resp["body"])
    assert body["precision"] == "area" and body["located_by"] == "llm"
    assert body["components"][0]["id"] == "0f1e2d3c-4b5a-5968-8776-a5b4c3d2e1f0"
    _assert_cis_only(pool)


async def test_merged_destination_redirects_to_its_keeper():
    """AA-847 (CIS 257): a merged id has no v_destination row; v_destination_redirect names the keeper → 301."""
    pool = RecordingPool([("SELECT keeper_id FROM catalog.v_destination_redirect", [{"keeper_id": LPB}])])
    resp = await h.route("GET", f"/browse/destinations/{VTE}", {}, pool=pool)
    assert resp["statusCode"] == 301 and resp["headers"]["location"] == LPB
    assert json.loads(resp["body"]) == {"redirect_to": LPB}
    assert "max-age" in resp["headers"]["cache-control"]
    _assert_cis_only(pool)


def test_destination_detail_never_reads_a_merged_row():
    """AA-997: catalog.v_destination still lists a merged row (duplicate_of set); the detail read skips it so the
    handler answers 301 to the keeper instead of 200 with the duplicate's location."""
    from backend.browse import destinations as dest_mod
    sql = " ".join(dest_mod.DEST_SQL.split())
    assert "NOT EXISTS (SELECT 1 FROM catalog.v_destination_redirect r WHERE r.destination_id = d.destination_id)" in sql
    assert "d.geo_status" in sql


async def test_suspect_destination_answers_without_any_location_field():
    """AA-997: a hidden place (geo_status 'suspect') keeps name, country and cover, never its location fields."""
    pool = RecordingPool([
        ("FROM catalog.v_destination d", [
            {"id": VTE, "name": "Vientiane", "country": "Laos", "cover_image_url": "https://img.example/v.jpg",
             "geo_status": "suspect", "located_by": "llm", "precision": "line", "shape_kind": "line",
             "shape_status": "ok", "length_km": 12.0, "min_lat": 17.9, "min_lng": 102.5, "max_lat": 18.0,
             "max_lng": 102.7}]),
    ])
    resp = await h.route("GET", f"/browse/destinations/{VTE}", {}, pool=pool)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["location_hidden"] is True
    assert body["name"] == "Vientiane" and body["cover_image_url"] == "https://img.example/v.jpg"
    for key in ("located_by", "precision", "shape_kind", "shape_status", "length_km", "bbox", "lat", "lng",
                "geo_status"):
        assert key not in body, key
    assert body["components"] == []
    _assert_cis_only(pool)


async def test_shown_destination_has_no_location_hidden_flag():
    pool = RecordingPool([
        ("FROM catalog.v_destination d", [
            {"id": VTE, "name": "Vientiane", "country": "Laos", "cover_image_url": None, "geo_status": "manual",
             "located_by": "manual", "precision": "point"}]),
    ])
    body = json.loads((await h.route("GET", f"/browse/destinations/{VTE}", {}, pool=pool))["body"])
    assert "location_hidden" not in body and body["precision"] == "point"


def test_country_covers_never_read_a_location():
    """AA-997: the country cover read keeps hidden / merged places (photo + caption only) and so must never select a
    point or a bbox."""
    from backend.browse import catalog as cat
    sql = cat.COUNTRY_COVERS_SQL.lower()
    for col in ("lat", "lng", "min_lat", "max_lat", "shape_lo", "located_by", "precision"):
        assert not re.search(rf"\b{col}\b", sql), col


async def test_unknown_destination_is_still_404():
    pool = RecordingPool([])
    resp = await h.route("GET", f"/browse/destinations/{VTE}", {}, pool=pool)
    assert resp["statusCode"] == 404
    assert any("v_destination_redirect" in q for q in pool.sql)


async def test_redirect_lookup_failure_is_still_404():
    class Broken(RecordingPool):
        async def fetchrow(self, query, *args):
            # AA-997: the detail read (DEST_SQL) also names the view; only the keeper lookup fails here
            if query.lstrip().startswith("SELECT keeper_id FROM catalog.v_destination_redirect"):
                raise RuntimeError("relation does not exist")
            return await super().fetchrow(query, *args)
    resp = await h.route("GET", f"/browse/destinations/{VTE}", {}, pool=Broken([]))
    assert resp["statusCode"] == 404


async def test_search_with_a_country_is_the_exact_aggregate_without_tenant_or_active_tour_join():
    pool = RecordingPool([("catalog.v_tour_component", [DEST_ROW | {"best_distance": 0.2}])])
    out = await search_mod.search("night market", BrowseFilters(country="Laos", season=11), pool=pool,
                                  embedder=lambda q: [0.0] * 4)
    assert [d["id"] for d in out["destinations"]] == [VTE]
    assert len(pool.sql) == 1                                     # exact first: no set_config, no hnsw settings
    q = pool.sql[0]
    assert "MIN(c.embedding <=> $1::vector)" in q and "dc.country = $2" in q and "$3 = ANY(c.season_months)" in q
    assert "FROM catalog.v_destination dc" in q and "d.destination_id AS id" in q
    _assert_cis_only(pool)


async def test_search_without_a_country_is_the_hnsw_top_k_over_the_embedding_view(monkeypatch):
    """AA-847: catalog.v_component_embedding (CIS 253, not a barrier view) serves ORDER BY <=> LIMIT k."""
    from backend.shared import vector_topk
    monkeypatch.setattr(vector_topk, "_iterative", None)
    pool = RecordingPool([("extversion", [{"extversion": "0.8.0"}]),
                          ("catalog.v_component_embedding", [DEST_ROW | {"best_distance": 0.2}])])
    out = await search_mod.search("night market", BrowseFilters(activity="culinary"), pool=pool,
                                  embedder=lambda q: [0.0] * 4, limit=1)
    assert [d["id"] for d in out["destinations"]] == [VTE] and out["destinations"][0]["precision"] == "point"
    scan = next(q for q in pool.sql if "catalog.v_component_embedding" in q)
    assert "ORDER BY c.embedding <=> $1::vector" in scan and "c.activity = $2" in scan and "LIMIT $3" in scan
    assert "JOIN catalog.v_destination od ON od.destination_id = b.destination_id" in scan
    assert any("hnsw.iterative_scan" in q for q in pool.sql)
    assert not any("app.tenant_id" in q for q in pool.sql)
    _assert_cis_only(pool)


def test_search_where_keeps_the_country_inside_the_scan():
    scan, keep, params = search_mod.search_where(BrowseFilters(activity="trekking", country="Laos"))
    assert params == ["trekking", "Laos"] and keep == ""
    assert "c.activity = $2" in scan and "ANY(ARRAY(SELECT dc.destination_id FROM catalog.v_destination dc" in scan


def _graph_rules():
    stops = [{"tour_id": SRC_A, "day_index": d, "destination_id": dest, "name": n, "lat": 0.0, "lng": 0.0,
              "country": "Laos"} for d, dest, n in ((1, VTE, "Vientiane"), (2, LPB, "Luang Prabang"),
                                                    (3, LPB, "Luang Prabang"))]
    comps = [{"tour_id": SRC_A, "day_index": d, "destination_id": dest, "component_id": f"comp-{d}"}
             for d, dest in ((1, VTE), (2, LPB), (3, LPB))]
    return [
        ("DISTINCT ON (l.source_id)", [{"tour_id": SRC_A, "day_from": 1, "day_to": 3, "days": 3, "places": 2,
                                        "whole": True}]),
        ("AS a_tour", []),
        ("FROM catalog.v_tour_stop s JOIN catalog.v_destination", stops),
        ("FROM catalog.v_tour_component", comps),
        ("SELECT source_id, tour_id::text AS tour_id, title", [{"source_id": SRC_A, "tour_id": TOUR_A,
                                                                "title": "Mekong Valley"}]),
        ("SELECT source_id FROM catalog.v_tour WHERE tour_id", [{"source_id": SRC_A}]),
        ("SELECT title FROM catalog.v_tour", [{"title": "Mekong Valley"}]),
    ]


async def test_routes_key_by_source_id_and_answer_the_current_tour_id():
    pool = RecordingPool(_graph_rules())
    resp = await h.route("GET", "/browse/routes", {"country": "Laos", "days": "3"}, pool=pool)
    body = json.loads(resp["body"])
    (p,) = body["proposals"]
    assert p["whole_tour"] and p["segments"] == [{"tour_id": TOUR_A, "source_id": SRC_A,
                                                  "tour_name": "Mekong Valley", "day_from": 1, "day_to": 3,
                                                  "days": 3}]
    assert [(d["tour_id"], d["source_id"], d["component_id"]) for d in p["days"]] == [
        (TOUR_A, SRC_A, "comp-1"), (TOUR_A, SRC_A, "comp-2"), (TOUR_A, SRC_A, "comp-3")]
    _assert_cis_only(pool)


async def test_the_candidate_reads_force_custom_plans():
    """AA-847: SET LOCAL plan_cache_mode precedes the single + chain reads (in their own transaction)."""
    pool = RecordingPool(_graph_rules())
    await h.route("GET", "/browse/routes", {"country": "Laos", "days": "3"}, pool=pool)
    assert pool.sql[0] == rt.FORCE_CUSTOM_PLAN_SQL == "SET LOCAL plan_cache_mode = force_custom_plan"
    assert pool.sql[1] == rt.SINGLE_SQL and pool.sql[2] == rt.CHAIN_SQL
    assert pool.sql.count(rt.FORCE_CUSTOM_PLAN_SQL) == 1


async def test_graph_sql_reads_views_only():
    for q in (rt.SINGLE_SQL, rt.CHAIN_SQL, rt.DAYS_SQL, rt.COMPONENTS_SQL,
              rt.PLACE_COMPONENTS_SQL, rt.TOURS_BY_SOURCE_SQL, rt.SOURCE_BY_TOUR_SQL):
        assert not LEGACY_OBJECTS.search(q) and "catalog.v_" in q


async def test_tour_route_uses_the_source_id_of_the_current_tour():
    pool = RecordingPool(_graph_rules())
    resp = await h.route("GET", "/browse/tour-route", {"tour_id": TOUR_A, "source_id": SRC_A}, pool=pool)
    body = json.loads(resp["body"])
    assert resp["statusCode"] == 200 and body["tour_id"] == TOUR_A
    assert body["proposal"]["match"] == "tour_graph"
    assert [d["component_id"] for d in body["proposal"]["days"]] == ["comp-1", "comp-2", "comp-3"]
    # same shape as /browse/routes: current tour_id + the catalog source_id
    assert [(s["tour_id"], s["source_id"]) for s in body["proposal"]["segments"]] == [(TOUR_A, SRC_A)]
    assert {(d["tour_id"], d["source_id"]) for d in body["proposal"]["days"]} == {(TOUR_A, SRC_A)}
    _assert_cis_only(pool)


async def test_tour_route_without_a_catalog_source_answers_404():
    pool = RecordingPool()   # v_tour knows neither the tour nor the source
    resp = await h.route("GET", "/browse/tour-route", {"tour_id": TOUR_B}, pool=pool)
    assert resp["statusCode"] == 404
    _assert_cis_only(pool)
