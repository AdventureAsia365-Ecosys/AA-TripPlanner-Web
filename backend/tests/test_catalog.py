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
    # the verb "may" is not May; negated clauses remove their months
    ("Rain may fall, best October to April", {10, 11, 12, 1, 2, 3, 4}),
    ("Mid-May to September", {5, 6, 7, 8, 9}),
    ("October to April, avoid June to August", {10, 11, 12, 1, 2, 3, 4}),
    ("All year except July", set(range(1, 13)) - {7}),
    ("Avoid June to August", set(range(1, 13)) - {6, 7, 8}),
    ("December to March. Not recommended in July", {12, 1, 2, 3}),
    ("Any time, but May is busiest", set(range(1, 13))),
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
    sqls = [v for k, v in vars(cat).items() if k.endswith("_SQL")] + [routes_mod.TOUR_TITLE_SQL,
                                                                       routes_mod.TOUR_ID_BY_SOURCE_SQL]
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
    extras = {"country_slug", "cover_url", "cover_source", "cover_place"}
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


async def test_tour_detail_legacy_itinerary_has_text_but_no_days():
    status, tour, _ = await _get(f"/browse/catalog/tours/{fx.TOURS[3]['slug']}")
    assert status == 200 and tour["days"] == []
    assert tour["itinerary_text"].startswith("Morning:")  # the page's fallback
    jsonschema.validate(_contract_view(tour), SCHEMA)


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


# --- cover fallback (D4/D5) -------------------------------------------------------------------

def _without_photos(i: int) -> list[dict]:
    """The fixture tours with tour i stripped of its own photos (cover NULL, photo_count 0)."""
    tours = [dict(t) for t in fx.TOURS]
    tours[i].update(cover_photo_path=None, photo_count=0)
    return tours


def test_main_destination_is_most_days_then_earliest():
    rows = [
        {"tour_id": "t", "destination_id": "a", "destination_name": "A", "day_number": 3},
        {"tour_id": "t", "destination_id": "b", "destination_name": "B", "day_number": 1},
        {"tour_id": "t", "destination_id": "b", "destination_name": "B", "day_number": 2},
        {"tour_id": "t", "destination_id": "c", "destination_name": "C", "day_number": None},
        {"tour_id": "t", "destination_id": "a", "destination_name": "A", "day_number": 4},
        {"tour_id": "t", "destination_id": "d", "destination_name": "D", "day_number": 5},
    ]
    assert [d for d, _ in cat.main_destinations(rows)["t"]] == ["b", "a", "d", "c"]


async def test_list_card_without_photo_gets_country_destination_photo():
    _, body, _ = await _get("/browse/catalog/tours", {"country": "vietnam"})
    mekong = next(t for t in body["tours"] if t["title"] == "Mekong Delta by Boat")
    # Can Tho (its only place) has no photo -> the best-covered Vietnam destination (tie -> by name).
    assert mekong["cover_source"] == "country" and mekong["cover_place"] == "Ha Long Bay"
    assert mekong["cover_url"].endswith("?size=large") and mekong["cover_url_small"].endswith("?size=small")
    assert mekong["photo_count"] == 0  # the featured sort still ranks on real photos
    hanoi = next(t for t in body["tours"] if t["title"] == "Hanoi and Ha Long Bay")
    assert hanoi["cover_source"] == "tour" and hanoi["cover_place"] is None


async def test_list_card_prefers_the_tours_main_destination():
    pool = fx.FakeCatalogPool(tours=_without_photos(0))
    _, body, _ = await _get("/browse/catalog/tours", {"country": "vietnam"}, pool=pool)
    t0 = next(t for t in body["tours"] if t["title"] == "Hanoi and Ha Long Bay")
    # Hanoi (days 1, 4) and Ha Long Bay (days 2, 3) tie on days; Hanoi comes first in the trip.
    assert t0["cover_source"] == "destination" and t0["cover_place"] == "Hanoi"
    assert "a0000000-0000-4000-8000-000000000001" in t0["cover_url"]


async def test_fallback_queries_only_for_the_page_without_covers():
    pool = fx.FakeCatalogPool()
    await _get("/browse/catalog/tours", {"country": "sri-lanka"}, pool=pool)
    assert not any(q in (cat.TOUR_DESTINATIONS_SQL, cat.COUNTRY_COVERS_SQL) for q, _ in pool.calls)
    pool = fx.FakeCatalogPool()
    await _get("/browse/catalog/tours", {"limit": "1", "offset": "2"}, pool=pool)  # Mekong only
    args = [a for q, a in pool.calls if q == cat.TOUR_DESTINATIONS_SQL]
    assert args == [([fx.TOURS[1]["tour_id"]],)]


async def test_country_fallback_ignores_case():
    # CIS joins shared.destinations to a tour's country with lower() (mig 225); so does the fallback.
    item = {"tour_id": "00000000-0000-4000-8000-0000000000ff", "country": "VIETNAM",
            "cover_url": None, "cover_url_small": None, "cover_source": None, "cover_place": None}
    pool = fx.FakeCatalogPool()
    await cat.fill_fallback_covers([item], pool=pool)
    assert item["cover_source"] == "country" and item["cover_place"] == "Ha Long Bay"
    assert [a for q, a in pool.calls if q == cat.COUNTRY_COVERS_SQL] == [(["vietnam"],)]


async def test_no_place_photo_keeps_the_placeholder():
    pool = fx.FakeCatalogPool(tours=_without_photos(2), dest_photos=[])
    _, body, _ = await _get("/browse/catalog/tours", {"country": "sri-lanka"}, pool=pool)
    card = body["tours"][0]
    assert card["cover_url"] is None and card["cover_url_small"] is None
    assert card["cover_source"] is None and card["cover_place"] is None


async def test_tour_detail_without_photos_gets_a_labelled_destination_hero(monkeypatch):
    monkeypatch.setattr(fx, "PHOTOS", [p for p in fx.PHOTOS if p["tour_id"] != fx.TOURS[0]["tour_id"]])
    pool = fx.FakeCatalogPool(tours=_without_photos(0))
    status, tour, _ = await _get(f"/browse/catalog/tours/{fx.TOURS[0]['slug']}", pool=pool)
    assert status == 200 and tour["photos"] == []
    assert tour["cover_source"] == "destination" and tour["cover_place"] == "Hanoi"
    assert tour["cover_url"].endswith("?size=large") and "cover_url_small" not in tour
    jsonschema.validate(_contract_view(tour), SCHEMA)
    # a tour with its own photos never reads the fallback
    monkeypatch.undo()
    pool = fx.FakeCatalogPool()
    _, tour, _ = await _get(f"/browse/catalog/tours/{fx.TOURS[0]['slug']}", pool=pool)
    assert tour["cover_source"] == "tour"
    assert not any(q == cat.TOUR_DESTINATIONS_SQL for q, _ in pool.calls)


async def test_destination_page_tours_and_hero_fallback():
    _, page, _ = await _get("/browse/catalog/destinations/vietnam")
    assert page["cover_url"] and "8d2e4f60" in page["cover_url"]  # the Hanoi tour's own cover
    mekong = next(t for t in page["tours"] if t["title"] == "Mekong Delta by Boat")
    assert mekong["cover_source"] == "country"
    # no tour photo in the country -> hero = the first gallery photo
    tours = [dict(t, cover_photo_path=None, photo_count=0) for t in fx.TOURS]
    _, page, _ = await _get("/browse/catalog/destinations/vietnam", pool=fx.FakeCatalogPool(tours=tours))
    assert page["cover_url"] == page["photos"][0]["url"]


async def test_country_without_tour_photos_uses_a_destination_photo():
    tours = [dict(t, cover_photo_path=None, photo_count=0) if t["country"] == "Vietnam" else t
             for t in fx.TOURS]
    _, body, _ = await _get("/browse/catalog/destinations", pool=fx.FakeCatalogPool(tours=tours))
    vn = next(c for c in body["countries"] if c["country"] == "Vietnam")
    assert "a0000000-0000-4000-8000-000000000002" in vn["cover_url"]  # Ha Long Bay (name order on a tie)
    sl = next(c for c in body["countries"] if c["country"] == "Sri Lanka")
    assert "2b3c4d5e" in sl["cover_url"]  # its own tour cover, untouched


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


async def test_tour_route_graph_match_is_labelled():
    status, body, _ = await _get("/browse/tour-route", {"tour_id": fx.TOURS[0]["tour_id"]})
    assert status == 200 and body["proposal"]["match"] == "tour_graph"


async def test_tour_route_graph_under_older_tour_id():
    # AA-812 D2: the Tour Graph was extracted under the tour's previous tour_id; the catalog (and the
    # source_id) now resolve to the re-uploaded one. The link's tour_id still finds the graph route,
    # relabelled to the current id.
    t = fx.TOURS[1]
    status, body, _ = await _get("/browse/tour-route", {"tour_id": fx.MEKONG_OLD_ID, "source_id": t["source_id"]})
    assert status == 200
    p = body["proposal"]
    assert body["tour_id"] == t["tour_id"] and p["match"] == "tour_graph"
    assert p["segments"][0]["tour_id"] == t["tour_id"] and p["segments"][0]["tour_name"] == t["title"]
    assert [d["component_id"] for d in p["days"]] == ["c1000000-0000-4000-8000-000000000001"]
    assert all(d["tour_id"] == t["tour_id"] for d in p["days"])


async def test_tour_route_from_catalog_places_when_not_in_graph(monkeypatch):
    # AA-812 D2: Sri Lanka is not in the Tour Graph at all, but its places (catalog.v_tour_place ->
    # shared.destinations) have planner experiences from another tour: the route is built from them.
    t = fx.TOURS[2]
    monkeypatch.setitem(t, "duration_days", 3)  # 2 pinned days of 3 clears the half-of-days bar
    status, body, _ = await _get("/browse/tour-route", {"tour_id": t["tour_id"], "source_id": t["source_id"]})
    assert status == 200
    p = body["proposal"]
    assert p["match"] == "places" and p["total_days"] == 2 and p["whole_tour"]
    assert p["segments"][0]["tour_name"] == "Sri Lanka Hill Country Trek"
    assert [d["name"] for d in p["days"]] == ["Kandy", "Ella"]
    # day 1 Kandy -> first Kandy experience; day 2 Ella (Nuwara Eliya has no destination row)
    assert [d["component_id"] for d in p["days"]] == ["c2000000-0000-4000-8000-000000000001",
                                                      "c2000000-0000-4000-8000-000000000003"]
    assert [d["lat"] for d in p["days"]] == [fx.DESTS[fx.KANDY][2], fx.DESTS[fx.ELLA][2]]


async def test_tour_route_places_needs_half_of_the_days(monkeypatch):
    # S236: a places-built route that pins under half of the tour's days is not offered; the
    # planner gets the 404 with the day list instead (AA-712 fills the Tour Graph later).
    t = fx.TOURS[2]
    monkeypatch.setitem(t, "duration_days", 10)  # 2 pinned days of 10
    status, body, _ = await _get("/browse/tour-route", {"tour_id": t["tour_id"]})
    assert status == 404 and body["tour"]["title"] == "Sri Lanka Hill Country Trek"
    monkeypatch.setitem(t, "duration_days", 4)  # 2 of 4 = half: offered
    status, body, _ = await _get("/browse/tour-route", {"tour_id": t["tour_id"]})
    assert status == 200 and body["proposal"]["match"] == "places"


async def test_tour_route_places_prefers_the_tours_own_components(monkeypatch):
    t = fx.TOURS[2]
    monkeypatch.setitem(t, "duration_days", 3)  # 2 pinned days of 3 clears the half-of-days bar
    own = {"id": "c3000000-0000-4000-8000-000000000001", "source_tour_id": t["tour_id"],
           "source_day_index": 1, "destination_id": fx.KANDY, "embedding": True}
    pool = fx.FakeCatalogPool(stops=[], components=fx.PLANNER_COMPONENTS + [own])
    status, body, _ = await _get("/browse/tour-route", {"tour_id": t["tour_id"]}, pool=pool)
    assert status == 200 and body["proposal"]["days"][0]["component_id"] == own["id"]


async def test_tour_route_places_spread_a_stay_over_several_experiences(monkeypatch):
    # Two days at Kandy pin two different experiences, not the same one twice.
    t = fx.TOURS[2]
    monkeypatch.setitem(t, "duration_days", 3)  # 2 pinned days of 3 clears the half-of-days bar
    extra = [{"tour_id": t["tour_id"], "day_number": 2, "position": 0, "place_name": "Kandy",
              "destination_id": fx.KANDY, "destination_name": "Kandy", "lat": fx.DESTS[fx.KANDY][2],
              "lng": fx.DESTS[fx.KANDY][3], "country": "Sri Lanka"}]
    fx.PLACES.extend(extra)
    try:
        status, body, _ = await _get("/browse/tour-route", {"tour_id": t["tour_id"]})
    finally:
        del fx.PLACES[-1]
    ids = [d["component_id"] for d in body["proposal"]["days"]]
    assert status == 200 and ids[0] != ids[1] and len(ids) == 2


async def test_tour_route_none_at_all_is_404_with_tour_days():
    # Truly nothing in the planner for this tour: 404, but with what the planner needs to help
    # (title, country, length, day titles), never a bare error.
    t = fx.TOURS[2]
    pool = fx.FakeCatalogPool(stops=[], components=[])
    status, body, headers = await _get("/browse/tour-route", {"tour_id": t["tour_id"]}, pool=pool)
    assert status == 404 and headers["cache-control"] == "no-store"
    assert body["tour"]["title"] == "Sri Lanka Hill Country Trek"
    assert body["tour"]["country"] == "Sri Lanka" and body["tour"]["duration_days"] == 7
    assert body["tour"]["days"] == [{"day": 1, "title": "Kandy"}, {"day": 2, "title": "Kandy to Ella"}]


async def test_tour_route_unknown_tour_is_404_without_tour():
    status, body, _ = await _get("/browse/tour-route", {"tour_id": "00000000-0000-4000-8000-00000000abcd"})
    assert status == 404 and body["tour"] is None


async def test_tour_route_ignores_components_without_embedding():
    t = fx.TOURS[2]
    only_unembedded = [c for c in fx.PLANNER_COMPONENTS if c["embedding"] is None]
    pool = fx.FakeCatalogPool(stops=[], components=only_unembedded)
    status, _, _ = await _get("/browse/tour-route", {"tour_id": t["tour_id"]}, pool=pool)
    assert status == 404


async def test_tour_route_resolves_current_tour_id_from_source_id():
    # A stale tour_id (the tour was re-uploaded) plus its stable source_id still finds the route.
    t = fx.TOURS[0]
    stale = "00000000-0000-4000-8000-000000000000"
    status, body, _ = await _get("/browse/tour-route", {"tour_id": stale, "source_id": t["source_id"]})
    assert status == 200 and body["tour_id"] == t["tour_id"]
    status, body, _ = await _get("/browse/tour-route", {"source_id": t["source_id"]})
    assert status == 200 and body["proposal"]["total_days"] == 4


async def test_tour_route_unknown_source_falls_back_to_tour_id():
    t = fx.TOURS[0]
    status, body, _ = await _get("/browse/tour-route", {"tour_id": t["tour_id"], "source_id": "gone"})
    assert status == 200 and body["tour_id"] == t["tour_id"]


async def test_tour_route_rejects_bad_source_id():
    status, _, _ = await _get("/browse/tour-route", {"source_id": "x'; drop"})
    assert status == 400
    status, _, _ = await _get("/browse/tour-route", {})
    assert status == 400


async def test_destination_places_query_takes_tour_ids():
    pool = fx.FakeCatalogPool()
    await h.route("GET", "/browse/catalog/destinations/vietnam", {}, pool=pool)
    args = next(a for q, a in pool.calls if q == cat.COUNTRY_PLACES_SQL)
    assert set(args[0]) == {fx.TOURS[0]["tour_id"], fx.TOURS[1]["tour_id"]}  # retired one excluded


async def test_tour_route_needs_uuid():
    status, _, _ = await _get("/browse/tour-route", {"tour_id": "x'; drop"})
    assert status == 400
