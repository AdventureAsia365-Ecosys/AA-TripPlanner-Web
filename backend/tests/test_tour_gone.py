"""AA-986 L6 (root ADR 0015 decision 5, Q5): the tour page answer for a tour AA no longer serves.

- retired (catalog.v_tour_retired state 'retired') → 410 Gone;
- paused, never served, unknown, or a tombstone that cannot be read (CIS 274 not applied) → 404, as before;
- superseded with the same source_id → the current version (200; the page then moves to its slug);
- /browse/catalog/retired lists the slug suffixes the frontend proxy answers 410 for, never one a live tour has.
"""
from __future__ import annotations

import json
import re

from backend.browse import catalog as cat
from backend.browse import handler as h
from backend.tests import catalog_fixture as fx

GONE = "abcdef12-0000-4000-8000-000000000001"
PAUSED = "bcdef123-0000-4000-8000-000000000002"


def _norm(q: str) -> str:
    return re.sub(r"\s+", " ", q).strip()


class TombstonePool(fx.FakeCatalogPool):
    def __init__(self, tombstone=None, error=None, **kw):
        super().__init__(**kw)
        self.tombstone = tombstone if tombstone is not None else {GONE: ("retired", "source_deleted"),
                                                                   PAUSED: ("paused", "master_inactive")}
        self.error = error

    async def fetch(self, query, *args):
        q = _norm(query)
        if q in (_norm(cat.TOMBSTONE_BY_SUFFIX_SQL), _norm(cat.RETIRED_KEYS_SQL)):
            if self.error:
                raise self.error
            rows = [{"source_id": k, "state": s, "reason": r} for k, (s, r) in self.tombstone.items()]
            if q == _norm(cat.RETIRED_KEYS_SQL):
                return [{"source_id": r["source_id"]} for r in rows if r["state"] == "retired"]
            return [r for r in rows if r["source_id"].replace("-", "")[:8] == args[0]]
        if q == _norm(cat.SERVED_SLUGS_SQL):
            return [{"slug": t["slug"]} for t in self.tours]
        return await super().fetch(query, *args)


async def _get(path, pool):
    resp = await h.route("GET", path, {}, pool=pool)
    return resp["statusCode"], json.loads(resp["body"]), resp["headers"]


async def test_retired_tour_is_410_gone():
    status, body, headers = await _get("/browse/catalog/tours/old-hue-trip-vietnam-abcdef12", TombstonePool())
    assert status == 410 and body == {"error": "tour_retired", "reason": "source_deleted"}
    assert headers["cache-control"] == "no-store"


async def test_paused_tour_stays_404():
    status, body, _ = await _get("/browse/catalog/tours/some-trip-bcdef123", TombstonePool())
    assert status == 404 and body == {"error": "tour not found"}


async def test_unknown_suffix_and_no_suffix_stay_404():
    assert (await _get("/browse/catalog/tours/some-trip-99999999", TombstonePool()))[0] == 404
    assert (await _get("/browse/catalog/tours/no-suffix-here", TombstonePool()))[0] == 404


async def test_unreadable_tombstone_degrades_to_404():
    pool = TombstonePool(error=RuntimeError('relation "catalog.v_tour_retired" does not exist'))
    status, _, _ = await _get("/browse/catalog/tours/old-hue-trip-vietnam-abcdef12", pool)
    assert status == 404


async def test_superseded_version_resolves_to_the_current_tour():
    """Same source_id, new title: the old slug's suffix finds the current version (the page redirects)."""
    t = fx.TOURS[0]
    sid8 = t["source_id"].replace("-", "")[:8]
    pool = TombstonePool(tombstone={t["source_id"]: ("superseded", "superseded_pending")})
    status, tour, _ = await _get(f"/browse/catalog/tours/the-title-before-the-upload-{sid8}", pool)
    assert status == 200 and tour["slug"] == t["slug"]


async def test_live_tour_wins_over_a_tombstone_row_with_the_same_suffix():
    t = fx.TOURS[0]
    pool = TombstonePool(tombstone={t["source_id"]: ("retired", "source_deleted")})
    status, tour, _ = await _get(f"/browse/catalog/tours/{t['slug']}", pool)
    assert status == 200 and tour["source_id"] == t["source_id"]


async def test_retired_list_has_retired_suffixes_only_and_never_a_live_one():
    live = fx.TOURS[1]["source_id"]
    pool = TombstonePool(tombstone={GONE: ("retired", "source_deleted"), PAUSED: ("paused", "master_inactive"),
                                    live: ("retired", "source_deleted")})
    status, body, headers = await _get("/browse/catalog/retired", pool)
    assert status == 200 and "max-age" in headers["cache-control"]
    assert body == {"suffixes": ["abcdef12"], "tombstone": "ok"}


async def test_retired_list_without_tombstone_is_empty():
    status, body, _ = await _get("/browse/catalog/retired", TombstonePool(error=RuntimeError("no view")))
    assert status == 200 and body == {"suffixes": [], "tombstone": "unavailable"}


def test_route_name_for_the_retired_list():
    assert h.route_name("GET", "/browse/catalog/retired") == "catalog_retired"
