"""AA-1040 (EU AI Act Art. 50(2), root ADR 0019 d19) — the browse Lambda's catalog JSON carries the machine-readable
mark of the AI-written CIS Master text: every tour card and the tour detail have `ai`, the same object as the CIS tour
reads (generator "Adventure Asia catalog writer", generated_at = the last Master publish of the text, else the
first). The text time is catalog.v_tour.text_updated_at (CIS 359), which a photo change does not move; before 359 the
reads select NULL for it and the mark falls back to updated_at. Offline: the fake catalog pool of catalog_fixture.py.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from backend.browse import catalog as cat
from backend.browse import handler as h
from backend.tests import catalog_fixture as fx

MARK_KEYS = {"ai_generated", "generator", "model", "generated_at", "human_edited"}
TEXT = datetime(2026, 10, 2, 3, 0, tzinfo=timezone.utc)
PHOTO = datetime(2026, 10, 20, 3, 0, tzinfo=timezone.utc)   # a photo update after the last text publish


@pytest.fixture(autouse=True)
def _fresh_probe():
    cat.reset_text_updated_at_probe()
    yield
    cat.reset_text_updated_at_probe()


async def _get(path: str, query: dict | None = None, pool: fx.FakeCatalogPool | None = None):
    resp = await h.route("GET", path, query or {}, pool=pool or fx.FakeCatalogPool())
    return resp["statusCode"], json.loads(resp["body"])


def _photo_moved_pool(applied: bool) -> fx.FakeCatalogPool:
    tours = [dict(t) for t in fx.TOURS]
    tours[0].update(updated_at=PHOTO, text_updated_at=TEXT)
    pool = fx.FakeCatalogPool(tours=tours)
    pool.has_text_updated_at = applied
    return pool


def test_mark_shape_and_fallback():
    last = datetime(2026, 10, 2, 3, 0, tzinfo=timezone.utc)
    first = datetime(2026, 9, 1, 3, 0, tzinfo=timezone.utc)
    assert cat.catalog_ai_mark(last, first) == {
        "ai_generated": True, "generator": "Adventure Asia catalog writer", "model": None,
        "generated_at": last.isoformat(), "human_edited": None,
    }
    assert cat.catalog_ai_mark(None, first)["generated_at"] == first.isoformat()
    assert cat.catalog_ai_mark(None, None)["generated_at"] is None
    assert cat.catalog_ai_mark(None, None)["ai_generated"] is True


async def test_tour_detail_carries_the_mark():
    t = fx.TOURS[0]
    status, tour = await _get(f"/browse/catalog/tours/{t['slug']}")
    assert status == 200
    assert set(tour["ai"]) == MARK_KEYS
    assert tour["ai"]["ai_generated"] is True
    assert tour["ai"]["generator"] == cat.CATALOG_AI_GENERATOR
    assert tour["ai"]["generated_at"] == tour["updated_at"]


async def test_every_listed_card_carries_the_mark():
    status, body = await _get("/browse/catalog/tours")
    assert status == 200 and body["tours"]
    for card in body["tours"]:
        assert set(card["ai"]) == MARK_KEYS and card["ai"]["ai_generated"] is True
        assert card["ai"]["generated_at"] == (card["updated_at"] or card["published_at"])


async def test_a_photo_change_does_not_move_generated_at():
    t = fx.TOURS[0]
    _, tour = await _get(f"/browse/catalog/tours/{t['slug']}", pool=_photo_moved_pool(True))
    assert tour["updated_at"] == PHOTO.isoformat()
    assert tour["ai"]["generated_at"] == TEXT.isoformat()
    _, body = await _get("/browse/catalog/tours", pool=_photo_moved_pool(True))
    card = next(c for c in body["tours"] if c["source_id"] == t["source_id"])
    assert card["ai"]["generated_at"] == TEXT.isoformat()


async def test_before_359_the_mark_falls_back_to_updated_at_and_reprobes():
    t = fx.TOURS[0]
    pool = _photo_moved_pool(False)
    _, tour = await _get(f"/browse/catalog/tours/{t['slug']}", pool=pool)
    assert tour["ai"]["generated_at"] == PHOTO.isoformat()         # the column is absent: an upper bound
    assert any(q == cat.TOUR_BY_SLUG_SQL_PRE_359 for q, _ in pool.calls)
    pool.has_text_updated_at = True                                 # 359 applied while the Lambda is warm
    _, tour = await _get(f"/browse/catalog/tours/{t['slug']}", pool=pool)
    assert tour["ai"]["generated_at"] == TEXT.isoformat()


async def test_a_true_probe_is_kept():
    pool = _photo_moved_pool(True)
    await _get("/browse/catalog/tours", pool=pool)
    await _get("/browse/catalog/tours", pool=pool)
    assert sum(1 for q, _ in pool.calls if q == cat.TEXT_UPDATED_AT_SQL) == 1
    assert any(q == cat.TOUR_CARDS_SQL for q, _ in pool.calls)


def test_the_pre_359_reads_select_null_for_the_column():
    assert "t.text_updated_at" in cat.TOUR_CARDS_SQL and "t.text_updated_at" in cat.TOUR_BY_SLUG_SQL
    for sql in (cat.TOUR_CARDS_SQL_PRE_359, cat.TOUR_BY_SLUG_SQL_PRE_359):
        assert "NULL::timestamptz AS text_updated_at" in sql and "t.text_updated_at" not in sql
