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

from collections import Counter
from typing import Any, Optional, Protocol

from backend.browse.catalog import HIDDEN_LIFECYCLE
from backend.browse.tiles import LINE_COLS_SQL, geo_fields


class _Pool(Protocol):
    async def fetch(self, query: str, *args: Any) -> Any: ...


# AA-1130 (S283 audit T1): each country also says how many live catalogue tours it has (catalog.v_tour, the same
# liveness as the home page's country list, catalog.COUNTRIES_SQL; matched on the lower-cased name like
# catalog.COUNTRY_COVERS_SQL). The route finder's "Heading to" lists only countries with a tour (a country with
# experiences but no tour of its own, e.g. a stop of a cross-border tour, proposes nothing); the map's country filter
# keeps every country with experiences.
COUNTRIES_SQL = f"""
    WITH tours AS (
        SELECT lower(t.country) AS country_key, count(*) AS tour_count
        FROM catalog.v_tour t
        WHERE t.country IS NOT NULL AND t.country <> ''
          AND t.lifecycle_stage IS DISTINCT FROM '{HIDDEN_LIFECYCLE}'
        GROUP BY lower(t.country)
    )
    SELECT d.country AS country, COUNT(c.component_id) AS component_count,
           COALESCE(max(tr.tour_count), 0) AS tour_count
    FROM catalog.v_destination d
    JOIN catalog.v_tour_component c ON c.destination_id = d.destination_id
    LEFT JOIN tours tr ON tr.country_key = lower(d.country)
    WHERE d.country IS NOT NULL AND d.country <> ''
    GROUP BY d.country
    HAVING COUNT(c.component_id) > 0
    ORDER BY d.country ASC
"""


async def list_countries(*, pool: _Pool, scope: Optional[dict[str, dict]] = None) -> dict:
    """Countries with experiences, each with its component and tour counts.

    AA-1130: `scope` (tenant_tours.served_scope) narrows the list to the countries of the tours an agency sells, with
    the agency's tour count; None = every country (AA's planner, the AA-hosted demo tenant). An empty scope reads
    nothing."""
    if scope is not None and not scope:
        return {"countries": []}
    rows = await pool.fetch(COUNTRIES_SQL)
    out = [{"country": r["country"], "component_count": r["component_count"], "tour_count": r["tour_count"]}
           for r in rows]
    if scope is not None:
        sold = Counter((t.get("country") or "").lower() for t in scope.values() if t.get("country"))
        out = [{**c, "tour_count": sold[c["country"].lower()]} for c in out if sold[c["country"].lower()]]
    return {"countries": out}


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
