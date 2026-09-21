"""Mapbox forward geocoding with a durable cache in shared.destinations.

Each place name is geocoded at most once: we look it up case-insensitively
in shared.destinations first (unique index on lower(name)), and only call
the Mapbox Geocoding API on a miss. The result is inserted with a
normalized country (country_normalize.py).

All I/O is injectable (db pool + http client) so unit tests never make a
live HTTP call or need a database.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Protocol

import httpx

from backend import config
from backend.extraction.country_normalize import normalize_country


class GeocodeError(RuntimeError):
    pass


@dataclass
class Destination:
    id: str
    name: str
    country: str
    lat: float
    lng: float


class _Pool(Protocol):
    async def fetchrow(self, query: str, *args: Any) -> Any: ...
    async def execute(self, query: str, *args: Any) -> Any: ...


async def _lookup_cached(pool: _Pool, name: str) -> Optional[Destination]:
    row = await pool.fetchrow(
        "SELECT id, name, country, lat, lng FROM shared.destinations "
        "WHERE lower(name) = lower($1)",
        name,
    )
    if row is None:
        return None
    return Destination(
        id=str(row["id"]),
        name=row["name"],
        country=row["country"],
        lat=row["lat"],
        lng=row["lng"],
    )


async def _mapbox_forward(
    http: httpx.AsyncClient, name: str, country: str, region: str = ""
) -> tuple[float, float]:
    """Return (lat, lng) for a place name. Raises GeocodeError on no match.

    `region` (e.g. the tour's country/area, derived from its name) is
    appended to the query text to disambiguate — without it Mapbox often
    returns a same-named place on the wrong continent.
    """
    if not config.MAPBOX_GEOCODING_TOKEN:
        raise GeocodeError("MAPBOX_GEOCODING_TOKEN is not set.")
    from urllib.parse import quote

    query = f"{name}, {region}" if region else name
    url = f"{config.MAPBOX_GEOCODING_URL}/{quote(query)}.json"
    params = {
        "access_token": config.MAPBOX_GEOCODING_TOKEN,
        "limit": "1",
        "types": "place,locality,region,poi",
    }
    resp = await http.get(url, params=params)
    resp.raise_for_status()
    data = resp.json()
    features = data.get("features") or []
    if not features:
        raise GeocodeError(f"No geocoding result for {name!r} ({country})")
    center = features[0].get("center")  # [lng, lat]
    if not center or len(center) != 2:
        raise GeocodeError(f"Malformed geocoding result for {name!r}")
    lng, lat = float(center[0]), float(center[1])
    return lat, lng


async def preload_cache(pool: _Pool) -> dict[str, Destination]:
    """Load all shared.destinations into an in-memory map keyed by
    lower(name). Lets a batch run avoid one DB SELECT per place over a
    high-latency tunnel. Optional — pass the result as `mem_cache`."""
    rows = await pool.fetch(  # type: ignore[attr-defined]
        "SELECT id, name, country, lat, lng FROM shared.destinations"
    )
    cache: dict[str, Destination] = {}
    for r in rows:
        cache[r["name"].lower()] = Destination(
            id=str(r["id"]), name=r["name"], country=r["country"],
            lat=r["lat"], lng=r["lng"],
        )
    return cache


async def geocode_place(
    name: str,
    country: str,
    *,
    pool: _Pool,
    http: httpx.AsyncClient,
    mem_cache: Optional[dict[str, Destination]] = None,
    region: str = "",
) -> Destination:
    """Resolve a place to a shared.destinations row, geocoding on cache miss.

    Idempotent: concurrent-safe via ON CONFLICT on the unique lower(name)
    index — if two runs race, the second reuses the first's row.

    If `mem_cache` is provided, it is consulted first (and updated on
    insert), avoiding a DB round-trip per already-known place.
    """
    key = name.lower()
    if mem_cache is not None and key in mem_cache:
        return mem_cache[key]

    if mem_cache is None:
        cached = await _lookup_cached(pool, name)
        if cached is not None:
            return cached

    normalized_country = normalize_country(country)
    lat, lng = await _mapbox_forward(http, name, normalized_country, region)

    row = await pool.fetchrow(
        "INSERT INTO shared.destinations (name, country, lat, lng) "
        "VALUES ($1, $2, $3, $4) "
        "ON CONFLICT (lower(name)) DO UPDATE SET name = shared.destinations.name "
        "RETURNING id, name, country, lat, lng",
        name,
        normalized_country,
        lat,
        lng,
    )
    dest = Destination(
        id=str(row["id"]),
        name=row["name"],
        country=row["country"],
        lat=row["lat"],
        lng=row["lng"],
    )
    if mem_cache is not None:
        mem_cache[key] = dest
    return dest
