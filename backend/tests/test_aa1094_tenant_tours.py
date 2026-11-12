"""AA-1094 (root ADR 0019 d10, S278 audit M20) — a tenant's own tours (backend/browse/tenant_tours.py) and the two
browse routes, offline: the demo tenant keeps the AA catalogue, an agency lists only its rows of
catalog.v_tenant_tour_v1, the view absent (before CIS 404) is an empty list / 404, a transient error is 503 no-store,
an unknown or unserved slug is 404 before the view is read. No price anywhere. Invented names only.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest

from backend.browse import catalog, tenant_site, tenant_tours
from backend.browse import handler as browse_handler

pytestmark = pytest.mark.asyncio

AGENCY = "4b1d7e57-0000-4000-8000-0000000000a1"
DEMO = "00000000-0000-0000-0000-000000000002"
WHEN = datetime(2031, 3, 4, 5, 6, tzinfo=timezone.utc)


def _site_row(**over):
    row = {"tenant_id": uuid.UUID(AGENCY), "slug": "quillmere-voyages", "name": "Quillmere Voyages", "kind": "agency",
           "in_service": True, "served": True, "tp_launcher": True, "tp_branding": False, "theme": "{}",
           "locale": "en"}
    row.update(over)
    return row


def _tour_row(**over):
    row = {"source_system": "cis", "source_id": "5f1e0c2a-7d3b-4c9e-8a61-2b4d6f8e0a13",
           "tour_id": "770a38dd-4e7b-4b55-9ded-0e9ced757301", "slug": "invented-ridge-trek-laos-5f1e0c2a",
           "title": "Quillmere's ridge walk", "subtitle": "Invented subtitle", "summary": "Invented tenant summary",
           "highlights": json.dumps(["Invented viewpoint", "Invented village"]),
           "itinerary_text": "Day 1 — Arrive\nInvented day.", "seo_title": "Invented", "seo_meta": "Invented meta",
           "country": "Laos", "duration_raw": "5 days", "duration_days": 5, "best_time_to_go": "November to March",
           "cover_photo_path": "/photos/x.jpg", "photo_count": 2, "version_id": str(uuid.uuid4()),
           "text_updated_at": WHEN, "human_edited": False, "content_hash": "abc"}
    row.update(over)
    return row


class _PgError(Exception):
    def __init__(self, sqlstate):
        super().__init__("value that must not be logged")
        self.sqlstate = sqlstate


class FakePool:
    """fetchrow = the tenant lookup; fetch = the view (by SQL) and the photo / cover reads."""

    def __init__(self, site=None, tours=(), error=None, photos=()):
        self.site, self.tours, self.error, self.photos, self.calls = site, list(tours), error, list(photos), []

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        return self.site

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        if sql in (tenant_tours.CARDS_SQL, tenant_tours.BY_SLUG_SQL):
            if self.error:
                raise self.error
            return self.tours
        if sql == catalog.TOUR_PHOTOS_SQL:
            return self.photos
        return []


def _q(**over):
    return {**catalog.parse_list_query({}), **over}


def _view_calls(pool):
    return [c for c in pool.calls if c[1] in (tenant_tours.CARDS_SQL, tenant_tours.BY_SLUG_SQL)]


async def test_an_agency_lists_its_own_tours_with_its_text_and_no_price():
    pool = FakePool(tours=[_tour_row()])
    site = tenant_site.shape(_site_row())
    out = await tenant_tours.list_tours(site, _q(), pool=pool)
    assert out["source"] == "tenant" and out["total"] == 1
    (card,) = out["tours"]
    assert card["title"] == "Quillmere's ridge walk" and card["summary"] == "Invented tenant summary"
    assert card["highlights"] == ["Invented viewpoint", "Invented village"]
    assert card["ai"] == {"ai_generated": True, "generator": tenant_tours.TENANT_AI_GENERATOR, "model": None,
                          "generated_at": WHEN.isoformat(), "human_edited": False}
    assert set(card) == set(catalog._card({**_tour_row(), "lifecycle_stage": None, "published_at": None,
                                           "updated_at": None}))
    assert "price" not in json.dumps(out) and "from_price" not in card
    ((_, sql, args),) = _view_calls(pool)
    assert args[0] == AGENCY and "WHERE t.tenant_id = $1::uuid" in sql and "SELECT *" not in sql


async def test_the_filters_and_month_apply_to_the_tenant_list():
    winter = _tour_row(best_time_to_go="November to March", title="A winter walk")
    summer = _tour_row(best_time_to_go="June to August", title="A summer walk", source_id="x2")
    pool = FakePool(tours=[winter, summer])
    out = await tenant_tours.list_tours(tenant_site.shape(_site_row()), _q(country="laos", min_days=3, month=7),
                                        pool=pool)
    assert [t["title"] for t in out["tours"]] == ["A summer walk"]
    ((_, _, args),) = _view_calls(pool)
    assert args == (AGENCY, "laos", 3, None)


async def test_a_human_edited_text_has_no_generation_time():
    pool = FakePool(tours=[_tour_row(human_edited=True)])
    (card,) = (await tenant_tours.list_tours(tenant_site.shape(_site_row()), _q(), pool=pool))["tours"]
    assert card["ai"]["human_edited"] is True and card["ai"]["generated_at"] is None


async def test_the_demo_tenant_keeps_the_aa_catalogue(monkeypatch):
    async def aa_list(q, *, pool):
        return {"filters": {}, "total": 7, "limit": 24, "offset": 0, "tours": []}
    monkeypatch.setattr(catalog, "list_tours", aa_list)
    pool = FakePool(tours=[_tour_row()])
    site = tenant_site.shape(_site_row(tenant_id=uuid.UUID(DEMO), slug="aa-tripplanner", kind="demo"))
    out = await tenant_tours.list_tours(site, _q(), pool=pool)
    assert out["source"] == "aa" and out["total"] == 7 and _view_calls(pool) == []
    assert await tenant_tours.tour_detail(site, "invented-ridge-trek-laos-5f1e0c2a", pool=pool) is None


@pytest.mark.parametrize("sqlstate", ["42P01", "42501"])
async def test_the_view_absent_lists_nothing_and_has_no_page(capsys, sqlstate):
    pool = FakePool(error=_PgError(sqlstate))
    site = tenant_site.shape(_site_row())
    out = await tenant_tours.list_tours(site, _q(), pool=pool)
    assert out["tours"] == [] and out["total"] == 0 and out["source"] == "tenant"
    assert await tenant_tours.tour_detail(site, "x-5f1e0c2a", pool=pool) is None
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines() if x.strip()]
    assert {x["evt"] for x in lines} == {"tenant_tour_view_unreadable"}
    assert "must not be logged" not in json.dumps(lines)


async def test_a_transient_error_raises_unavailable():
    with pytest.raises(tenant_tours.TenantToursUnavailable):
        await tenant_tours.list_tours(tenant_site.shape(_site_row()), _q(), pool=FakePool(error=_PgError("57P01")))


async def test_the_tour_page_has_the_tenant_text_photos_and_the_agency():
    photo = {"photo_id": "p1", "path": "/photos/a.jpg", "place_label": "Invented lake", "destination_id": None,
             "width": 800, "height": 600, "position": 1}
    pool = FakePool(tours=[_tour_row()], photos=[photo, dict(photo, photo_id="p2")])
    site = tenant_site.shape(_site_row())
    page = await tenant_tours.tour_detail(site, "Invented-Ridge-Trek-Laos-5f1e0c2a ", pool=pool)
    assert page["contract"] == "catalog.tenant_tour" and page["tenant"] == {"slug": "quillmere-voyages",
                                                                             "name": "Quillmere Voyages"}
    assert page["title"] == "Quillmere's ridge walk" and page["itinerary_text"].startswith("Day 1")
    assert [p["photo_id"] for p in page["photos"]] == ["p1"]               # the same file once
    assert "from_price" not in page and "price" not in json.dumps(page)
    ((_, _, args),) = _view_calls(pool)
    assert args == (AGENCY, "invented-ridge-trek-laos-5f1e0c2a", "5f1e0c2a")


async def test_a_tour_the_tenant_did_not_make_its_own_has_no_page():
    assert await tenant_tours.tour_detail(tenant_site.shape(_site_row()), "other-tour-0a0b0c0d",
                                          pool=FakePool(tours=[])) is None


# --- the browse routes -------------------------------------------------------------------------------------------

async def test_route_lists_a_served_tenants_tours():
    out = await browse_handler.route("GET", "/browse/tenant/quillmere-voyages/tours", {"sort": "title"},
                                     pool=FakePool(site=_site_row(), tours=[_tour_row()]))
    assert out["statusCode"] == 200 and out["headers"]["cache-control"].startswith("public")
    body = json.loads(out["body"])
    assert body["source"] == "tenant" and body["tours"][0]["title"] == "Quillmere's ridge walk"
    assert browse_handler.route_name("GET", "/browse/tenant/quillmere-voyages/tours") == "tenant_tours"
    assert browse_handler.route_name("GET", "/browse/tenant/quillmere-voyages/tours/x-1") == "tenant_tour"


@pytest.mark.parametrize("site", [None, _site_row(served=False), _site_row(in_service=False)])
async def test_route_answers_404_for_an_unknown_or_unserved_tenant_without_reading_the_view(site):
    pool = FakePool(site=site, tours=[_tour_row()])
    for path in ("/browse/tenant/quillmere-voyages/tours", "/browse/tenant/quillmere-voyages/tours/x-5f1e0c2a"):
        out = await browse_handler.route("GET", path, {}, pool=pool)
        assert out["statusCode"] == 404 and out["headers"]["cache-control"] == "no-store"
    assert _view_calls(pool) == []


async def test_route_tour_page_200_404_and_503():
    ok = await browse_handler.route("GET", "/browse/tenant/quillmere-voyages/tours/invented-ridge-trek-laos-5f1e0c2a",
                                    {}, pool=FakePool(site=_site_row(), tours=[_tour_row()]))
    assert ok["statusCode"] == 200 and json.loads(ok["body"])["source_id"] == _tour_row()["source_id"]
    gone = await browse_handler.route("GET", "/browse/tenant/quillmere-voyages/tours/other-0a0b0c0d", {},
                                      pool=FakePool(site=_site_row(), tours=[]))
    assert gone["statusCode"] == 404
    down = await browse_handler.route("GET", "/browse/tenant/quillmere-voyages/tours", {},
                                      pool=FakePool(site=_site_row(), error=ConnectionResetError("x")))
    assert down["statusCode"] == 503 and down["headers"]["cache-control"] == "no-store"


async def test_route_refuses_a_bad_list_query_and_a_bad_tour_slug():
    bad = await browse_handler.route("GET", "/browse/tenant/quillmere-voyages/tours", {"sort": "price"},
                                     pool=FakePool(site=_site_row()))
    assert bad["statusCode"] == 400
    odd = await browse_handler.route("GET", "/browse/tenant/quillmere-voyages/tours/Bad_Slug", {},
                                     pool=FakePool(site=_site_row()))
    assert odd["statusCode"] == 404
