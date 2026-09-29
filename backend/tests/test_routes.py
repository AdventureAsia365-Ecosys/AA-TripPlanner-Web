"""AA-674 / PR-11 — whole-route proposals from real tour legs."""
from __future__ import annotations

from backend.browse import routes as rt


def test_rank_prefers_the_asked_duration_then_fewer_transfers_and_one_per_tour_set():
    singles = [{"tour_id": "t1", "day_from": 1, "day_to": 8, "days": 8, "places": 5},
               {"tour_id": "t2", "day_from": 2, "day_to": 11, "days": 10, "places": 4}]
    chains = [{"a_tour": "t3", "a_from": 1, "a_to": 5, "a_days": 5, "b_tour": "t4", "b_from": 3, "b_to": 7,
               "b_days": 5, "transfer_km": 12.34, "places": 8},
              {"a_tour": "t4", "a_from": 1, "a_to": 5, "a_days": 5, "b_tour": "t3", "b_from": 1, "b_to": 5,
               "b_days": 5, "transfer_km": 0, "places": 6}]
    out = rt.rank(singles, chains, 10)
    assert [p["transfers"] for p in out] == [0, 1, 0]            # 10-day single, then the 10-day chain, then 8 days
    assert out[0]["segments"] == [("t2", 2, 11)]
    assert out[1]["transfer_km"] == 12.3 and len(out) == 3       # t3+t4 kept once


def test_day_by_day_numbers_the_trip_and_carries_the_last_place_over_empty_days():
    stops = {"t1": [{"day_index": 1, "destination_id": "k", "name": "Kathmandu", "lat": 27.7, "lng": 85.3, "country": "Nepal"},
                    {"day_index": 3, "destination_id": "p", "name": "Pokhara", "lat": 28.2, "lng": 83.9, "country": "Nepal"}],
             "t2": [{"day_index": 5, "destination_id": "c", "name": "Chitwan", "lat": 27.5, "lng": 84.4, "country": "Nepal"}]}
    p = {"segments": [("t1", 1, 3), ("t2", 5, 6)]}
    days = rt.day_by_day(p, stops)
    assert [(d["day"], d["tour_id"], d["tour_day"], d.get("name")) for d in days] == [
        (1, "t1", 1, "Kathmandu"), (2, "t1", 2, "Kathmandu"), (3, "t1", 3, "Pokhara"),
        (4, "t2", 5, "Chitwan"), (5, "t2", 6, "Chitwan")]


async def test_routes_endpoint_validates_its_query():
    from backend.browse import handler

    class _Pool:
        pass

    out = await handler.route("GET", "/browse/routes", {"country": "Nepal", "days": "99"}, pool=_Pool())
    assert out["statusCode"] == 400
