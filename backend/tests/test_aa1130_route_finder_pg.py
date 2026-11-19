"""AA-1130 (S283 audit T1) integration — the route finder's countries and scoped routes on a real Postgres.

Runs only with ``pytest --pg-dsn postgresql://postgres@127.0.0.1:<port>/postgres`` against a throwaway LOCAL
Postgres. Reuses the catalog-view stand-in and its invented tours of test_places_source_pg.py (fixtures ``pg_dsn``
and ``pool``: two Laos tours, src-a and src-b, and one Vietnam tour, src-c).
"""
from __future__ import annotations

import pytest

pytest.importorskip("asyncpg")

from backend.browse import routes as routes_mod  # noqa: E402
from backend.tests.test_places_source_pg import _get, pg_dsn, pool  # noqa: E402,F401 (fixtures)


async def test_countries_count_only_live_tours_and_keep_a_country_without_one(pool):
    """AA-1130: a retired tour is no tour of its country; a country with experiences but no live tour stays (tour_count
    0) for the map filter, and the route finder leaves it out."""
    await pool.execute("UPDATE catalog.v_tour SET lifecycle_stage = 'retired' WHERE source_id = 'src-c'")
    _, body = await _get(pool, "/browse/countries")
    assert [(c["country"], c["tour_count"]) for c in body["countries"]] == [("Laos", 2), ("Vietnam", 0)]


async def test_scoped_routes_use_only_the_agency_tours_on_a_real_planner_read(pool):
    """AA-1130: the scoped single and chain reads run on Postgres with the agency's source ids as $4: only src-b is
    sold, so no proposal has src-a (not even as one leg of a chain), and every segment carries the agency's title."""
    scope = {"src-b": {"title": "Invented agency title", "country": "Laos", "duration_days": 3}}
    async with pool.acquire() as conn:
        for days in (2, 3, 4, 6):
            body = await routes_mod.proposals(conn, "Laos", days, scope=scope)
            for p in body["proposals"]:
                assert {s["source_id"] for s in p["segments"]} == {"src-b"}, (days, p)
                assert {s["tour_name"] for s in p["segments"]} == {"Invented agency title"}
        body = await routes_mod.proposals(conn, "Laos", 3, scope=scope)
        (whole,) = [p for p in body["proposals"] if p["whole_tour"]]
        assert whole["tour_days"] == 3 and whole["total_days"] == 3
        unscoped = await routes_mod.proposals(conn, "Laos", 6)
    assert any(s["source_id"] == "src-a" for p in unscoped["proposals"] for s in p["segments"])
