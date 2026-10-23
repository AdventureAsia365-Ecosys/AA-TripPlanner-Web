"""AA-676 — the inquiry.submitted hand-off to AA-Booking (transactional outbox, ADR 0001 decision 6).

send-to-advisor calls write_inquiry() inside its own transaction, right after the `sent` event,
so the outbox row and the hand-off commit or roll back together. AA-Booking reads
tripplanner.v_inquiry_outbox and keeps its own inbox keyed by idempotency_key; it never writes here.

Contract: AA-Ecosys docs/contracts/inquiry.submitted.v1.2.json (payload version 1.2, AA-830). Narration is not part of v1:
it is not persisted server-side (the frontend asks for it on demand), and a client-sent copy would be
untrusted text.

Tour key (AA-830, data standards X3 / DM-2.2): a component's `source_tour_id` is a catalog source_id for some
tours and a version tour_id (changes on re-upload) for others. Every leg therefore also carries the catalog
key `source_system` + `source_id`, resolved at send time against catalog.v_tour (accepting either key kind).
A key the catalog does not know (tour left the catalog) or an unreadable catalog leaves both null: the
hand-off is never refused over it, and AA-Booking reports the leg as unmatched.

Day guard (AA-847, TP #86): a component row's tour day can be wrong (AA-819). The legs use the same check as the
narration (master_content.guarded_days): a day whose AA text does not name the stop moves to the nearest day of
the same tour that does; `days[]` keeps the component's own row. AA text that cannot be read leaves the days as
they are (never refuses a hand-off).

Identity (S235 security fix): no customer field is verified — TripPlanner has no OTP; AA-Booking will
own identity and verification. Every customer object therefore carries `verified: false`. When the
submitted phone/email matched someone else's stored customer, the session is not linked to it
(registration.py) and the payload carries the submitted contact with `customer_id: null` — never the
stored identity of another person. Contract delta (verified, nullable customer_id) goes into v1.1.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional, Protocol

from backend.assembly import master_content
from backend.assembly.registration import normalise_contact

log = logging.getLogger(__name__)

EVENT = "inquiry.submitted"
# Payload `version` (major.minor). AA-Ecosys docs/contracts/inquiry.submitted.v1.2.json declares it
# as the NUMBER 1.2; v1.2 is additive over v1.1 (per-leg source_system + source_id, AA-830), v1.1 over
# v1 (customer.verified, nullable customer_id, AA-805).
SCHEMA_VERSION = 1.2
# tripplanner.outbox.schema_version is an INT column: it carries the major only.
SCHEMA_MAJOR = 1

# Both key kinds in one read: a leg's source_tour_id is either catalog.v_tour.source_id or a tour_id.
# The text comparison on tour_id is a filter over the ~900 live tours (measured 8 ms on Dev, S238).
TOUR_KEYS_SQL = (
    "SELECT source_system, source_id, tour_id::text AS tour_id FROM catalog.v_tour "
    "WHERE source_id = ANY($1::text[]) OR tour_id::text = ANY($1::text[])"
)


class _Conn(Protocol):
    async def execute(self, query: str, *args: Any) -> Any: ...
    async def fetchrow(self, query: str, *args: Any) -> Any: ...
    async def fetch(self, query: str, *args: Any) -> Any: ...


def idempotency_key(trip_id: str, handoff_id: Any) -> str:
    """One key per hand-off: a re-send after a real edit is a new hand-off (new `sent` event id)."""
    return f"{trip_id}:{handoff_id}"


def leg_chain(itinerary: list[dict]) -> list[dict]:
    """Group consecutive itinerary days that come from the same AA tour into legs.

    Each leg: {position, source_tour_id, day_from, day_to} where day_from / day_to are the tour's
    own day indexes (catalog day_index). Days with no source tour (none today) are skipped.
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


TourKey = tuple[str, str]  # (source_system, source_id)


async def resolve_tour_keys(conn: _Conn, keys: list[str]) -> dict[str, TourKey]:
    """Map each source_tour_id (either key kind) to its catalog key (source_system, source_id).

    Keys catalog.v_tour does not hold are absent from the result. A source_id match wins over a tour_id
    match (the two never collide on Dev: S238 check). Read inside a savepoint when the connection has
    one, so an unreadable catalog (grant missing, view renamed) only leaves the keys null and never
    aborts the hand-off transaction."""
    wanted = sorted({str(k) for k in keys if k})
    if not wanted:
        return {}
    try:
        if hasattr(conn, "transaction"):
            async with conn.transaction():
                rows = await conn.fetch(TOUR_KEYS_SQL, wanted)
        else:
            rows = await conn.fetch(TOUR_KEYS_SQL, wanted)
    except Exception as exc:  # noqa: BLE001 — never refuse a traveller's hand-off over the catalog
        log.warning("catalog.v_tour unreadable, hand-off legs carry no catalog key: %s", type(exc).__name__)
        return {}
    by_tour: dict[str, TourKey] = {}
    by_source: dict[str, TourKey] = {}
    for r in rows or []:
        key = (r["source_system"], r["source_id"])
        by_source[r["source_id"]] = key
        by_tour[r["tour_id"]] = key
    return {k: by_source.get(k) or by_tour[k] for k in wanted if k in by_source or k in by_tour}


async def load_day_texts(conn: _Conn, keys: list) -> dict[tuple[str, int], str]:
    """AA's day texts for the day guard; {} when unreadable. In a savepoint when the connection has one, so a
    failed read never aborts the hand-off transaction."""
    try:
        if hasattr(conn, "transaction"):
            async with conn.transaction():
                return await master_content.load_for_tours(conn, keys)
        return await master_content.load_for_tours(conn, keys)
    except Exception as exc:  # noqa: BLE001 — the guard is best effort, the hand-off is not
        log.warning("day texts unreadable, hand-off legs use the component days: %s", type(exc).__name__)
        return {}


def with_tour_keys(legs: list[dict], tour_keys: dict[str, TourKey]) -> list[dict]:
    """Add source_system + source_id to each leg (both None when the key is unresolved), keeping the
    contract's field order: position, source_tour_id, source_system, source_id, day_from, day_to."""
    out = []
    for leg in legs:
        system, source_id = tour_keys.get(leg["source_tour_id"]) or (None, None)
        out.append({"position": leg["position"], "source_tour_id": leg["source_tour_id"],
                    "source_system": system, "source_id": source_id,
                    "day_from": leg["day_from"], "day_to": leg["day_to"]})
    return out


def build_payload(
    *,
    trip_id: str,
    handoff_id: Any,
    customer: Optional[dict],
    itinerary: list[dict],
    custom_days: list[dict],
    tour_keys: Optional[dict[str, TourKey]] = None,
    submitted_at: Optional[datetime] = None,
    day_texts: Optional[dict[tuple[str, int], str]] = None,
) -> dict:
    """The v1.2 payload. `tour_keys` (resolve_tour_keys) maps source_tour_id to the catalog key; a leg
    whose key is missing carries source_system / source_id null. `day_texts` (AA's day texts of the tours,
    master_content.load_for_tours) turn on the day guard of the legs (AA-847)."""
    legs_from = master_content.guarded_days(itinerary, day_texts) if day_texts else itinerary
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
            "legs": with_tour_keys(leg_chain(legs_from), tour_keys or {}),
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
    tour_keys = await resolve_tour_keys(conn, [e.get("source_tour_id") for e in itinerary])
    day_texts = await load_day_texts(conn, [e.get("source_tour_id") for e in itinerary])
    payload = build_payload(trip_id=trip_id, handoff_id=handoff_id, customer=customer,
                            itinerary=itinerary, custom_days=custom_days, tour_keys=tour_keys,
                            day_texts=day_texts)
    key = payload["idempotency_key"]
    await conn.execute(
        "INSERT INTO tripplanner.outbox (event, schema_version, idempotency_key, aggregate_id, payload) "
        "VALUES ($1, $2, $3, $4, $5::jsonb) ON CONFLICT (idempotency_key) DO NOTHING",
        EVENT, SCHEMA_MAJOR, key, trip_id, json.dumps(payload, default=str),
    )
    return key
