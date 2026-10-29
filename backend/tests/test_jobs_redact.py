"""AA-861 — the outbox redaction rules, offline: the allowlist rebuild (jobs/redact.py), the payload / mode / override
rules of tp_retention_outbox_redact (jobs/outbox_redact.py), the runner's lease and the jobs Lambda's attention rule.

Fixtures are synthetic: example.test addresses, a 555 phone number, no real traveller text.
"""
from __future__ import annotations

import copy
from datetime import date, datetime, timezone

import pytest

from backend.assembly import handoff
from backend.jobs import handler, outbox_redact as kind, redact, runner
from backend.jobs.runner import PermanentError

TRIP = "11111111-2222-4333-8444-555555555555"


def _payload(**customer) -> dict:
    """A v1.4 payload as handoff.build_payload writes it, with a signed-in customer."""
    cust = {"customer_id": None, "name": "Test Traveller", "phone": "+1 555 0100", "email": "traveller@example.test",
            "consent": True, "consent_at": "2026-10-01T00:00:00+00:00", "verified": True,
            "identity": {"iss": "https://issuer.example.test", "sub": "sub-test-1"}}
    cust.update(customer)
    itinerary = [{"day": 1, "component_id": "c-1", "name": "Old Quarter", "source_tour_id": "t-1",
                  "source_day_index": 1, "destination_id": "d-1", "lat": 21.03, "lng": 105.85}]
    custom = [{"day_id": "x-1", "kind": "free", "after_component_id": "c-1", "note": "Call me after 6 pm"}]
    return handoff.build_payload(trip_id=TRIP, handoff_id="h-1", customer=cust, itinerary=itinerary,
                                 custom_days=custom, submitted_at=datetime(2026, 10, 1, tzinfo=timezone.utc))


# ── redact_payload ────────────────────────────────────────────────────────────


def test_redact_nulls_contact_identity_and_notes_and_keeps_the_rest():
    src = _payload()
    before = copy.deepcopy(src)
    out, paths = redact.redact_payload(src)
    assert src == before  # input untouched
    assert out["customer"] == {"customer_id": None, "consent": True, "consent_at": "2026-10-01T00:00:00+00:00",
                               "verified": True, "name": None, "email": None, "phone": None, "identity": None}
    assert out["itinerary"]["custom_days"] == [{"day_id": "x-1", "kind": "free", "after_component_id": "c-1",
                                                "note": None}]
    for key in redact.ENVELOPE_KEYS:
        assert out[key] == src[key]
    assert out["itinerary"]["days"] == src["itinerary"]["days"]
    assert out["itinerary"]["legs"] == src["itinerary"]["legs"]
    assert sorted(paths) == ["customer.email", "customer.identity", "customer.name", "customer.phone",
                             "itinerary.custom_days[0].note"]


def test_redact_is_idempotent():
    once, _ = redact.redact_payload(_payload())
    twice, paths = redact.redact_payload(once)
    assert twice == once and paths == []


def test_every_key_build_payload_writes_is_known_to_the_allowlist():
    """A new contract field must be classified (kept or nulled) here, or a redaction silently drops it."""
    src = _payload()
    _, paths = redact.redact_payload(src)
    assert not any(p.endswith("*") for p in paths), paths


def test_unknown_keys_are_dropped_and_reported_without_their_names():
    src = _payload()
    src["extra_contact"] = "someone@example.test"
    src["customer"]["whatsapp"] = "+1 555 0101"
    src["itinerary"]["days"][0]["guest_note"] = "free text"
    src["itinerary"]["notes"] = "free text"
    out, paths = redact.redact_payload(src)
    assert "extra_contact" not in out and "whatsapp" not in out["customer"]
    assert "guest_note" not in out["itinerary"]["days"][0] and "notes" not in out["itinerary"]
    assert {"*", "customer.*", "itinerary.days[0].*", "itinerary.*"} <= set(paths)
    assert not any("whatsapp" in p or "extra_contact" in p or "guest_note" in p for p in paths)


@pytest.mark.parametrize("payload, expected", [
    (None, (None, [])),
    ("a string", (None, ["$"])),
    ({"customer": "Test Traveller"}, ({"customer": None}, ["customer"])),
    ({"itinerary": ["x"]}, ({"itinerary": None}, ["itinerary"])),
    ({"version": {"nested": 1}}, ({"version": None}, ["version"])),
    ({"itinerary": {"days": [1, None]}}, ({"itinerary": {"days": [None, None]}}, ["itinerary.days[0]"])),
])
def test_malformed_shapes_become_null(payload, expected):
    assert redact.redact_payload(payload) == expected


def test_guest_payload_keeps_customer_id_and_consent():
    out, _ = redact.redact_payload(_payload(customer_id="cust-1", identity=None, verified=False))
    assert out["customer"]["customer_id"] == "cust-1" and out["customer"]["verified"] is False


# ── payload validation, override gate, mode ────────────────────────────────────

GATE_OPEN = {"ENVIRONMENT": "dev", kind.OVERRIDE_FLAG: "true"}


@pytest.mark.parametrize("payload, message", [
    ("x", "must be an object"),
    ({"email": "a@example.test"}, "unknown payload keys"),
    ({"trigger": "cron"}, "trigger must be one of"),
    ({"dry_run": "no"}, "dry_run must be true or false"),
    ({"max_rows": "10"}, "max_rows must be an integer"),
    ({"max_rows": True}, "max_rows must be an integer"),
    ({"older_than_days": 3}, "only accepted with trigger"),
])
def test_validate_payload_refuses_bad_shapes_without_echoing_values(payload, message):
    with pytest.raises(PermanentError, match=message) as e:
        kind.validate_payload(payload)
    assert "a@example.test" not in str(e.value)


def test_override_dry_run_needs_no_gate_but_a_positive_age():
    assert kind.override_days({"trigger": "manual_override", "older_than_days": 1}, {}) == 1
    with pytest.raises(kind.OverrideRefused):
        kind.override_days({"trigger": "manual_override", "older_than_days": 0}, {})
    with pytest.raises(kind.OverrideRefused):
        kind.override_days({"trigger": "manual_override"}, {})
    assert kind.override_days({"trigger": "manual"}, {}) is None


@pytest.mark.parametrize("environ", [
    {},
    {"ENVIRONMENT": "dev"},
    {"ENVIRONMENT": "prod", kind.OVERRIDE_FLAG: "true"},
    {"ENVIRONMENT": "", kind.OVERRIDE_FLAG: "true"},
    {"ENVIRONMENT": "dev", kind.OVERRIDE_FLAG: "yes"},
])
def test_live_override_gate_fails_closed(environ):
    payload = {"trigger": "manual_override", "older_than_days": 10, "dry_run": False, "max_rows": 5}
    with pytest.raises(kind.OverrideRefused):
        kind.override_days(payload, environ)


@pytest.mark.parametrize("payload", [
    {"trigger": "manual_override", "older_than_days": 3, "dry_run": False, "max_rows": 5},
    {"trigger": "manual_override", "older_than_days": 10, "dry_run": False},
    {"trigger": "manual_override", "older_than_days": 10, "dry_run": False, "max_rows": 1001},
    {"trigger": "manual_override", "older_than_days": 10, "dry_run": False, "max_rows": 0},
])
def test_live_override_refuses_young_cutoffs_and_bad_max_rows(payload):
    with pytest.raises(kind.OverrideRefused):
        kind.override_days(payload, GATE_OPEN)


def test_live_override_with_the_gate_open():
    payload = {"trigger": "manual_override", "older_than_days": 10, "dry_run": False, "max_rows": 5}
    assert kind.override_days(payload, GATE_OPEN) == 10
    assert kind.decide_mode(payload, 99, None) == ("live", 5,
                                                   "manual override live run (explicit age), approved max_rows")


def test_cutoff_is_utc_midnight():
    assert kind.cutoff_for(date(2026, 10, 29), 90) == datetime(2026, 7, 31, tzinfo=timezone.utc)


LIVE_PREV = {"windows": {"payload_pii": 90}, "affected_total": 600}


@pytest.mark.parametrize("payload, candidates, prev, mode, limit", [
    ({}, 10, None, "dry_run", 0),
    ({"dry_run": True}, 10, LIVE_PREV, "dry_run", 0),
    ({"dry_run": False, "max_rows": 7}, 10, None, "live", 7),
    ({"dry_run": False, "max_rows": 10**9}, 10, None, "live", kind.RUN_MAX_ROWS),
    ({"trigger": "schedule"}, 10, None, "dry_run", 0),
    ({"trigger": "schedule"}, 10, {"windows": {"payload_pii": 30}, "affected_total": 5}, "dry_run", 0),
    ({"trigger": "schedule"}, 1200, LIVE_PREV, "live", 1200),
    ({"trigger": "schedule"}, 1201, LIVE_PREV, "held", 0),
    ({"trigger": "schedule"}, 900, {"windows": {"payload_pii": 90}, "affected_total": 1}, "live", 900),
    ({"trigger": "manual_override", "older_than_days": 2}, 10, None, "dry_run", 0),
])
def test_decide_mode(payload, candidates, prev, mode, limit):
    got_mode, got_limit, _reason = kind.decide_mode(payload, candidates, prev)
    assert (got_mode, got_limit) == (mode, limit)


def test_live_manual_run_needs_max_rows():
    with pytest.raises(PermanentError, match="needs max_rows"):
        kind.decide_mode({"dry_run": False}, 10, None)


# ── runner lease, Lambda attention rule ───────────────────────────────────────


@pytest.mark.parametrize("left, lease", [(0, 60), (30, 90), (300, 360), (5000, 900)])
def test_lease_covers_the_invocation_plus_a_margin(left, lease):
    assert runner.lease_seconds(left) == lease


def test_attention_names_failed_lost_and_held_jobs_only():
    jobs = [{"job_id": "a", "status": "succeeded", "result": {"mode": "dry_run"}},
            {"job_id": "b", "status": "succeeded", "result": {"mode": "held"}},
            {"job_id": "c", "status": "failed"},
            {"job_id": "d", "status": "lost"},
            {"job_id": "e", "status": "queued"}]
    assert handler.attention(jobs) == ["b", "c", "d"]


def test_registered_kinds_carry_the_tripplanner_prefix_and_daily_runs_never_retry():
    for name in handler.HANDLERS:
        assert name.startswith("tp_")
    assert handler.DAILY[kind.KIND][1] == 1
    assert handler.DAILY[kind.KIND][2](date(2026, 10, 29)) == "tp_retention_outbox_redact:schedule:2026-10-29"
