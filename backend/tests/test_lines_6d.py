"""AA-847 slice 6d (owner decision S246, D1) — line places (rivers, roads, trails) from the CIS catalog views:
tiles select by bbox and carry no geometry, /browse/shapes serves it by id, the tour answer (catalog.tour v1.2)
carries the day stretch and the line's shape_lo, assembly components carry the stretch. Offline, no DB."""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from backend.assembly import events
from backend.browse import catalog as cat
from backend.browse import handler as h
from backend.browse import shapes as shapes_mod
from backend.browse import tiles
from backend.browse.filters import BrowseFilters
from backend.tests import catalog_fixture as fx

MEKONG = "1f2e3d4c-5b6a-4978-8695-a4b3c2d1e0f9"
HX = "2a3b4c5d-6e7f-4a8b-9c0d-1e2f3a4b5c6d"
PB = "3b4c5d6e-7f8a-4b9c-8d1e-2f3a4b5c6d7e"
LINE = {"type": "LineString", "coordinates": [[100.41, 20.27], [100.75, 20.05], [101.13, 19.89]]}
SCHEMA = json.loads((Path(__file__).parent / "contracts" / "catalog.tour.v1.2.json").read_text())


class _Pool:
    def __init__(self, rows=()):
        self.rows, self.sql = list(rows), []

    async def fetch(self, query, *args):
        self.sql.append((query, args))
        return self.rows


# ── tiles ────────────────────────────────────────────────────────────────────────────────────────────────────

def test_tile_selects_by_bbox_intersection_without_geometry():
    sql, params = tiles.build_tile_query(19, 100, BrowseFilters(), 1)
    assert "d.max_lat >= $1 AND d.min_lat < $2" in sql and "d.max_lng >= $3 AND d.min_lng < $4" in sql
    assert "shape_kind" in sql and "shape_mid" not in sql and "shape_lo" not in sql   # no geometry in tiles
    group = sql.split("GROUP BY", 1)[1]
    assert "jsonb" not in group and "segment" not in group
    assert params[:4] == [19, 20, 100, 101]


def test_geo_fields_of_a_line_and_a_point():
    line = {"located_by": "geoapify", "precision": "line", "shape_kind": "line", "shape_status": "ok",
            "length_km": 151.2, "min_lat": 19.89, "min_lng": 100.41, "max_lat": 20.27, "max_lng": 101.13}
    out = tiles.geo_fields(line)
    assert out["bbox"] == [100.41, 19.89, 101.13, 20.27] and out["shape_status"] == "ok" and out["length_km"] == 151.2
    point = {**line, "precision": "point"}
    assert tiles.geo_fields(point) == {"located_by": "geoapify", "precision": "point"}


async def test_tile_answer_carries_line_fields():
    rows = [{"id": MEKONG, "name": "Mekong River", "lat": 20.0, "lng": 100.8, "located_by": "geoapify",
             "precision": "line", "shape_kind": "line", "shape_status": "approximate", "length_km": 88.0,
             "min_lat": 19.89, "min_lng": 100.41, "max_lat": 20.27, "max_lng": 101.13, "component_count": 3}]
    out = await tiles.query_tile("19_100", BrowseFilters(), pool=_Pool(rows))
    d = out["destinations"][0]
    assert d["precision"] == "line" and d["shape_status"] == "approximate" and d["bbox"][0] == 100.41


# ── /browse/shapes ───────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("ids,err", [(None, "ids required"), ("x", "uuids"), (",".join([MEKONG] * 1), None),
                                     (",".join(f"{i:08d}-0000-4000-8000-000000000000" for i in range(51)), "50")])
def test_parse_ids(ids, err):
    if err is None:
        assert shapes_mod.parse_ids(ids) == [MEKONG]
    else:
        with pytest.raises(shapes_mod.ShapesError, match=err):
            shapes_mod.parse_ids(ids)


async def test_shapes_route_reads_the_view_and_is_cacheable():
    pool = _Pool([{"destination_id": MEKONG, "shape_status": "ok", "shape": json.dumps(LINE)}])
    resp = await h.route("GET", "/browse/shapes", {"ids": f"{MEKONG},{MEKONG.upper()}", "level": "mid"}, pool=pool)
    body = json.loads(resp["body"])
    assert resp["statusCode"] == 200 and "max-age" in resp["headers"]["cache-control"]
    assert body == {"level": "mid", "shapes": [{"id": MEKONG, "status": "ok", "geometry": LINE}]}
    sql, args = pool.sql[0]
    assert "catalog.v_destination_shape" in sql and "shape_mid" in sql and args == ([MEKONG],)


async def test_shapes_route_errors():
    pool = _Pool()
    resp = await h.route("GET", "/browse/shapes", {"ids": MEKONG}, pool=pool)
    assert json.loads(resp["body"]) == {"level": "lo", "shapes": []} and len(pool.sql) == 1   # no row: no shape
    resp = await h.route("GET", "/browse/shapes", {"ids": MEKONG, "level": "hi"}, pool=pool)
    assert resp["statusCode"] == 400 and resp["headers"]["cache-control"] == "no-store"


# ── the tour answer (catalog.tour v1.2) ──────────────────────────────────────────────────────────────────────

class _LinePool(fx.FakeCatalogPool):
    """Tour 1 (Mekong Delta) gains a line place with its day stretch and shape."""

    async def fetch(self, query, *args):
        q = fx._norm(query)
        if q == fx._norm(cat.TOUR_PLACES_SQL) and args[0] == fx.TOURS[1]["tour_id"]:
            base = await super().fetch(query, *args)
            line = {**base[0], "position": 2, "place_name": "Mekong River", "destination_id": MEKONG,
                    "destination_name": "Mekong River", "lat": 10.03, "lng": 105.78, "located_by": "geoapify",
                    "precision": "line", "segment": json.dumps(LINE), "segment_basis": "day_anchors",
                    "segment_from_destination_id": HX, "segment_to_destination_id": PB, "segment_km": 87.6}
            return [{**b, "located_by": None, "precision": "point", "segment": None, "segment_basis": None,
                     "segment_from_destination_id": None, "segment_to_destination_id": None, "segment_km": None}
                    for b in base] + [line]
        if q == fx._norm(cat.LINE_SHAPES_LO_SQL):
            return [{"destination_id": MEKONG, "shape_lo": json.dumps(LINE)}] if MEKONG in args[0] else []
        return await super().fetch(query, *args)


async def test_tour_answer_carries_the_line_stretch_and_shape():
    slug = fx.TOURS[1]["slug"]
    resp = await h.route("GET", f"/browse/catalog/tours/{slug}", {}, pool=_LinePool())
    tour = json.loads(resp["body"])
    view = {k: v for k, v in tour.items() if k in SCHEMA["properties"]}
    view["photos"] = [{k: v for k, v in p.items() if k not in ("url", "url_small")} for p in tour["photos"]]
    jsonschema.validate(view, SCHEMA)
    assert tour["version"] == 1.2
    line = next(p for p in tour["places"] if p["precision"] == "line")
    assert line["segment"] == LINE and line["segment_basis"] == "day_anchors" and line["segment_km"] == 87.6
    assert line["shape_lo"] == LINE and line["segment_from_destination_id"] == HX
    point = next(p for p in tour["places"] if p["precision"] == "point")
    assert "shape_lo" not in point and point["segment"] is None


# ── assembly ─────────────────────────────────────────────────────────────────────────────────────────────────

def test_component_segment_fields_are_json_ready():
    import uuid
    c = events._segment_fields({"id": "x", "segment": json.dumps(LINE), "segment_basis": "anchor_window",
                                "segment_from_destination_id": uuid.UUID(HX), "segment_to_destination_id": None,
                                "segment_km": 30.0})
    assert c["segment"] == LINE and c["segment_from_destination_id"] == HX and c["segment_to_destination_id"] is None
    assert "c.segment, c.segment_basis" in events.COMPONENTS_SQL
