"""Append-only trip_events writer + trip_drafts projection updater.

trip_events is the canonical source of truth (never deleted). trip_drafts
is a read-cache PROJECTION, updated in the SAME transaction as each event
append. Rule enforced here: you cannot update the projection without
appending the corresponding event — both happen inside append_event().

The projection's itinerary is recomputed from the current component set
using deterministic sequencing; manual reorder events pin an explicit
order that overrides the algorithm until the component set changes again.
"""
from __future__ import annotations

import json
from typing import Any, Optional, Protocol

from backend import config
from backend.assembly import sequencing

VALID_EVENTS = {"add_component", "remove_component", "reorder", "sent"}


class _Conn(Protocol):
    async def execute(self, query: str, *args: Any) -> Any: ...
    async def fetch(self, query: str, *args: Any) -> Any: ...
    async def fetchrow(self, query: str, *args: Any) -> Any: ...
    def transaction(self) -> Any: ...


def _project_itinerary(
    components: list[dict], explicit_order: Optional[list[str]]
) -> list[dict]:
    """Compute the itinerary projection.

    If explicit_order is given (a manual reorder), day-order follows it
    exactly. Otherwise components are sequenced deterministically by
    geography.
    """
    by_id = {str(c["id"]): c for c in components}
    if explicit_order:
        ordered = [by_id[cid] for cid in explicit_order if cid in by_id]
        # append any components not named in the explicit order (safety)
        for cid, c in by_id.items():
            if cid not in explicit_order:
                ordered.append(c)
        return sequencing.group_into_days(ordered)
    return sequencing.sequence_and_group(components)


async def _current_components(conn: _Conn, trip_id: str) -> list[dict]:
    """Reconstruct the current component set for a trip by folding its
    event log (add/remove), then hydrate lat/lng from the components +
    destinations tables. Canonical read comes from trip_events."""
    rows = await conn.fetch(
        "SELECT event_type, payload FROM tripplanner.trip_events "
        "WHERE trip_id = $1 ORDER BY id ASC",
        trip_id,
    )
    present: list[str] = []
    for r in rows:
        payload = r["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        etype = r["event_type"]
        if etype == "add_component":
            cid = payload["component_id"]
            if cid not in present:
                present.append(cid)
        elif etype == "remove_component":
            cid = payload["component_id"]
            if cid in present:
                present.remove(cid)
    if not present:
        return []
    comp_rows = await conn.fetch(
        """
        SELECT c.id, c.name, c.activity, c.duration_hint, c.text_extract,
               c.source_tour_id, c.source_day_index,
               d.lat AS lat, d.lng AS lng
        FROM tripplanner.itinerary_components c
        JOIN shared.destinations d ON d.id = c.destination_id
        WHERE c.id = ANY($1::uuid[])
        """,
        present,
    )
    by_id = {str(r["id"]): dict(r) for r in comp_rows}
    # preserve add order for stable fallback
    return [by_id[cid] for cid in present if cid in by_id]


async def _latest_explicit_order(conn: _Conn, trip_id: str) -> Optional[list[str]]:
    """Return the ordered_component_ids from the most recent reorder event
    IF no add/remove happened after it (a later set-change invalidates a
    manual order)."""
    row = await conn.fetchrow(
        "SELECT event_type, payload FROM tripplanner.trip_events "
        "WHERE trip_id = $1 ORDER BY id DESC LIMIT 1",
        trip_id,
    )
    if row is None or row["event_type"] != "reorder":
        return None
    payload = row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    return payload.get("ordered_component_ids")


async def _ensure_guest_session(conn: _Conn, session_id: str) -> None:
    """Guarantee a sessions row exists for session_id before any event that
    FK-references it. The app has no separate 'create session' call — the
    first pin (add_component) from a fresh visitor lazily creates an
    anonymous GUEST session (guest_token = the session id, 90-day expiry).
    Registration later attaches a customer_id and clears the expiry
    (registration.py). Idempotent: a no-op if the session already exists,
    and never resets a registered session's expiry.
    """
    await conn.execute(
        """
        INSERT INTO tripplanner.sessions (id, guest_token, expires_at)
        VALUES ($1::uuid, ($1::uuid)::text, now() + make_interval(days => $2::int))
        ON CONFLICT (id) DO NOTHING
        """,
        session_id,
        config.GUEST_SESSION_DAYS,
    )


async def current_itinerary(conn: _Conn, trip_id: str) -> list[dict]:
    """Read-only: recompute the current day-ordered itinerary for a trip,
    including text_extract per day (needed by the narration LLM). Uses the
    same projection rules as append_event (deterministic sequence, unless a
    still-valid manual reorder pins the order), but writes nothing.

    Returns [{day, component_id, name, text_extract, activity, ...}].
    """
    components = await _current_components(conn, trip_id)
    explicit = await _latest_explicit_order(conn, trip_id)
    itinerary = _project_itinerary(components, explicit)
    return [
        {
            "day": e["day"],
            "component_id": e["component_id"],
            "name": e.get("name"),
            "text_extract": e.get("text_extract", ""),
            "source_tour_id": e.get("source_tour_id"),
            "source_day_index": e.get("source_day_index"),
        }
        for e in itinerary
    ]


async def append_event(
    conn: _Conn,
    trip_id: str,
    session_id: str,
    event_type: str,
    payload: dict,
) -> list[dict]:
    """Append an event and update the trip_drafts projection in ONE
    transaction. Returns the new itinerary projection."""
    if event_type not in VALID_EVENTS:
        raise ValueError(f"invalid event_type: {event_type}")

    async with conn.transaction():
        await _ensure_guest_session(conn, session_id)
        await conn.execute(
            "INSERT INTO tripplanner.trip_events "
            "(trip_id, session_id, event_type, payload) "
            "VALUES ($1, $2, $3, $4::jsonb)",
            trip_id,
            session_id,
            event_type,
            json.dumps(payload),
        )

        components = await _current_components(conn, trip_id)
        explicit = await _latest_explicit_order(conn, trip_id)
        itinerary = _project_itinerary(components, explicit)

        status = "sent" if event_type == "sent" else "draft"
        itinerary_json = json.dumps(
            [
                {
                    "day": e["day"],
                    "component_id": e["component_id"],
                    "name": e.get("name"),
                    "rationale": e.get("rationale"),
                }
                for e in itinerary
            ]
        )
        await conn.execute(
            """
            INSERT INTO tripplanner.trip_drafts (id, session_id, status, itinerary)
            VALUES ($1, $2, $3, $4::jsonb)
            ON CONFLICT (id) DO UPDATE
              SET itinerary = EXCLUDED.itinerary,
                  status = CASE WHEN tripplanner.trip_drafts.status = 'sent'
                                THEN 'sent' ELSE EXCLUDED.status END,
                  updated_at = now()
            """,
            trip_id,
            session_id,
            status,
            itinerary_json,
        )

    return itinerary
