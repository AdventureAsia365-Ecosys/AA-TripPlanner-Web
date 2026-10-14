"""AA-805 review: the inquiry.submitted payload must keep matching the ecosystem contract.

backend/tests/contracts/inquiry.submitted.v1.1.json is a vendored copy of AA-Ecosys
docs/contracts/inquiry.submitted.v1.1.json (the root repo is the source of truth). When the
contract changes there, copy it here; this test then fails if build_payload drifts from it.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import jsonschema

from backend.assembly import handoff

SCHEMA = json.loads(
    (Path(__file__).parent / "contracts" / "inquiry.submitted.v1.1.json").read_text()
)

TOUR = "11111111-1111-1111-1111-111111111111"


def _payload(customer=None):
    itinerary = [
        {"day": d, "component_id": f"c{d}", "name": f"Day {d}", "source_tour_id": TOUR,
         "source_day_index": d, "destination_id": None, "lat": 21.0, "lng": 105.8}
        for d in (1, 2)
    ]
    return handoff.build_payload(
        trip_id="22222222-2222-2222-2222-222222222222", handoff_id=7, customer=customer,
        itinerary=itinerary, custom_days=[],
        submitted_at=datetime(2026, 10, 14, tzinfo=timezone.utc))


def test_payload_version_is_1_1():
    assert handoff.SCHEMA_VERSION == 1.1
    assert _payload()["version"] == 1.1
    assert 1.1 in SCHEMA["properties"]["version"]["enum"]


def test_payload_has_every_required_top_level_field():
    assert set(SCHEMA["required"]) <= set(_payload())


def test_guest_payload_validates_against_the_v1_1_schema():
    jsonschema.validate(_payload(), SCHEMA)
