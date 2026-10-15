"""Fixed-grid tile query for the Map/Browse Lambda (Lambda A).

A tile_id encodes the integer lat/lng cell as "<latCell>_<lngCell>" (a 1-degree
cell, matches frontend/lib/mapbox.ts tileIdFor), or "<latCell>_<lngCell>_<size>"
for a coarser cell of `size` degrees (AA-812 hotfix: the map asks for a few
coarse tiles at low zoom instead of one request per 1-degree cell, which sent
~10k requests for a world view). A tile covers [latCell, latCell+size) x
[lngCell, lngCell+size); both corners must sit on the size grid so a tile has
exactly one id (CDN cache friendly). We return the destinations that fall in the
cell AND have at least one component matching the active filters, with a
per-destination component_count.

Cacheable behind CloudFront (fixed 30-min TTL) — this endpoint takes no
per-visitor state.
"""
from __future__ import annotations

from typing import Any, Protocol

from backend.browse.filters import BrowseFilters
from backend.shared import places_source


class _Pool(Protocol):
    async def fetch(self, query: str, *args: Any) -> Any: ...


class TileError(ValueError):
    pass


# Allowed cell sizes in degrees. Each divides 90 and 180, so the grid tiles the
# globe exactly. Keep in sync with TILE_SIZES in frontend/lib/tileLoader.ts.
TILE_SIZES = (1, 2, 5, 10, 30, 90)


def parse_tile_id(tile_id: str) -> tuple[int, int, int]:
    """Parse "<lat>_<lng>" or "<lat>_<lng>_<size>" into (lat, lng, size).
    Raises TileError."""
    parts = tile_id.split("_")
    if len(parts) not in (2, 3):
        raise TileError(f"Malformed tile_id: {tile_id!r}")
    try:
        lat_cell = int(parts[0])
        lng_cell = int(parts[1])
        size = int(parts[2]) if len(parts) == 3 else 1
    except ValueError as e:
        raise TileError(f"Non-integer tile cell in {tile_id!r}") from e
    if size not in TILE_SIZES:
        raise TileError(f"tile size must be one of {TILE_SIZES}: {tile_id!r}")
    if lat_cell % size or lng_cell % size:
        raise TileError(f"tile corner not on the {size}-degree grid: {tile_id!r}")
    if not (-90 <= lat_cell <= 90 - size) or not (-180 <= lng_cell <= 180 - size):
        raise TileError(f"tile_id out of range: {tile_id!r}")
    return lat_cell, lng_cell, size


def build_tile_query(
    lat_cell: int, lng_cell: int, filters: BrowseFilters, size: int = 1
) -> tuple[str, list[Any]]:
    """Build the tile SQL + params. Bounds are params $1..$4; filters
    continue from $5."""
    where_frag, filter_params = filters.where_sql(start_index=5)
    params: list[Any] = [lat_cell, lat_cell + size, lng_cell, lng_cell + size]
    params.extend(filter_params)
    if places_source.is_cis():
        # AA-712 slice 6b: CIS catalog views (228). Same filter columns on the component view; the
        # view hides components on suspect / duplicate destinations and of tours that are not live.
        sql = f"""
        SELECT d.destination_id AS id, d.name, d.lat, d.lng, d.located_by, d.precision,
               COUNT(c.component_id) AS component_count
        FROM catalog.v_destination d
        JOIN catalog.v_tour_component c ON c.destination_id = d.destination_id
        WHERE d.lat >= $1 AND d.lat < $2
          AND d.lng >= $3 AND d.lng < $4
          {where_frag}
        GROUP BY d.destination_id, d.name, d.lat, d.lng, d.located_by, d.precision
        HAVING COUNT(c.component_id) > 0
        ORDER BY component_count DESC
    """
        return sql, params
    sql = f"""
        SELECT d.id, d.name, d.lat, d.lng, COUNT(c.id) AS component_count
        FROM shared.destinations d
        JOIN tripplanner.itinerary_components c ON c.destination_id = d.id
        WHERE d.lat >= $1 AND d.lat < $2
          AND d.lng >= $3 AND d.lng < $4
          {where_frag}
        GROUP BY d.id, d.name, d.lat, d.lng
        HAVING COUNT(c.id) > 0
        ORDER BY component_count DESC
    """
    return sql, params


async def query_tile(tile_id: str, filters: BrowseFilters, *, pool: _Pool) -> dict:
    lat_cell, lng_cell, size = parse_tile_id(tile_id)
    sql, params = build_tile_query(lat_cell, lng_cell, filters, size)
    rows = await pool.fetch(sql, *params)
    return {
        "destinations": [
            {
                "id": str(r["id"]),
                "name": r["name"],
                "lat": r["lat"],
                "lng": r["lng"],
                "component_count": r["component_count"],
                **geo_fields(r),
            }
            for r in rows
        ]
    }


def geo_fields(r: Any) -> dict:
    """``located_by`` / ``precision`` of a cis-path destination row (catalog.v_destination, 228): the map
    draws ``precision = 'area'`` as an area and shows the Geoapify + OSM attribution for ``geoapify``
    points. Empty on the legacy path (shared.destinations rows are not selected with them)."""
    keys = r.keys() if hasattr(r, "keys") else ()
    return {k: r[k] for k in ("located_by", "precision") if k in keys}
