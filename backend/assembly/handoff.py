"""AA-676 — the inquiry.submitted hand-off to AA-Booking (transactional outbox, ADR 0001 decision 6).

send-to-advisor calls write_inquiry() inside its own transaction, right after the `sent` event,
so the outbox row and the hand-off commit or roll back together. AA-Booking reads
tripplanner.v_inquiry_outbox and keeps its own inbox keyed by idempotency_key; it never writes here.

Contract: AA-Ecosys docs/contracts/inquiry.submitted.v1.1.json (payload version 1.1, AA-805). Narration is not part of v1: it is
not persisted server-side (the frontend asks for it on demand), and a client-sent copy would be
untrusted text.

Identity (S235 security fix): no customer field is verified — TripPlanner has no OTP; AA-Booking will
own identity and verification. Every customer object therefore carries `verified: false`. When the
submitted phone/email matched someone else's stored customer, the session is not linked to it
(registration.py) and the payload carries the submitted contact with `customer_id: null` — never the
stored identity of another person. Contract delta (verified, nullable customer_id) goes into v1.1.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional, Protocol

from backend.assembly.registration import normalise_contact

EVENT = "inquiry.submitted"
# Payload `version` (major.minor). AA-Ecosys docs/contracts/inquiry.submitted.v1.1.json declares it
# as the NUMBER 1.1 (enum [1, 1.1]); v1.1 is additive over v1 (customer.verified, nullable
# customer_id), which this module already sends (S235). AA-805.
SCHEMA_VERSION = 1.1
# tripplanner.outbox.schema_version is an INT column: it carries the major only.
SCHEMA_MAJOR = 1


class _Conn(Protocol):
    async def execute(self, query: str, *args: Any) -> Any: ...
    async def fetchrow(self, query: str, *args: Any) -> Any: ...


def idempotency_key(trip_id: str, handoff_id: Any) -> str:
    """One key per hand-off: a re-send after a real edit is a new hand-off (new `sent` event id)."""
    return f"{trip_id}:{handoff_id}"


def leg_chain(itinerary: list[dict]) -> list[dict]:
    """Group consecutive itinerary days that come from the same AA tour into legs.

    Each leg: {position, source_tour_id, day_from, day_to} where day_from / day_to are the tour's
    own day indexes (tour_day.day_index). Days with no source tour (none today) are skipped.
    """
    legs: list[dict] = []
    for entry in sorted(itinerary, key=lambda e: e["day"]):
        tour = entry.get("source_tour_id")
        idx = entry.get("source_day_index")
        if not tour or idx is None:
            continue
        last = legs[-1] if legs else None
        # Same tour day (several activities on one day) or its next day → the same leg.
        if last and last["source_tour_id"] == str(tour) and idx in (last["day_to"], last["day_to"] + 1):
            last["day_to"] = idx
        else:
            legs.append({"position": len(legs) + 1, "source_tour_id": str(tour),
                         "day_from": idx, "day_to": idx})
    return legs


def build_payload(
    *,
    trip_id: str,
    handoff_id: Any,
    customer: Optional[dict],
    itinerary: list[dict],
    custom_days: list[dict],
    submitted_at: Optional[datetime] = None,
) -> dict:
    submitted_at = submitted_at or datetime.now(timezone.utc)
    return {
        "contract": EVENT,
        "version": SCHEMA_VERSION,
        "idempotency_key": idempotency_key(trip_id, handoff_id),
        "source": "tripplanner",
        "trip_id": trip_id,
        "handoff_id": str(handoff_id),
        "submitted_at": submitted_at.isoformat(),
        "customer": customer,
        "itinerary": {
            "legs": leg_chain(itinerary),
            "days": [
                {
                    "day": e["day"],
                    "component_id": str(e["component_id"]),
                    "name": e.get("name"),
                    "source_tour_id": str(e["source_tour_id"]) if e.get("source_tour_id") else None,
                    "source_day_index": e.get("source_day_index"),
                    "destination_id": str(e["destination_id"]) if e.get("destination_id") else None,
                    "lat": e.get("lat"),
                    "lng": e.get("lng"),
                }
                for e in sorted(itinerary, key=lambda e: e["day"])
            ],
            "custom_days": [
                {"day_id": d["day_id"], "kind": d["kind"],
                 "after_component_id": d.get("after_component_id"), "note": d.get("note")}
                for d in custom_days
            ],
        },
    }


async def load_customer(conn: _Conn, session_id: str) -> Optional[dict]:
    """The customer claimed by this session, or None for a guest hand-off."""
    row = await conn.fetchrow(
        "SELECT c.id, c.name, c.phone, c.email, c.contact_consent, c.contact_consent_at "
        "FROM tripplanner.sessions s JOIN tripplanner.customers c ON c.id = s.customer_id "
        "WHERE s.id = $1",
        session_id,
    )
    if row is None:
        return None
    at = row["contact_consent_at"]
    return {
        "customer_id": str(row["id"]),
        "name": row["name"],
        "phone": row["phone"],
        "email": row["email"],
        "consent": bool(row["contact_consent"]),
        "consent_at": at.isoformat() if hasattr(at, "isoformat") else at,
        "verified": False,
    }


def submitted_contact(
    *, name: str, phone: Optional[str], email: Optional[str], consent: bool,
    submitted_at: Optional[datetime] = None,
) -> dict:
    """The contact exactly as submitted in this request, not linked to any stored customer."""
    phone, email = normalise_contact(phone, email)
    at = (submitted_at or datetime.now(timezone.utc)).isoformat() if consent else None
    return {
        "customer_id": None,
        "name": (name or "").strip(),
        "phone": phone,
        "email": email,
        "consent": bool(consent),
        "consent_at": at,
        "verified": False,
    }


async def write_inquiry(
    conn: _Conn,
    *,
    trip_id: str,
    handoff_id: Any,
    session_id: str,
    itinerary: list[dict],
    custom_days: list[dict],
    submitted: Optional[dict] = None,
) -> str:
    """Append the inquiry.submitted row. Caller holds the transaction. Returns the idempotency key.

    `submitted` (submitted_contact) is used when the session has no customer of its own — i.e. the
    contact in this request matched an existing customer and was deliberately not linked.

    ON CONFLICT DO NOTHING: the same hand-off can never produce two rows."""
    customer = await load_customer(conn, session_id)
    if customer is None and submitted is not None:
        customer = submitted
    payload = build_payload(trip_id=trip_id, handoff_id=handoff_id, customer=customer,
                            itinerary=itinerary, custom_days=custom_days)
    key = payload["idempotency_key"]
    await conn.execute(
        "INSERT INTO tripplanner.outbox (event, schema_version, idempotency_key, aggregate_id, payload) "
        "VALUES ($1, $2, $3, $4, $5::jsonb) ON CONFLICT (idempotency_key) DO NOTHING",
        EVENT, SCHEMA_MAJOR, key, trip_id, json.dumps(payload, default=str),
    )
    return key
