"""Destination-detail query for the Map/Browse Lambda (Lambda A).

Lists every itinerary_component at a destination (name, activity,
duration, description, thumbnail). Cacheable behind CloudFront.

`in_trip_day` is left null here — whether a component is already in the
visitor's trip is per-visitor state owned by Lambda B, so the browse
Lambda never joins trip state (keeps this endpoint cacheable). The
frontend overlays trip membership from its own trip state.
"""
from __future__ import annotations

from typing import Any, Protocol


class _Pool(Protocol):
    async def fetchrow(self, query: str, *args: Any) -> Any: ...
    async def fetch(self, query: str, *args: Any) -> Any: ...


async def destination_detail(destination_id: str, *, pool: _Pool) -> dict | None:
    dest = await pool.fetchrow(
        "SELECT id, name, country, cover_image_url "
        "FROM shared.destinations WHERE id = $1",
        destination_id,
    )
    if dest is None:
        return None
    rows = await pool.fetch(
        """
        SELECT id, name, activity, intensity_level, duration_hint,
               text_extract
        FROM tripplanner.itinerary_components
        WHERE destination_id = $1
        ORDER BY name
        """,
        destination_id,
    )
    return {
        "id": str(dest["id"]),
        "name": dest["name"],
        "country": dest["country"],
        "cover_image_url": dest["cover_image_url"],
        "components": [
            {
                "id": str(r["id"]),
                "name": r["name"],
                "activity": r["activity"],
                "intensity_level": r["intensity_level"],
                "duration_hint": r["duration_hint"],
                "text_extract": r["text_extract"],
                "thumbnail_url": None,
                "in_trip_day": None,
            }
            for r in rows
        ],
    }
