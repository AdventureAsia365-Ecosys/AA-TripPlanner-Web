"""AA-812 — catalog reads for the home, tour pages and destination pages. Offline: no DB.

The fake pool (catalog_fixture.py) answers the module's exact SQL constants from rows shaped like
the CIS catalog views (migration 225). backend/tests/contracts/catalog.tour.v1.json is a vendored
copy of AA-Ecosys docs/contracts/catalog.tour.v1.json (the root repo is the source of truth).
"""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from backend import config
from backend.browse import catalog as cat
from backend.browse import handler as h
from backend.browse import routes as routes_mod
from backend.tests import catalog_fixture as fx

SCHEMA = json.loads((Path(__file__).parent / "contracts" / "catalog.tour.v1.json").read_text())


async def _get(path: str, query: dict | None = None, pool=None):
    resp = await h.route("GET", path, query or {}, pool=pool or fx.FakeCatalogPool())
    return resp["statusCode"], json.loads(resp["body"]), resp["headers"]


# --- pure helpers -----------------------------------------------------------------------------

def test_slugify_matches_catalog_slug_rule():
    # The contract's example: '<title>-<country>' -> 'hanoi-and-ha-long-vietnam-3f0c2a8e'.
    assert cat.slugify("Hanoi and Ha Long Vietnam") == "hanoi-and-ha-long-vietnam"
    assert cat.slugify("Sri Lanka") == "sri-lanka"
    assert cat.slugify("  Côte d'Ivoire ") == "c-te-d-ivoire"
    assert cat.slugify(None) == ""


@pytest.mark.parametrize("text,months", [
    ("October to April", {10, 11, 12, 1, 2, 3, 4}),
    ("Nov - Mar", {11, 12, 1, 2, 3}),
    ("Nov–Feb and Jul–Aug", {11, 12, 1, 2, 7, 8}),
    ("March, April and May", {3, 4, 5}),
    ("All year round", set(range(1, 13))),
    ("year-round", set(range(1, 13))),
    ("Best in the dry season", None),
    (None, None),
    ("", None),
])
def test_months_from_text(text, months):
    assert cat.months_from_text(text) == months


def test_photo_url_uses_cis_base(monkeypatch):
    monkeypatch.setattr(config, "CIS_PUBLIC_API_BASE", "https://cis.example/")
    assert cat.photo_url("/content/photos/x", "small") == "https://cis.example/content/photos/x?size=small"
    assert cat.photo_url(None) is None


def test_str_list_accepts_the_shapes_cis_stores():
    assert cat._str_list('["a", " b ", ""]') == ["a", "b"]
    assert cat._str_list([{"text": "x"}, {"title": "y"}, 3]) == ["x", "y"]
    assert cat._str_list("one\n- two\n\nthree") == ["one", "two", "three"]
    assert cat._str_list(None) == []


def test_new_reads_never_touch_cis_internals():
    # ADR 0001: new code reads only the catalog views.
    sqls = [v for k, v in vars(cat).items() if k.endswith("_SQL")] + [routes_mod.TOUR_TITLE_SQL]
    assert len(sqls) >= 9
    src = "\n".join(sqls)
    assert "catalog.v_" in src
    for internal in ("gold_aa_internal", "silver_aa_internal", "acp_contract", "acp_shared"):
        assert internal not in src


# --- list --------------------------------------------------------------------------------------

async def test_list_default_featured_hides_retired_and_caches():
    status, body, headers = await _get("/browse/catalog/tours")
    assert status == 200
    assert "max-age" in headers["cache-control"]
    titles = [t["title"] for t in body["tours"]]
    assert "Old Saigon Food Walk" not in titles  # retired
    assert body["total"] == 3
    # featured = tours with photos first, newest first among them
    assert titles == ["Hanoi and Ha Long Bay", "Sri Lanka Hill Country Trek", "Mekong Delta by Boat"]
    card = body["tours"][0]
    assert card["country_slug"] == "vietnam"
    assert card["cover_url"].endswith("?size=large")
    assert card["highlights"] == ["Private guide", "Hand-picked stays", "Local food"]


@pytest.mark.parametrize("country", ["Sri Lanka", "sri-lanka", "SRI LANKA"])
async def test_list_country_by_name_or_slug(country):
    _, body, _ = await _get("/browse/catalog/tours", {"country": country})
    assert [t["country"] for t in body["tours"]] == ["Sri Lanka"]
    assert body["filters"]["country"] == "sri-lanka"


async def test_list_duration_range_and_sort():
    _, body, _ = await _get("/browse/catalog/tours", {"min_days": "4", "max_days": "9", "sort": "duration_desc"})
    assert [t["duration_days"] for t in body["tours"]] == [9, 7, 4]
    _, body, _ = await _get("/browse/catalog/tours", {"sort": "duration_asc"})
    assert [t["duration_days"] for t in body["tours"]] == [4, 7, 9]


async def test_list_month_filter_keeps_unknown_and_matching():
    _, body, _ = await _get("/browse/catalog/tours", {"month": "6"})
    # June: not Oct–Apr, not Nov–Mar; "All year" matches.
    assert [t["title"] for t in body["tours"]] == ["Sri Lanka Hill Country Trek"]
    _, body, _ = await _get("/browse/catalog/tours", {"month": "1"})
    assert body["total"] == 3


async def test_list_paging():
    _, body, _ = await _get("/browse/catalog/tours", {"limit": "1", "offset": "1", "sort": "duration_asc"})
    assert body["total"] == 3 and len(body["tours"]) == 1
    assert body["tours"][0]["duration_days"] == 7


@pytest.mark.parametrize("query", [
    {"sort": "price"}, {"min_days": "x"}, {"month": "13"}, {"min_days": "9", "max_days": "3"},
    {"limit": "500"},
])
async def test_list_bad_query_is_400_no_store(query):
    status, body, headers = await _get("/browse/catalog/tours", query)
    assert status == 400 and "error" in body
    assert headers["cache-control"] == "no-store"


async def test_list_empty_catalog():
    status, body, _ = await _get("/browse/catalog/tours", pool=fx.FakeCatalogPool(tours=[]))
    assert status == 200 and body["total"] == 0 and body["tours"] == []


# --- tour detail ------------------------------------------------------------------------------

def _contract_view(tour: dict) -> dict:
    """The catalog.tour v1 part of the answer (the API adds display helpers next to it)."""
    extras = {"country_slug", "cover_url"}
    out = {k: v for k, v in tour.items() if k not in extras}
    out["photos"] = [{k: v for k, v in p.items() if k not in ("url", "url_small")} for p in tour["photos"]]
    return out


async def test_tour_detail_matches_contract():
    slug = fx.TOURS[0]["slug"]
    status, tour, headers = await _get(f"/browse/catalog/tours/{slug}")
    assert status == 200 and "max-age" in headers["cache-control"]
    jsonschema.validate(_contract_view(tour), SCHEMA)
    assert tour["slug"] == slug
    assert [d["day_number"] for d in tour["days"]] == [1, 2, 3, 4]
    assert tour["days"][1]["places"] == ["Ha Long Bay"]
    assert tour["places"][0]["lat"] == pytest.approx(21.0285)
    assert [p["position"] for p in tour["photos"]] == [1, 2, 3]
    assert tour["photos"][0]["url"].endswith("?size=large")
    assert tour["activities"] == ["walking", "boat trip"]


async def test_tour_detail_old_slug_resolves_by_source_id_suffix():
    sid8 = fx.TOURS[0]["source_id"].replace("-", "")[:8]
    status, tour, _ = await _get(f"/browse/catalog/tours/an-old-title-{sid8}")
    assert status == 200
    assert tour["slug"] == fx.TOURS[0]["slug"]  # the page redirects to this


async def test_tour_detail_retired_still_reachable():
    status, tour, _ = await _get(f"/browse/catalog/tours/{fx.TOURS[3]['slug']}")
    assert status == 200 and tour["lifecycle_stage"] == "retired"


async def test_tour_detail_unknown_is_404_no_store():
    status, _, headers = await _get("/browse/catalog/tours/no-such-tour")
    assert status == 404 and headers["cache-control"] == "no-store"


async def test_tour_detail_rejects_bad_slug_chars():
    status, _, _ = await _get("/browse/catalog/tours/Bad_Slug!")
    assert status == 404  # not matched by the route


# --- destinations -----------------------------------------------------------------------------

async def test_country_list():
    _, body, _ = await _get("/browse/catalog/destinations")
    vn = body["countries"][0]
    assert vn["country"] == "Vietnam" and vn["slug"] == "vietnam"
    assert vn["tour_count"] == 2  # the retired tour is not counted
    assert (vn["min_days"], vn["max_days"]) == (4, 9)
    assert vn["cover_url"]


async def test_destination_page():
    status, page, _ = await _get("/browse/catalog/destinations/vietnam")
    assert status == 200
    assert page["country"] == "Vietnam" and page["tour_count"] == 2
    assert (page["min_days"], page["max_days"]) == (4, 9)
    names = [p["name"] for p in page["places"]]
    assert set(names) == {"Hanoi", "Ha Long Bay", "Can Tho"}
    hanoi = next(p for p in page["places"] if p["name"] == "Hanoi")
    assert hanoi["tour_count"] == 1 and hanoi["lat"] == pytest.approx(21.0285)
    assert hanoi["cover_url"] and hanoi["activity_types"][:2] == ["food", "walking"]
    assert "boat trip" in page["top_activities"]
    assert {p["place_label"] for p in page["photos"]} == {"Old Quarter", "Karsts"}
    assert [t["title"] for t in page["tours"]][0] == "Hanoi and Ha Long Bay"


async def test_destination_place_without_geocode_is_left_out():
    _, page, _ = await _get("/browse/catalog/destinations/sri-lanka")
    assert [p["name"] for p in page["places"]] == ["Ella", "Kandy"]  # not Nuwara Eliya (no lat/lng)
    assert page["photos"] == []


async def test_destination_unknown_is_404():
    status, _, _ = await _get("/browse/catalog/destinations/atlantis")
    assert status == 404


# --- sitemap ----------------------------------------------------------------------------------

async def test_sitemap():
    _, body, _ = await _get("/browse/catalog/sitemap")
    assert len(body["tours"]) == 3
    assert {c["slug"] for c in body["countries"]} == {"vietnam", "sri-lanka"}
    assert all(t["updated_at"] for t in body["tours"])


# --- tour route (Customise this trip) ---------------------------------------------------------

async def test_tour_route_whole_tour_in_day_order():
    tid = fx.TOURS[0]["tour_id"]
    status, body, _ = await _get("/browse/tour-route", {"tour_id": tid})
    assert status == 200
    p = body["proposal"]
    assert p["whole_tour"] and p["total_days"] == 4
    assert p["segments"][0]["tour_name"] == "Hanoi and Ha Long Bay"
    assert [d["component_id"][-1] for d in p["days"]] == ["1", "2", "3", "4"]
    assert [d["name"] for d in p["days"]] == ["Hanoi", "Ha Long Bay", "Ha Long Bay", "Hanoi"]


async def test_tour_route_not_in_planner_is_404():
    status, _, _ = await _get("/browse/tour-route", {"tour_id": fx.TOURS[2]["tour_id"]})
    assert status == 404


async def test_tour_route_needs_uuid():
    status, _, _ = await _get("/browse/tour-route", {"tour_id": "x'; drop"})
    assert status == 400
