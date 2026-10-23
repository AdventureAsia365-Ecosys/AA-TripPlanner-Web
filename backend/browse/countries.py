"""Country list for the Map/Browse Lambda (Lambda A).

Powers the country-first filter (product requirement: filter by COUNTRY,
then by activity). Returns only countries that actually have at least one
catalog component, with a per-country component count, so the UI never
shows an empty country.

Cacheable behind CloudFront (fixed TTL) — no per-visitor state. The result
is small and stable, so the frontend can populate a dropdown from it.

Reads the CIS catalog views (AA-712 slice 6b; the only source since AA-712 X4).
"""
from __future__ import annotations

from typing import Any, Protocol

from backend.browse.tiles import LINE_COLS_SQL, geo_fields


class _Pool(Protocol):
    async def fetch(self, query: str, *args: Any) -> Any: ...


COUNTRIES_SQL = """
    SELECT d.country AS country, COUNT(c.component_id) AS component_count
    FROM catalog.v_destination d
    JOIN catalog.v_tour_component c ON c.destination_id = d.destination_id
    WHERE d.country IS NOT NULL AND d.country <> ''
    GROUP BY d.country
    HAVING COUNT(c.component_id) > 0
    ORDER BY d.country ASC
"""


async def list_countries(*, pool: _Pool) -> dict:
    rows = await pool.fetch(COUNTRIES_SQL)
    return {
        "countries": [
            {"country": r["country"], "component_count": r["component_count"]}
            for r in rows
        ]
    }


# All destinations in a country (no map-bounds filter), so the UI can fit the
# map to a country when the user picks one from the country-first filter.
COUNTRY_DESTINATIONS_SQL = f"""
    SELECT d.destination_id AS id, d.name, d.lat, d.lng, d.located_by, d.precision, {LINE_COLS_SQL},
           COUNT(c.component_id) AS component_count
    FROM catalog.v_destination d
    JOIN catalog.v_tour_component c ON c.destination_id = d.destination_id
    WHERE d.country = $1
    GROUP BY d.destination_id, d.name, d.lat, d.lng, d.located_by, d.precision, {LINE_COLS_SQL}
    HAVING COUNT(c.component_id) > 0
    ORDER BY component_count DESC
"""


async def destinations_in_country(country: str, *, pool: _Pool) -> dict:
    rows = await pool.fetch(COUNTRY_DESTINATIONS_SQL, country)
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
