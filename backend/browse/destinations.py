"""Destination-detail query for the Map/Browse Lambda (Lambda A).

Lists every itinerary_component at a destination (name, activity,
duration, description, thumbnail). Cacheable behind CloudFront.

`in_trip_day` is left null here — whether a component is already in the
visitor's trip is per-visitor state owned by Lambda B, so the browse
Lambda never joins trip state (keeps this endpoint cacheable). The
frontend overlays trip membership from its own trip state.

AA-847 (CIS 257): the CIS job place_dedupe merges duplicate destinations
automatically. A merged id has no catalog.v_destination row any more;
catalog.v_destination_redirect maps it to its keeper (always a root), so an
old link / shared URL answers 301 to the keeper instead of 404. Drafts are not
affected (trip events pin component_id, which survives a merge).
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Protocol

from backend.browse.tiles import LINE_COLS, geo_fields
from backend.shared import places_source

log = logging.getLogger(__name__)


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
REDIRECT_SQL_CIS = """
    SELECT keeper_id FROM catalog.v_destination_redirect WHERE destination_id = $1::uuid
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


async def destination_redirect(destination_id: str, *, pool: _Pool) -> str | None:
    """The keeper of a merged destination (PLACES_SOURCE=cis only), else None. A malformed id is never looked up."""
    if not places_source.is_cis():
        return None
    try:
        uuid.UUID(str(destination_id))
    except ValueError:
        return None
    try:
        row = await pool.fetchrow(REDIRECT_SQL_CIS, destination_id)
    except Exception as e:  # noqa: BLE001 — e.g. the view before CIS 257: a 404 stays a 404, never a 500
        log.warning("destination_redirect_unavailable: %s", type(e).__name__)
        return None
    return str(row["keeper_id"]) if row is not None and row["keeper_id"] else None
