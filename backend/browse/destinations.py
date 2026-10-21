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

from backend.browse.tiles import LINE_COLS, geo_fields
from backend.shared import places_source


class _Pool(Protocol):
    async def fetchrow(self, query: str, *args: Any) -> Any: ...
    async def fetch(self, query: str, *args: Any) -> Any: ...


# AA-712 slice 6b: PLACES_SOURCE=cis reads the CIS catalog views (228). Component ids are the views'
# stable component_id (uuid5), not tripplanner.itinerary_components ids.
DEST_SQL = "SELECT id, name, country, cover_image_url FROM shared.destinations WHERE id = $1"
DEST_SQL_CIS = f"""
    SELECT destination_id AS id, name, country, cover_image_url, located_by, precision, {", ".join(LINE_COLS)}
    FROM catalog.v_destination WHERE destination_id = $1::uuid
"""
COMPONENTS_SQL = """
    SELECT id, name, activity, intensity_level, duration_hint,
           text_extract
    FROM tripplanner.itinerary_components
    WHERE destination_id = $1
    ORDER BY name
"""
COMPONENTS_SQL_CIS = """
    SELECT component_id AS id, name, activity, intensity_level, duration_hint, text_extract
    FROM catalog.v_tour_component
    WHERE destination_id = $1::uuid
    ORDER BY name, component_id
"""


async def destination_detail(destination_id: str, *, pool: _Pool) -> dict | None:
    dest = await pool.fetchrow(places_source.pick(DEST_SQL, DEST_SQL_CIS), destination_id)
    if dest is None:
        return None
    rows = await pool.fetch(places_source.pick(COMPONENTS_SQL, COMPONENTS_SQL_CIS), destination_id)
    return {
        "id": str(dest["id"]),
        "name": dest["name"],
        "country": dest["country"],
        "cover_image_url": dest["cover_image_url"],
        **geo_fields(dest),
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
