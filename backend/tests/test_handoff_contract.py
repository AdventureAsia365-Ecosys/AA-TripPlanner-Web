"""AA-805 / AA-830 / AA-986 L6 / AA-908: the inquiry.submitted payload must keep matching the ecosystem contract.

backend/tests/contracts/inquiry.submitted.v1.4.json is a vendored copy of AA-Ecosys
docs/contracts/inquiry.submitted.v1.4.json (the root repo is the source of truth; v1.1 to v1.3 are kept for
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
    (Path(__file__).parent / "contracts" / "inquiry.submitted.v1.4.json").read_text()
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


def _payload(customer=None, tour_keys=None, unavailable=None):
    return handoff.build_payload(
        trip_id="22222222-2222-2222-2222-222222222222", handoff_id=7, customer=customer,
        itinerary=_itinerary(), custom_days=[], tour_keys=tour_keys,
        submitted_at=datetime(2026, 10, 14, tzinfo=timezone.utc), unavailable=unavailable)


KEYS = {TOUR: ("cis", SOURCE), TOUR_B: ("cis", TOUR_B)}


def test_payload_version_is_1_4():
    assert handoff.SCHEMA_VERSION == 1.4
    assert _payload()["version"] == 1.4
    assert SCHEMA["properties"]["version"]["enum"] == [1.4]


def test_payload_has_every_required_top_level_field():
    assert set(SCHEMA["required"]) <= set(_payload())


@pytest.mark.parametrize("tour_keys", [KEYS, None, {TOUR: ("cis", SOURCE)}],
                         ids=["resolved", "unresolved", "partly"])
def test_payload_validates_against_the_v1_4_schema(tour_keys):
    jsonschema.validate(_payload(tour_keys=tour_keys), SCHEMA)


def test_legs_carry_the_catalog_key_and_keep_source_tour_id():
    legs = _payload(tour_keys=KEYS)["itinerary"]["legs"]
    assert legs == [
        {"position": 1, "source_tour_id": TOUR, "source_system": "cis", "source_id": SOURCE,
         "day_from": 1, "day_to": 2, "available": True, "reason": None, "reason_detail": None},
        {"position": 2, "source_tour_id": TOUR_B, "source_system": "cis", "source_id": TOUR_B,
         "day_from": 1, "day_to": 1, "available": True, "reason": None, "reason_detail": None},
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


# --- AA-986 L6: stops whose tour AA no longer serves still go, flagged ---------------------------------------------

GONE = "66666666-6666-6666-6666-666666666666"         # a retired catalog key (in the tombstone)


def _unavailable():
    """Shape of events.unavailable_stops(): two days of a retired tour, a stop with no snapshot (saved before L6)."""
    return [
        {"component_id": "g1", "day": 2, "name": "Old stop 1", "source_tour_id": GONE, "source_day_index": 3,
         "destination_id": None, "destination_name": "Hue", "lat": 16.4, "lng": 107.6, "source_system": "cis",
         "source_id": GONE, "available": False, "reason": "retired", "reason_detail": "source_deleted"},
        {"component_id": "g2", "day": 3, "name": "Old stop 2", "source_tour_id": GONE, "source_day_index": 4,
         "destination_id": None, "destination_name": "Hue", "lat": 16.4, "lng": 107.6, "source_system": "cis",
         "source_id": GONE, "available": False, "reason": "retired", "reason_detail": "source_deleted"},
        {"component_id": "g3", "day": None, "name": None, "source_tour_id": None, "source_day_index": None,
         "destination_id": None, "destination_name": None, "lat": None, "lng": None, "source_system": None,
         "source_id": None, "available": False, "reason": "not_served", "reason_detail": None},
    ]


def test_unavailable_stops_validate_and_follow_the_served_ones():
    p = _payload(tour_keys=KEYS, unavailable=_unavailable())
    jsonschema.validate(p, SCHEMA)
    legs, days = p["itinerary"]["legs"], p["itinerary"]["days"]
    assert [(leg["position"], leg["available"]) for leg in legs] == [(1, True), (2, True), (3, False)]
    gone = legs[2]
    assert gone == {"position": 3, "source_tour_id": GONE, "source_system": "cis", "source_id": GONE,
                    "day_from": 3, "day_to": 4, "available": False, "reason": "retired",
                    "reason_detail": "source_deleted"}
    assert [d["component_id"] for d in days] == ["c1", "c2", "c3", "g1", "g2", "g3"]
    assert [d["available"] for d in days] == [True, True, True, False, False, False]
    # a stop with no known day goes after the last served day; a stop with no tour key has no leg
    assert days[-1]["day"] == 4 and days[-1]["reason"] == "not_served"


def test_schema_refuses_an_unavailable_leg_without_reason_and_an_available_one_with_reason():
    p = _payload(tour_keys=KEYS, unavailable=_unavailable())
    bad = json.loads(json.dumps(p))
    bad["itinerary"]["legs"][2]["reason"] = None
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, SCHEMA)
    bad = json.loads(json.dumps(p))
    bad["itinerary"]["days"][0]["reason"] = "retired"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, SCHEMA)


# --- AA-908: v1.4 customer.identity --------------------------------------------------------------------------------

ISS = "https://cognito-idp.us-west-1.amazonaws.com/us-west-1_TESTPOOLX"
SUB = "7c1d2e3f-0000-4000-8000-000000000908"


def _principal(email="Traveller@Example.com", email_verified=True):
    from backend.shared.identity_verifier import Principal
    return Principal(iss=ISS, sub=SUB, client_id="tpclient", groups=(), email=email,
                     email_verified=email_verified, username=SUB)


def test_signed_in_send_carries_the_token_email_and_identity_and_validates():
    customer = handoff.signed_in_contact(_principal(), name=" Jo ", phone=" +84 1 ", consent=True,
                                         submitted_at=datetime(2026, 10, 25, tzinfo=timezone.utc))
    assert customer == {"customer_id": None, "name": "Jo", "phone": "+84 1", "email": "traveller@example.com",
                        "consent": True, "consent_at": "2026-10-25T00:00:00+00:00", "verified": True,
                        "identity": {"iss": ISS, "sub": SUB}}
    jsonschema.validate(_payload(customer=customer, tour_keys=KEYS), SCHEMA)


@pytest.mark.parametrize("email, flag", [("a@b.co", False), ("a@b.co", None), (None, True)],
                         ids=["unverified", "no_flag", "no_email"])
def test_signed_in_send_is_verified_only_with_a_verified_email(email, flag):
    customer = handoff.signed_in_contact(_principal(email=email, email_verified=flag), name="Jo", phone=None,
                                         consent=False)
    assert customer["verified"] is False and customer["identity"] == {"iss": ISS, "sub": SUB}
    assert customer["consent_at"] is None
    jsonschema.validate(_payload(customer=customer), SCHEMA)


def test_guest_send_is_unverified_with_a_null_identity():
    customer = handoff.submitted_contact(name="Jo", phone=None, email="Jo@X.com", consent=True)
    assert customer["verified"] is False and customer["identity"] is None
    jsonschema.validate(_payload(customer=customer), SCHEMA)


def test_schema_refuses_verified_without_identity_and_any_token_field():
    customer = handoff.signed_in_contact(_principal(), name="Jo", phone=None, consent=False)
    bad = json.loads(json.dumps(_payload(customer=customer)))
    bad["customer"]["identity"] = None
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, SCHEMA)
    bad = json.loads(json.dumps(_payload(customer=customer)))
    bad["customer"]["access_token"] = "x"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, SCHEMA)
