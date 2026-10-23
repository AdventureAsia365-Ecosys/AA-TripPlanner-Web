"""AA-847 slice 6d (owner decision S246, D1) — the geometry of LINE destinations (rivers, roads, trekking stretches)
by id, for the browse map, search results and the trip map.

  GET /browse/shapes?ids=<uuid>,<uuid>…&level=lo|mid

Reads catalog.v_destination_shape (CIS 256, contract catalog.graph.v1.2 `destination_shapes`; tripplanner only).
``lo`` (≤ 60 vertices) below zoom 7 and for static maps, ``mid`` (≤ 300 vertices / 12 KB) from zoom 7 up. Tiles
never carry geometry (a long river would ship in every tile it crosses); the client caches shapes by id and the
response is CDN-cacheable (same ids → same answer until the next shape change).

``status``: ok / manual (draw it), approximate (draw it dashed, label "approximate route"), pending / not_found /
failed (no geometry: the trip map draws the provisional dashed line between the day's stops).
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional, Protocol

MAX_IDS = 50
LEVELS = ("lo", "mid")
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

SHAPES_SQL = {
    "lo": "SELECT destination_id, shape_status, shape_lo AS shape FROM catalog.v_destination_shape "
          "WHERE destination_id = ANY($1::uuid[])",
    "mid": "SELECT destination_id, shape_status, shape_mid AS shape FROM catalog.v_destination_shape "
           "WHERE destination_id = ANY($1::uuid[])",
}


class _Pool(Protocol):
    async def fetch(self, query: str, *args: Any) -> Any: ...


class ShapesError(ValueError):
    pass


def parse_ids(raw: Optional[str]) -> list[str]:
    """Comma-separated uuids, deduplicated and sorted (one cache key per id set), at most MAX_IDS."""
    ids = sorted({i.strip().lower() for i in (raw or "").split(",") if i.strip()})
    if not ids:
        raise ShapesError("ids required")
    if len(ids) > MAX_IDS:
        raise ShapesError(f"at most {MAX_IDS} ids")
    bad = [i for i in ids if not _UUID_RE.match(i)]
    if bad:
        raise ShapesError("ids must be uuids")
    return ids


def _geojson(v: Any) -> Optional[dict]:
    if v is None:
        return None
    return json.loads(v) if isinstance(v, str) else v


async def shapes(ids_raw: Optional[str], level: Optional[str], *, pool: _Pool) -> dict:
    ids = parse_ids(ids_raw)
    lvl = (level or "lo").lower()
    if lvl not in LEVELS:
        raise ShapesError(f"level must be one of {LEVELS}")
    rows = await pool.fetch(SHAPES_SQL[lvl], ids)
    return {"level": lvl,
            "shapes": [{"id": str(r["destination_id"]), "status": r["shape_status"], "geometry": _geojson(r["shape"])}
                       for r in rows]}
