"""Offline unit tests for Lambda A (Map/Browse). No DB, no Bedrock."""
from __future__ import annotations

import json

import pytest

from backend.browse import handler as h
from backend.browse import search as search_mod
from backend.browse.filters import BrowseFilters
from backend.browse.tiles import TileError, build_tile_query, parse_tile_id


# --- FakePool ---------------------------------------------------------------

class FakePool:
    def __init__(self, *, fetch_rows=None, fetchrow_row=None):
        self._fetch_rows = fetch_rows or []
        self._fetchrow_row = fetchrow_row
        self.calls: list[tuple[str, tuple]] = []

    async def fetch(self, query, *args):
        self.calls.append((query, args))
        return self._fetch_rows

    async def fetchrow(self, query, *args):
        self.calls.append((query, args))
        # crude: first fetchrow returns dest row, subsequent handled by fetch
        return self._fetchrow_row


# --- tile id parsing --------------------------------------------------------

def test_parse_tile_id_ok():
    assert parse_tile_id("22_103") == (22, 103)
    assert parse_tile_id("-8_115") == (-8, 115)


def test_parse_tile_id_rejects_bad():
    with pytest.raises(TileError):
        parse_tile_id("abc")
    with pytest.raises(TileError):
        parse_tile_id("22")
    with pytest.raises(TileError):
        parse_tile_id("999_0")


# --- filter SQL -------------------------------------------------------------

def test_filters_from_query_validates_enums():
    f = BrowseFilters.from_query(
        {"activity": "trekking", "intensity_level": "bogus", "season": "5"}
    )
    assert f.activity == "trekking"
    assert f.intensity_level is None  # invalid enum dropped
    assert f.season == 5


def test_filters_season_out_of_range_dropped():
    assert BrowseFilters.from_query({"season": "13"}).season is None
    assert BrowseFilters.from_query({"season": "x"}).season is None


def test_where_sql_builds_bound_params():
    f = BrowseFilters(activity="culinary", country="Vietnam", season=6)
    frag, params = f.where_sql(start_index=5)
    assert "c.activity = $5" in frag
    assert "d.country = $6" in frag
    assert "$7 = ANY(c.season_months)" in frag
    assert params == ["culinary", "Vietnam", 6]


def test_build_tile_query_bounds_and_filters():
    f = BrowseFilters(activity="trekking")
    sql, params = build_tile_query(22, 103, f)
    # bounds are params 1..4, then filter param 5
    assert params[:4] == [22, 23, 103, 104]
    assert params[4] == "trekking"
    assert "d.lat >= $1 AND d.lat < $2" in sql
    assert "c.activity = $5" in sql


# --- routing ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_route_tiles_returns_cache_headers():
    pool = FakePool(fetch_rows=[
        {"id": "d1", "name": "Sapa", "lat": 22.3, "lng": 103.8,
         "component_count": 2},
    ])
    resp = await h.route("GET", "/browse/tiles/22_103", {}, pool=pool)
    assert resp["statusCode"] == 200
    assert "max-age" in resp["headers"]["cache-control"]
    body = json.loads(resp["body"])
    assert body["destinations"][0]["name"] == "Sapa"


@pytest.mark.asyncio
async def test_route_tiles_bad_id_400():
    pool = FakePool()
    resp = await h.route("GET", "/browse/tiles/nope", {}, pool=pool)
    assert resp["statusCode"] == 400


@pytest.mark.asyncio
async def test_route_destination_404_when_missing():
    pool = FakePool(fetchrow_row=None)
    resp = await h.route("GET", "/browse/destinations/x", {}, pool=pool)
    assert resp["statusCode"] == 404


@pytest.mark.asyncio
async def test_route_search_no_store_and_uses_embedder(monkeypatch):
    pool = FakePool(fetch_rows=[
        {"id": "d1", "name": "Hoi An", "lat": 15.9, "lng": 108.3,
         "component_count": 1},
    ])
    called = {"n": 0}

    def fake_embed(text):
        called["n"] += 1
        return [0.0] * 8

    monkeypatch.setattr(search_mod.bedrock_satellite, "embed", fake_embed)
    resp = await h.route("GET", "/browse/search", {"q": "beach town"}, pool=pool)
    assert resp["statusCode"] == 200
    assert resp["headers"]["cache-control"] == "no-store"
    assert called["n"] == 1
    body = json.loads(resp["body"])
    assert body["destinations"][0]["name"] == "Hoi An"


@pytest.mark.asyncio
async def test_route_search_empty_query_returns_empty(monkeypatch):
    pool = FakePool()

    def boom(text):
        raise AssertionError("embed should not be called for empty query")

    monkeypatch.setattr(search_mod.bedrock_satellite, "embed", boom)
    resp = await h.route("GET", "/browse/search", {"q": ""}, pool=pool)
    assert json.loads(resp["body"]) == {"destinations": []}


@pytest.mark.asyncio
async def test_route_method_not_allowed():
    pool = FakePool()
    resp = await h.route("POST", "/browse/tiles/22_103", {}, pool=pool)
    assert resp["statusCode"] == 405


@pytest.mark.asyncio
async def test_route_countries_returns_list_and_cache_headers():
    pool = FakePool(fetch_rows=[
        {"country": "Laos", "component_count": 120},
        {"country": "Nepal", "component_count": 80},
    ])
    resp = await h.route("GET", "/browse/countries", {}, pool=pool)
    assert resp["statusCode"] == 200
    assert "max-age" in resp["headers"]["cache-control"]
    body = json.loads(resp["body"])
    assert body["countries"][0]["country"] == "Laos"
    assert body["countries"][0]["component_count"] == 120
