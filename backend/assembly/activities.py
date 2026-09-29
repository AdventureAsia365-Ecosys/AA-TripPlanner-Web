"""AA-674 / PR-11 point 2 — the activities a traveller can select at a stop.

A component is a place + activity taken from a real AA tour day (itinerary_components), so
selecting an activity is pinning its component (event add_component) and deselecting is
remove_component. Both events are kept in the trip log and reach the advisor. This lists what
can be selected at one destination: only what AA tours actually do there, so an activity no tour
offers is never selectable. One entry per (name, activity), with the tours that offer it.
"""
from __future__ import annotations

from typing import Any

from backend.assembly import events as events_mod

ACTIVITIES_SQL = """
    SELECT (array_agg(c.id::text ORDER BY c.source_tour_id, c.source_day_index))[1] AS component_id,
           array_agg(c.id::text) AS component_ids,
           c.name, c.activity, (array_agg(c.intensity_level))[1] AS intensity_level,
           (array_agg(c.duration_hint))[1] AS duration_hint,
           (array_agg(c.text_extract ORDER BY length(c.text_extract) DESC))[1] AS text_extract,
           count(DISTINCT c.source_tour_id) AS tour_count
    FROM tripplanner.itinerary_components c
    WHERE c.destination_id = $1::uuid
    GROUP BY c.name, c.activity
    ORDER BY count(DISTINCT c.source_tour_id) DESC, c.name
"""


async def at_stop(conn: Any, trip_id: str, destination_id: str) -> dict:
    pinned = {str(c["id"]) for c in await events_mod._current_components(conn, trip_id)}  # noqa: SLF001
    out = []
    for r in await conn.fetch(ACTIVITIES_SQL, destination_id):
        ids = list(r["component_ids"])
        chosen = next((i for i in ids if i in pinned), None)
        out.append({"component_id": chosen or r["component_id"], "name": r["name"], "activity": r["activity"],
                    "intensity_level": r["intensity_level"], "duration_hint": r["duration_hint"],
                    "text_extract": r["text_extract"], "tour_count": int(r["tour_count"]),
                    "selected": chosen is not None})
    return {"destination_id": destination_id, "activities": out}
