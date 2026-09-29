"""AA-674 — coverage check: the leg chain of real tours that covers a trip's pins."""
from __future__ import annotations

from backend.assembly import coverage as cv


def _pin(i, dest, lat, lng, tour=None, day=None):
    return {"id": i, "name": dest.title(), "destination_id": dest, "lat": lat, "lng": lng,
            "source_tour_id": tour, "source_day_index": day}


PARO, THIMPHU, PUNAKHA = (27.43, 89.41), (27.47, 89.64), (27.59, 89.87)
KATHMANDU = (27.71, 85.31)


def test_one_tour_in_order_is_one_leg():
    pins = [_pin("a", "paro", *PARO, "t1", 1), _pin("b", "thimphu", *THIMPHU, "t1", 3),
            _pin("c", "punakha", *PUNAKHA, "t1", 5)]
    out = cv.solve(pins, {})
    assert out["coverable"] and out["tours"] == 1 and out["transfers"] == []
    assert out["legs"] == [{"tour_id": "t1", "day_from": 1, "day_to": 5, "pins": ["a", "b", "c"], "days": 5}]
    assert out["total_days"] == 5


def test_prefers_staying_on_one_tour_over_a_transfer():
    # the Thimphu pin came from t2, but t1 also visits Thimphu on day 3
    pins = [_pin("a", "paro", *PARO, "t1", 1), _pin("b", "thimphu", *THIMPHU, "t2", 1)]
    out = cv.solve(pins, {"thimphu": {("t1", 3), ("t2", 1)}})
    assert [leg["tour_id"] for leg in out["legs"]] == ["t1"] and out["transfers"] == []


def test_near_hop_between_tours_is_a_valid_transfer():
    pins = [_pin("a", "paro", *PARO, "t1", 2), _pin("b", "punakha", *PUNAKHA, "t2", 4)]
    out = cv.solve(pins, {})
    assert out["coverable"] and out["tours"] == 2
    assert out["transfers"][0]["ok"] and 40 < out["transfers"][0]["km"] < 60
    assert out["total_days"] == 2


def test_far_hop_between_tours_is_a_gap_with_a_reason():
    pins = [_pin("a", "paro", *PARO, "t1", 2), _pin("b", "kathmandu", *KATHMANDU, "t2", 1)]
    out = cv.solve(pins, {})
    assert not out["coverable"]
    assert out["gaps"][0]["reason"] == "no Adventure Asia tour links these two places"
    assert out["gaps"][0]["km"] > 400


def test_going_back_in_the_same_tour_starts_a_new_leg():
    pins = [_pin("a", "thimphu", *THIMPHU, "t1", 3), _pin("b", "paro", *PARO, "t1", 1)]
    out = cv.solve(pins, {})
    assert len(out["legs"]) == 2 and out["transfers"][0]["ok"]


def test_empty_trip_is_trivially_coverable():
    assert cv.solve([], {})["coverable"] is True


def test_keeps_each_pin_on_its_own_tour_day_when_nothing_is_saved():
    # t1 also visits Thimphu on day 2, but the pinned Thimphu component is t1 day 5
    pins = [_pin("a", "paro", *PARO, "t1", 1), _pin("b", "thimphu", *THIMPHU, "t1", 5)]
    out = cv.solve(pins, {"thimphu": {("t1", 2), ("t1", 5)}})
    assert out["legs"] == [{"tour_id": "t1", "day_from": 1, "day_to": 5, "pins": ["a", "b"], "days": 5}]
