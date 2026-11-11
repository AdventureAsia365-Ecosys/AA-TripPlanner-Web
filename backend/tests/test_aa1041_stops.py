"""AA-1041 (R7 slice 1) — GET /browse/stops: the planner's stops by component id, with the place's CIS cover photo
(trip panel photos and icons, the read-only share / print view). Offline: a fake pool; the SQL itself runs on a local
Postgres in test_places_source_pg.py (test_aa1041_stops_read_over_the_views)."""
from __future__ import annotations

import json

import pytest

from backend import config
from backend.browse import handler as h
from backend.browse import stops as stops_mod

A = "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"
B = "1b2c3d4e-5f6a-4b7c-9d8e-0f1a2b3c4d5e"


class _Pool:
    def __init__(self, rows=()):
        self.rows, self.sql = list(rows), []

    async def fetch(self, query, *args):
        self.sql.append((query, args))
        return self.rows


def _row(cid, name, photo):
    return {"id": cid, "name": name, "activity": "cultural_heritage", "duration_hint": "half_day",
            "source_tour_id": "src-x", "source_day_index": 2, "destination_id": "dest-1",
            "destination_name": "Ban Imaginaire", "country": "Laos", "precision": "point", "cover_photo_path": photo}


@pytest.fixture(autouse=True)
def _cis_base(monkeypatch):
    monkeypatch.setattr(config, "CIS_PUBLIC_API_BASE", "https://cis.example/")


async def test_stops_read_the_catalog_join_and_build_photo_urls():
    pool = _Pool([_row(B, "Old town walk", "/content/photos/p1"), _row(A, "Morning market", None)])
    resp = await h.route("GET", "/browse/stops", {"ids": f"{B},{A},{A.upper()}"}, pool=pool)
    assert resp["statusCode"] == 200 and "max-age" in resp["headers"]["cache-control"]
    body = json.loads(resp["body"])
    assert [s["id"] for s in body["stops"]] == [A, B]                      # sorted, one per id
    by = {s["id"]: s for s in body["stops"]}
    assert by[B]["photo_url"] == "https://cis.example/content/photos/p1?size=large"
    assert by[B]["photo_url_small"] == "https://cis.example/content/photos/p1?size=small"
    assert by[A]["photo_url"] is None and by[A]["photo_url_small"] is None  # no CIS photo: the client shows the icon
    assert by[A]["activity"] == "cultural_heritage" and by[A]["source_day_index"] == 2
    assert not {"lat", "lng", "bbox"} & set(by[A])                          # no point: the share view draws no map
    sql, args = pool.sql[0]
    assert "catalog.v_tour_component" in sql and "catalog.v_destination" in sql
    assert args == ([A, B],)                                                # deduplicated, lower-cased, sorted


@pytest.mark.parametrize("query", [{}, {"ids": "not-a-uuid"},
                                   {"ids": ",".join(f"{i:08d}-0000-4000-8000-000000000000" for i in range(51))}])
async def test_stops_refuse_bad_ids_without_a_query(query):
    pool = _Pool()
    resp = await h.route("GET", "/browse/stops", query, pool=pool)
    assert resp["statusCode"] == 400 and resp["headers"]["cache-control"] == "no-store"
    assert pool.sql == []


async def test_stops_of_tours_no_longer_served_are_absent():
    resp = await h.route("GET", "/browse/stops", {"ids": A}, pool=_Pool())
    assert json.loads(resp["body"]) == {"stops": []}


def test_route_name_is_logged_without_ids():
    assert h.route_name("GET", "/browse/stops") == "stops"


def test_cap_matches_the_shapes_cap():
    assert stops_mod.MAX_IDS == 50
