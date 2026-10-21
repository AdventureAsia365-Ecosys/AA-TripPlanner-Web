"""Append-only trip_events writer + trip_drafts projection updater.

trip_events is the canonical source of truth (never deleted). trip_drafts
is a read-cache PROJECTION, updated in the SAME transaction as each event
append. Rule enforced here: you cannot update the projection without
appending the corresponding event — both happen inside append_event().

The projection's itinerary is recomputed from the current component set
using deterministic sequencing (tour blocks in day order, AA-805); manual reorder events pin an
explicit order that overrides the algorithm until the component set changes again.

AA-847 (AA-712 slice 6c): the component reads follow PLACES_SOURCE (backend/shared/places_source.py). On the cis
path the components come from the CIS catalog views and every component id of the event log (add, remove,
reorder) is first resolved through tripplanner.component_id_map (backend/shared/component_ids.py), so a draft
started before the flip, or an id from a browse answer cached before it, still folds to the same components.
"""
from __future__ import annotations

import json
from typing import Any, Optional, Protocol

from backend.assembly import sequencing
from backend.shared import component_ids, places_source

VALID_EVENTS = {"add_component", "remove_component", "reorder", "sent", "add_day", "remove_day"}

# AA-674 / PR-11 point 3: a day the traveller adds on top of the AA legs. It is not part of any
# tour, so the Trip Case shows it as a customization. kind: extra_night (stay one more night at a
# stop) | free_day | extend_end (one more day after the last stop).
DAY_KINDS = {"extra_night", "free_day", "extend_end"}


class _Conn(Protocol):
    async def execute(self, query: str, *args: Any) -> Any: ...
    async def fetch(self, query: str, *args: Any) -> Any: ...
    async def fetchrow(self, query: str, *args: Any) -> Any: ...
    def transaction(self) -> Any: ...


# The pinned components with their destination point. $1 = component ids (uuid[]).
COMPONENTS_SQL = """
    SELECT c.id, c.name, c.activity, c.duration_hint, c.text_extract,
           c.source_tour_id, c.source_day_index, c.destination_id,
           d.name AS destination_name, d.lat AS lat, d.lng AS lng
    FROM tripplanner.itinerary_components c
    JOIN shared.destinations d ON d.id = c.destination_id
    WHERE c.id = ANY($1::uuid[])
"""
# AA-847 slice 6c: the catalog views (live tours, usable destinations only); keyed by catalog source_id.
COMPONENTS_SQL_CIS = """
    SELECT c.component_id AS id, c.name, c.activity, c.duration_hint, c.text_extract,
           c.source_id AS source_tour_id, c.day_index AS source_day_index, c.destination_id,
           d.name AS destination_name, d.lat AS lat, d.lng AS lng, d.located_by, d.precision
    FROM catalog.v_tour_component c
    JOIN catalog.v_destination d ON d.destination_id = c.destination_id
    WHERE c.component_id = ANY($1::uuid[])
"""


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
    folded: list[tuple[str, str]] = []
    for r in rows:
        payload = r["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        if r["event_type"] in ("add_component", "remove_component"):
            folded.append((r["event_type"], str(payload["component_id"])))
    # AA-847: one id per component across the flip (an old add and a new remove cancel out)
    mapping = await component_ids.id_map(conn, [cid for _, cid in folded])
    present: list[str] = []
    for etype, raw in folded:
        cid = mapping.get(raw, raw)
        if etype == "add_component":
            if cid not in present:
                present.append(cid)
        elif cid in present:
            present.remove(cid)
    if not present:
        return []
    comp_rows = await conn.fetch(places_source.pick(COMPONENTS_SQL, COMPONENTS_SQL_CIS), present)
    by_id = {str(r["id"]): dict(r) for r in comp_rows}
    # preserve add order for stable fallback
    return [by_id[cid] for cid in present if cid in by_id]


async def current_custom_days(conn: _Conn, trip_id: str) -> list[dict]:
    """Fold add_day / remove_day events into the trip's added days, in the order added.
    Each: {day_id, kind, after_component_id, note}."""
    rows = await conn.fetch(
        "SELECT event_type, payload FROM tripplanner.trip_events "
        "WHERE trip_id = $1 AND event_type IN ('add_day', 'remove_day') ORDER BY id ASC",
        trip_id,
    )
    days: dict[str, dict] = {}
    for r in rows:
        payload = r["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        if r["event_type"] == "add_day":
            days[payload["day_id"]] = {"day_id": payload["day_id"], "kind": payload["kind"],
                                       "after_component_id": payload.get("after_component_id"),
                                       "note": payload.get("note"), "customization": True}
        elif r["event_type"] == "remove_day":
            days.pop(payload.get("day_id"), None)
    return list(days.values())


async def _latest_explicit_order(conn: _Conn, trip_id: str) -> Optional[list[str]]:
    """Return the ordered_component_ids of the most recent reorder event IF no add/remove happened
    after it (a later set-change invalidates a manual order). `sent`, `add_day` and `remove_day`
    leave the set, and therefore the order, as it was.

    AA-805: an apply-route pins a route in its day order through a reorder event; the `sent` event
    that follows must not drop that order, or the hand-off is re-sequenced by geography (S235:
    legs 3-4, 7, 2, 1 of one tour)."""
    row = await conn.fetchrow(
        "SELECT event_type, payload FROM tripplanner.trip_events "
        "WHERE trip_id = $1 AND event_type IN ('reorder', 'add_component', 'remove_component') "
        "ORDER BY id DESC LIMIT 1",
        trip_id,
    )
    if row is None or row["event_type"] != "reorder":
        return None
    payload = row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    ordered = payload.get("ordered_component_ids")
    if not ordered:
        return ordered
    return await component_ids.resolve(conn, ordered)  # AA-847: ids from before the flip


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
            "destination_id": str(e["destination_id"]) if e.get("destination_id") else None,
            # AA-819: narration checks that a tour day's text visits this place (master_content).
            "destination_name": e.get("destination_name"),
            "lat": e.get("lat"),
            "lng": e.get("lng"),
            # AA-847: how the point was located (cis path only; ADR 0007: 'area' is drawn as an area)
            "located_by": e.get("located_by"),
            "precision": e.get("precision"),
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
    transaction. Returns the new itinerary projection.

    The sessions row must already exist: the handler's _guard creates or binds it (AA-827,
    guest_session.authorize) before any mutation. If a route ever skips the guard, the
    trip_events.session_id FK fails loudly instead of creating an unauthenticated session."""
    if event_type not in VALID_EVENTS:
        raise ValueError(f"invalid event_type: {event_type}")

    async with conn.transaction():
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
