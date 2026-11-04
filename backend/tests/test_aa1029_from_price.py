"""AA-1029 (root ADR 0005, ADR 0019 b15): the tour detail of the browse Lambda carries the public "from" price read
from AA-Booking's contract view booking.v_from_prices (price.from.v1); no row, or a view that cannot be read yet
(Booking bk-039 not applied), gives `from_price: null` and the page shows no price. Offline: the fake catalog pool.
"""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from backend.browse import catalog as cat
from backend.browse import handler as h
from backend.tests import catalog_fixture as fx


async def _tour(pool: fx.FakeCatalogPool) -> dict:
    t = fx.TOURS[0]
    resp = await h.route("GET", f"/browse/catalog/tours/{t['slug']}", {}, pool=pool)
    assert resp["statusCode"] == 200
    return json.loads(resp["body"])


async def test_tour_detail_carries_the_from_price():
    pool = fx.FakeCatalogPool()
    pool.from_prices = {fx.TOURS[0]["source_id"]: {
        "amount": Decimal("235.00"), "currency": "USD ", "basis": "per_person", "pax": 2, "season": "Low",
        "valid_to": date(2027, 3, 31)}}
    tour = await _tour(pool)
    assert tour["from_price"] == {"amount": "235.00", "currency": "USD", "basis": "per_person", "pax": 2,
                                  "season": "Low", "valid_to": "2027-03-31"}
    sql, args = next(c for c in pool.calls if c[0] == cat.FROM_PRICE_SQL)
    assert args == ("cis", fx.TOURS[0]["source_id"])


async def test_no_row_or_no_view_shows_no_price():
    assert (await _tour(fx.FakeCatalogPool()))["from_price"] is None
    pool = fx.FakeCatalogPool()
    pool.from_prices = None
    assert (await _tour(pool))["from_price"] is None


def test_the_read_is_the_contract_view_only():
    sql = " ".join(cat.FROM_PRICE_SQL.split())
    assert "FROM booking.v_from_prices" in sql
    assert "trip_plan_prices" not in sql and "net" not in sql
