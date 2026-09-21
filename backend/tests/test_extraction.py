"""Offline unit tests for the extraction pipeline.

No DB, no Mapbox, no Bedrock — the LLM categorize step is stubbed and the
transformation logic is exercised directly with small fixtures.
"""
from __future__ import annotations

import json

import pytest

from backend.extraction import prompts, run
from backend.extraction.country_normalize import normalize_country
from backend.extraction.verify import verify_activity
from backend.extraction.run import AtomGroup


# --- verify.py --------------------------------------------------------------

def test_verify_activity_matches_synonym():
    assert verify_activity("trekking", "A guided hike up the ridge.")
    assert verify_activity("culinary", "Join a street food tasting tour.")
    assert verify_activity("water_activities", "Sunset river cruise on the bay.")


def test_verify_activity_rejects_ungrounded():
    assert not verify_activity("trekking", "Relax at the beachfront spa.")
    assert not verify_activity("wildlife_nature", "A cooking class downtown.")


def test_verify_activity_word_boundary():
    # "walk" should match as a word, but a made-up activity should not.
    assert verify_activity("trekking", "We walk along the coastal trail.")
    assert not verify_activity("unknown_activity", "anything at all")


# --- country_normalize.py ---------------------------------------------------

def test_country_normalize_known_and_passthrough():
    assert normalize_country("SRI-LANDKA") == "Sri Lanka"
    assert normalize_country("OKINAWA") == "Japan"
    assert normalize_country("Vietnam") == "Vietnam"
    assert normalize_country("  OKINAWA  ") == "Japan"


# --- prompts.parse_components_json ------------------------------------------

def test_parse_components_plain_json():
    raw = json.dumps({"components": [{"name": "Sapa"}]})
    assert prompts.parse_components_json(raw) == [{"name": "Sapa"}]


def test_parse_components_code_fenced():
    raw = "```json\n" + json.dumps({"components": [{"name": "Hoi An"}]}) + "\n```"
    assert prompts.parse_components_json(raw) == [{"name": "Hoi An"}]


# --- group_atoms ------------------------------------------------------------

def test_group_atoms_groups_by_tour_and_day():
    rows = [
        {"tour_id": "T1", "itinerary_day": 1, "text": "Arrive Hanoi",
         "primary_destination": "Hanoi"},
        {"tour_id": "T1", "itinerary_day": 1, "text": "Old Quarter walk",
         "primary_destination": "Hanoi"},
        {"tour_id": "T1", "itinerary_day": 2, "text": "Halong cruise",
         "primary_destination": "Hanoi"},
    ]
    groups = run.group_atoms(rows)
    by_day = {(g.tour_id, g.itinerary_day): g for g in groups}
    assert set(by_day) == {("T1", 1), ("T1", 2)}
    assert len(by_day[("T1", 1)].texts) == 2
    assert by_day[("T1", 1)].primary_destination == "Hanoi"


# --- validate_and_ground ----------------------------------------------------

def _valid_component_dict(activity="trekking"):
    return {
        "name": "Sapa",
        "activity": activity,
        "intensity_level": "active",
        "duration_hint": "multi_night",
        "season_months": [3, 4, 5],
        "text_extract": "Trek through terraced rice fields around Sapa.",
    }


def test_validate_and_ground_accepts_grounded():
    group = AtomGroup("T1", 3, ["Trek through the hills to Sapa village."], "Sapa")
    raw = json.dumps({"components": [_valid_component_dict()]})
    accepted = run.validate_and_ground(raw, group)
    assert len(accepted) == 1
    assert accepted[0].name == "Sapa"


def test_validate_and_ground_drops_ungrounded_activity():
    # activity says trekking but the source text has no trekking signal.
    group = AtomGroup("T1", 3, ["Relax at a beach resort."], "Beach")
    raw = json.dumps({"components": [_valid_component_dict()]})
    accepted = run.validate_and_ground(raw, group)
    assert accepted == []


def test_validate_and_ground_drops_invalid_enum():
    group = AtomGroup("T1", 3, ["A guided hike."], "Hills")
    bad = _valid_component_dict(activity="skydiving")  # not in taxonomy
    raw = json.dumps({"components": [bad]})
    accepted = run.validate_and_ground(raw, group)
    assert accepted == []


# --- categorize_group retry-once semantics ----------------------------------

@pytest.mark.asyncio
async def test_categorize_group_retries_once_then_succeeds():
    group = AtomGroup("T1", 3, ["A guided hike to Sapa."], "Sapa")
    calls = {"n": 0}

    async def flaky(_group):
        calls["n"] += 1
        if calls["n"] == 1:
            return "not json at all"          # first attempt fails to parse
        return json.dumps({"components": [_valid_component_dict()]})

    accepted = await run.categorize_group(group, flaky)
    assert calls["n"] == 2
    assert len(accepted) == 1


@pytest.mark.asyncio
async def test_categorize_group_discards_after_second_failure():
    group = AtomGroup("T1", 3, ["Relax at the spa."], "Spa")
    calls = {"n": 0}

    async def always_ungrounded(_group):
        calls["n"] += 1
        # valid JSON + schema, but activity 'trekking' is not grounded in
        # a spa-only text, so it never passes verify.
        return json.dumps({"components": [_valid_component_dict()]})

    accepted = await run.categorize_group(group, always_ungrounded)
    assert calls["n"] == 2      # tried twice
    assert accepted == []       # discarded, not persisted
