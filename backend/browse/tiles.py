"""Fixed 1-degree grid tile query for the Map/Browse Lambda (Lambda A).

A tile_id encodes the integer lat/lng cell as "<latCell>_<lngCell>"
(matches frontend/lib/mapbox.ts tileIdFor). A tile covers
[latCell, latCell+1) x [lngCell, lngCell+1). We return the destinations
that fall in the cell AND have at least one component matching the active
filters, with a per-destination component_count.

Cacheable behind CloudFront (fixed 30-min TTL) — this endpoint takes no
per-visitor state.
"""
from __future__ import annotations

from typing import Any, Protocol

from backend.browse.filters import BrowseFilters


class _Pool(Protocol):
    async def fetch(self, query: str, *args: Any) -> Any: ...


class TileError(ValueError):
    pass


def parse_tile_id(tile_id: str) -> tuple[int, int]:
    """Parse "<lat>_<lng>" into integer cell corners. Raises TileError."""
    parts = tile_id.split("_")
    if len(parts) != 2:
        raise TileError(f"Malformed tile_id: {tile_id!r}")
    try:
        lat_cell = int(parts[0])
        lng_cell = int(parts[1])
    except ValueError as e:
        raise TileError(f"Non-integer tile cell in {tile_id!r}") from e
    if not (-90 <= lat_cell <= 89) or not (-180 <= lng_cell <= 179):
        raise TileError(f"tile_id out of range: {tile_id!r}")
    return lat_cell, lng_cell


def build_tile_query(
    lat_cell: int, lng_cell: int, filters: BrowseFilters
) -> tuple[str, list[Any]]:
    """Build the tile SQL + params. Bounds are params $1..$4; filters
    continue from $5."""
    where_frag, filter_params = filters.where_sql(start_index=5)
    params: list[Any] = [lat_cell, lat_cell + 1, lng_cell, lng_cell + 1]
    params.extend(filter_params)
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
    lat_cell, lng_cell = parse_tile_id(tile_id)
    sql, params = build_tile_query(lat_cell, lng_cell, filters)
    rows = await pool.fetch(sql, *params)
    return {
        "destinations": [
            {
                "id": str(r["id"]),
                "name": r["name"],
                "lat": r["lat"],
                "lng": r["lng"],
                "component_count": r["component_count"],
            }
            for r in rows
        ]
    }
