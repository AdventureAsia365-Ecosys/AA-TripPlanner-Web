"""AA-805 / AA-830: the inquiry.submitted payload must keep matching the ecosystem contract.

backend/tests/contracts/inquiry.submitted.v1.2.json is a vendored copy of AA-Ecosys
docs/contracts/inquiry.submitted.v1.2.json (the root repo is the source of truth; v1.1 is kept for
history). When the contract changes there, copy it here; this test then fails if build_payload
drifts from it.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import jsonschema
import pytest

from backend.assembly import handoff

SCHEMA = json.loads(
    (Path(__file__).parent / "contracts" / "inquiry.submitted.v1.2.json").read_text()
)

TOUR = "11111111-1111-1111-1111-111111111111"        # a version tour_id
TOUR_B = "33333333-3333-3333-3333-333333333333"      # already a catalog source_id
SOURCE = "44444444-4444-4444-4444-444444444444"      # catalog source_id of TOUR


def _itinerary():
    days = [
        {"day": d, "component_id": f"c{d}", "name": f"Day {d}", "source_tour_id": TOUR,
         "source_day_index": d, "destination_id": None, "lat": 21.0, "lng": 105.8}
        for d in (1, 2)
    ]
    days.append({"day": 3, "component_id": "c3", "name": "Day 3", "source_tour_id": TOUR_B,
                 "source_day_index": 1, "destination_id": None, "lat": 20.0, "lng": 105.0})
    return days


def _payload(customer=None, tour_keys=None):
    return handoff.build_payload(
        trip_id="22222222-2222-2222-2222-222222222222", handoff_id=7, customer=customer,
        itinerary=_itinerary(), custom_days=[], tour_keys=tour_keys,
        submitted_at=datetime(2026, 10, 14, tzinfo=timezone.utc))


KEYS = {TOUR: ("cis", SOURCE), TOUR_B: ("cis", TOUR_B)}


def test_payload_version_is_1_2():
    assert handoff.SCHEMA_VERSION == 1.2
    assert _payload()["version"] == 1.2
    assert SCHEMA["properties"]["version"]["enum"] == [1.2]


def test_payload_has_every_required_top_level_field():
    assert set(SCHEMA["required"]) <= set(_payload())


@pytest.mark.parametrize("tour_keys", [KEYS, None, {TOUR: ("cis", SOURCE)}],
                         ids=["resolved", "unresolved", "partly"])
def test_payload_validates_against_the_v1_2_schema(tour_keys):
    jsonschema.validate(_payload(tour_keys=tour_keys), SCHEMA)


def test_legs_carry_the_catalog_key_and_keep_source_tour_id():
    legs = _payload(tour_keys=KEYS)["itinerary"]["legs"]
    assert legs == [
        {"position": 1, "source_tour_id": TOUR, "source_system": "cis", "source_id": SOURCE,
         "day_from": 1, "day_to": 2},
        {"position": 2, "source_tour_id": TOUR_B, "source_system": "cis", "source_id": TOUR_B,
         "day_from": 1, "day_to": 1},
    ]


def test_unresolved_leg_has_both_key_fields_null():
    (leg, _) = _payload(tour_keys={TOUR_B: ("cis", TOUR_B)})["itinerary"]["legs"]
    assert (leg["source_system"], leg["source_id"]) == (None, None)


class _Rows:
    """Fake connection: answers TOUR_KEYS_SQL with fixed catalog rows, records the keys asked for."""

    def __init__(self, rows=None, error=None):
        self.rows, self.error, self.asked = rows or [], error, []

    async def fetch(self, query, *args):
        assert query == handoff.TOUR_KEYS_SQL
        self.asked.append(args[0])
        if self.error:
            raise self.error
        return self.rows


async def test_resolve_accepts_both_key_kinds():
    conn = _Rows([{"source_system": "cis", "source_id": SOURCE, "tour_id": TOUR},
                  {"source_system": "cis", "source_id": TOUR_B, "tour_id": "55555555-5555-5555-5555-555555555555"}])
    keys = await handoff.resolve_tour_keys(conn, [TOUR, TOUR_B, TOUR, None, "gone"])
    assert keys == {TOUR: ("cis", SOURCE), TOUR_B: ("cis", TOUR_B)}
    assert conn.asked == [sorted({TOUR, TOUR_B, "gone"})]


async def test_resolve_without_keys_reads_nothing():
    conn = _Rows()
    assert await handoff.resolve_tour_keys(conn, [None, ""]) == {}
    assert conn.asked == []


async def test_unreadable_catalog_leaves_keys_empty():
    conn = _Rows(error=RuntimeError("permission denied for view v_tour"))
    assert await handoff.resolve_tour_keys(conn, [TOUR]) == {}
