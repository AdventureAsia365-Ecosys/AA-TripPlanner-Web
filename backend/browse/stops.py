"""AA-1041 (R7 TripPlanner UX, slice 1) — the planner's stops by component id, with their place photo.

  GET /browse/stops?ids=<component uuid>,<component uuid>…   (at most 50)

One answer serves two readers:
- the planner's trip panel: a photo per stop and per day (the place's CIS cover photo, ``catalog.v_destination
  .cover_photo_path``, the photo coverage of AA-997), and the stop's activity for its icon;
- the read-only share / print view (``/plan/shared#s=<ids>``): the shared link carries component ids only, never text,
  so the page shows only what the CIS catalog says about those stops (no text someone typed into a link).

Reads only the CIS catalog views TripPlanner already reads (``catalog.v_tour_component`` joined to
``catalog.v_destination``, the same join as the assembly Lambda's itinerary; root ADR 0001). A stop whose tour is not
served any more is simply absent (the views carry live tours only). No point is returned: the share view draws no map,
and a place CIS hides (``geo_status = 'suspect'``, AA-997) keeps its name and photo only. Catalog data only (no trip,
no guest, no PII), so the answer is CDN-cacheable like ``/browse/shapes``: the same id set gives the same answer until
the catalog changes.
"""
from __future__ import annotations

from typing import Any, Optional, Protocol

from backend.browse.catalog import photo_url
from backend.browse.shapes import ShapesError, parse_ids

# Same cap as /browse/shapes: a trip longer than 50 stops is asked in batches by the client.
MAX_IDS = 50

STOPS_SQL = """
    SELECT c.component_id::text AS id, c.name, c.activity, c.duration_hint,
           c.source_id AS source_tour_id, c.day_index AS source_day_index,
           c.destination_id::text AS destination_id, d.name AS destination_name, d.country,
           d.precision, d.cover_photo_path
    FROM catalog.v_tour_component c
    JOIN catalog.v_destination d ON d.destination_id = c.destination_id
    WHERE c.component_id = ANY($1::uuid[])
"""


class _Pool(Protocol):
    async def fetch(self, query: str, *args: Any) -> Any: ...


class StopsError(ValueError):
    pass


def _stop(r: Any) -> dict:
    path: Optional[str] = r["cover_photo_path"]
    return {
        "id": r["id"],
        "name": r["name"],
        "activity": r["activity"],
        "duration_hint": r["duration_hint"],
        "source_tour_id": r["source_tour_id"],
        "source_day_index": r["source_day_index"],
        "destination_id": r["destination_id"],
        "destination_name": r["destination_name"],
        "country": r["country"],
        "precision": r["precision"],
        # None = the place has no CIS photo yet: the client shows the activity icon tile instead.
        "photo_url": photo_url(path, "large"),
        "photo_url_small": photo_url(path, "small"),
    }


async def stops(ids_raw: Optional[str], *, pool: _Pool) -> dict:
    """The served stops among ``ids`` (sorted by id; the caller keeps its own order), each with its place photo."""
    try:
        ids = parse_ids(ids_raw)
    except ShapesError as e:
        raise StopsError(str(e)) from None
    rows = await pool.fetch(STOPS_SQL, ids)
    # One row per component (the view's key); a duplicate row would only repeat the same stop.
    seen: dict[str, dict] = {}
    for r in rows:
        seen.setdefault(r["id"], _stop(r))
    return {"stops": [seen[k] for k in sorted(seen)]}
