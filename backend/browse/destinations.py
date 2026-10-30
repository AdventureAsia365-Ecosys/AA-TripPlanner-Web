"""Destination-detail query for the Map/Browse Lambda (Lambda A).

Lists every catalog component at a destination (name, activity,
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

AA-997 (S260): catalog.v_destination lists EVERY CIS destination, also a merged one (its row stays, with
``duplicate_of`` set) and a hidden one (``geo_status = 'suspect'``: CIS could not confirm its point, or its point
policy hid it). The tiles, search, routes and component reads already drop both (``catalog.v_tour_component`` and
the graph views filter them); only this read by id did not, so an old link answered 200 with the place's
precision / line bbox. Now:
- a merged id is not read here (``DEST_SQL`` skips a row with a ``v_destination_redirect`` entry), so the handler
  answers 301 to the keeper as the docstring above always meant;
- a suspect row still answers 200 (name, country, cover photo — no point is in them) but without any location
  field (no located_by / precision / shape / bbox) and with ``location_hidden: true``; its component list is
  empty (the component view drops it). The frontend opens this popup only from a pin, and a suspect place has no
  pin, so only a stale link reaches it.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Protocol

from backend.browse.tiles import LINE_COLS_SQL, geo_fields

log = logging.getLogger(__name__)


class _Pool(Protocol):
    async def fetchrow(self, query: str, *args: Any) -> Any: ...
    async def fetch(self, query: str, *args: Any) -> Any: ...


# AA-712 slice 6b: the CIS catalog views (228), the only source since AA-712 X4. Component ids are the views'
# stable component_id (uuid5).
# AA-997: a merged row (it has a v_destination_redirect entry) is not served here: the handler redirects it.
DEST_SQL = f"""
    SELECT d.destination_id AS id, d.name, d.country, d.cover_image_url, d.geo_status, d.located_by, d.precision,
           {LINE_COLS_SQL}
    FROM catalog.v_destination d
    WHERE d.destination_id = $1::uuid
      AND NOT EXISTS (SELECT 1 FROM catalog.v_destination_redirect r WHERE r.destination_id = d.destination_id)
"""
# AA-997: CIS geo_status of a destination whose point readers must not show (catalog.v_destination.geo_status).
HIDDEN_GEO_STATUS = "suspect"
REDIRECT_SQL = """
    SELECT keeper_id FROM catalog.v_destination_redirect WHERE destination_id = $1::uuid
"""
COMPONENTS_SQL = """
    SELECT component_id AS id, name, activity, intensity_level, duration_hint, text_extract
    FROM catalog.v_tour_component
    WHERE destination_id = $1::uuid
    ORDER BY name, component_id
"""


def location_hidden(row: Any) -> bool:
    """True when CIS hides this destination's point from readers (``geo_status = 'suspect'``)."""
    keys = row.keys() if hasattr(row, "keys") else ()
    return "geo_status" in keys and row["geo_status"] == HIDDEN_GEO_STATUS


async def destination_detail(destination_id: str, *, pool: _Pool) -> dict | None:
    dest = await pool.fetchrow(DEST_SQL, destination_id)
    if dest is None:
        return None
    rows = await pool.fetch(COMPONENTS_SQL, destination_id)
    hidden = location_hidden(dest)
    return {
        "id": str(dest["id"]),
        "name": dest["name"],
        "country": dest["country"],
        "cover_image_url": dest["cover_image_url"],
        # AA-997: a hidden place keeps its name and photo but never its location fields
        **({"location_hidden": True} if hidden else geo_fields(dest)),
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
    """The keeper of a merged destination, else None. A malformed id is never looked up."""
    try:
        uuid.UUID(str(destination_id))
    except ValueError:
        return None
    try:
        row = await pool.fetchrow(REDIRECT_SQL, destination_id)
    except Exception as e:  # noqa: BLE001 — e.g. the view before CIS 257: a 404 stays a 404, never a 500
        log.warning("destination_redirect_unavailable: %s", type(e).__name__)
        return None
    return str(row["keeper_id"]) if row is not None and row["keeper_id"] else None
