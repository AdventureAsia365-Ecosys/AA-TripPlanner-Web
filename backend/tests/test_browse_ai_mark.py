"""AA-1040 (EU AI Act Art. 50(2), root ADR 0019 d19) — the browse Lambda's catalog JSON carries the machine-readable
mark of the AI-written CIS Master text: every tour card and the tour detail have `ai`, the same object as the CIS tour
reads (generator "Adventure Asia catalog writer", generated_at = the last Master publish, else the first). Offline:
the fake catalog pool of catalog_fixture.py.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from backend.browse import catalog as cat
from backend.browse import handler as h
from backend.tests import catalog_fixture as fx

MARK_KEYS = {"ai_generated", "generator", "model", "generated_at", "human_edited"}


async def _get(path: str, query: dict | None = None):
    resp = await h.route("GET", path, query or {}, pool=fx.FakeCatalogPool())
    return resp["statusCode"], json.loads(resp["body"])


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
