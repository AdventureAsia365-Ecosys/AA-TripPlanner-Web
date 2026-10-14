"""AA-805 — shape guard for the labelled 30-query search set used in live checks. Offline."""
from __future__ import annotations

import json
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "search_labelled_30.json"

# Planner countries on Dev (/browse/countries, 14/10/2026).
PLANNER_COUNTRIES = {
    "Bhutan", "China", "India", "Japan", "Laos", "Mongolia", "Nepal", "South Korea",
    "Sri Lanka", "Taiwan", "Thailand",
}


def _queries() -> list[dict]:
    return json.loads(FIXTURE.read_text())["queries"]


def test_thirty_unique_queries():
    qs = _queries()
    assert len(qs) == 30
    assert sorted(q["id"] for q in qs) == list(range(1, 31))
    assert len({q["query"].strip().lower() for q in qs}) == 30


def test_labels_are_valid():
    for q in _queries():
        assert q["kind"] in {"place", "activity", "theme"}, q
        assert set(q["expected_countries"]) <= PLANNER_COUNTRIES, q
        assert 0 <= q["min_top5_in_expected"] <= 5, q
        if q["kind"] != "theme":
            assert q["expected_countries"] and q["min_top5_in_expected"] >= 1, q


def test_covers_every_large_planner_country():
    covered = {c for q in _queries() for c in q["expected_countries"]}
    assert PLANNER_COUNTRIES - {"Taiwan"} <= covered  # Taiwan has 19 components only
