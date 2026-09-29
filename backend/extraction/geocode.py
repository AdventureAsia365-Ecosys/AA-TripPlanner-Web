"""Mapbox forward geocoding with a durable cache in shared.destinations.

Each place name is geocoded at most once: we look it up case-insensitively
in shared.destinations first (unique index on lower(name)), and only call
the Mapbox Geocoding API on a miss. The result is inserted with a
normalized country (country_normalize.py).

All I/O is injectable (db pool + http client) so unit tests never make a
live HTTP call or need a database.
"""
from __future__ import annotations

import os
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


_token_cache: dict[str, str] = {}


def _mapbox_token() -> str:
    """The Mapbox token: MAPBOX_GEOCODING_TOKEN locally; in the assembly Lambda (AA-675) the
    Secrets Manager secret named by MAPBOX_GEOCODING_TOKEN_ARN, read once per container."""
    if config.MAPBOX_GEOCODING_TOKEN:
        return config.MAPBOX_GEOCODING_TOKEN
    arn = os.environ.get("MAPBOX_GEOCODING_TOKEN_ARN")
    if not arn:
        return ""
    if arn not in _token_cache:
        import boto3  # lazy: tests and local runs never hit this branch

        value = boto3.client("secretsmanager", region_name=config.BEDROCK_REGION).get_secret_value(
            SecretId=arn).get("SecretString") or ""
        _token_cache[arn] = "" if value == "REPLACE_ME" else value
    return _token_cache[arn]


# ISO 3166-1 alpha-2 of the countries AA sells (Mapbox `country` filter). Tibet is geocoded in China.
COUNTRY_ISO = {
    "bhutan": "bt", "cambodia": "kh", "china": "cn", "india": "in", "japan": "jp", "laos": "la",
    "mongolia": "mn", "myanmar": "mm", "nepal": "np", "south korea": "kr", "sri lanka": "lk",
    "taiwan": "tw", "thailand": "th", "tibet": "cn", "vietnam": "vn",
}
ISO_COUNTRY = {iso: name.title() for name, iso in COUNTRY_ISO.items() if name != "tibet"}
ISO_COUNTRY.update({"kr": "South Korea", "lk": "Sri Lanka"})
# Every search is limited to these: a multi-country tour can sleep in a neighbour (Siem Reap on a
# Thailand tour), but never on another continent.
AA_ISOS = ",".join(sorted(set(COUNTRY_ISO.values())))
# No "region": a hotel or valley that only matches a province would land on the province centroid
# (29/09/2026: seven Korean hotels shared one point in the Philippines).
_TYPES = "place,locality,district,poi"


def country_iso(country: str) -> str:
    return COUNTRY_ISO.get((country or "").strip().lower(), "")


def _feature_iso(feature: dict) -> str:
    if "country" in (feature.get("place_type") or []):
        return (feature.get("properties", {}).get("short_code") or "").lower()
    for ctx in feature.get("context") or []:
        if str(ctx.get("id", "")).startswith("country."):
            return (ctx.get("short_code") or "").lower()
    return ""


def pick_feature(features: list[dict], iso: str) -> Optional[dict]:
    """The best match, preferring the tour's own country among equally relevant results."""
    if not features:
        return None
    top = max(f.get("relevance", 0) for f in features)
    best = [f for f in features if f.get("relevance", 0) >= top - 1e-9]
    return next((f for f in best if iso and _feature_iso(f) == iso), best[0])


async def _mapbox_forward(
    http: httpx.AsyncClient, name: str, country: str, region: str = ""
) -> tuple[float, float, str]:
    """Return (lat, lng, country) for a place name. Raises GeocodeError on no match.

    One search limited to the AA countries (Mapbox `country` filter), never the whole world where
    a same-named place on another continent would win; among the equally relevant results the
    tour's own country wins. The returned country is where Mapbox put the place (Siem Reap ->
    Cambodia even on a Thailand tour). `region` (free text, older callers) is used only when
    `country` has no ISO code.
    """
    token = _mapbox_token()
    if not token:
        raise GeocodeError("MAPBOX_GEOCODING_TOKEN is not set.")
    from urllib.parse import quote

    iso = country_iso(country) or country_iso(region)
    url = f"{config.MAPBOX_GEOCODING_URL}/{quote(name)}.json"
    params = {"access_token": token, "limit": "5", "types": _TYPES, "country": AA_ISOS}
    resp = await http.get(url, params=params)
    resp.raise_for_status()
    feature = pick_feature(resp.json().get("features") or [], iso)
    if feature is None:
        raise GeocodeError(f"No geocoding result for {name!r} in the AA countries")
    center = feature.get("center")  # [lng, lat]
    if not center or len(center) != 2:
        raise GeocodeError(f"Malformed geocoding result for {name!r}")
    found = ISO_COUNTRY.get(_feature_iso(feature), "")
    return float(center[1]), float(center[0]), found or country


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
    lat, lng, found_country = await _mapbox_forward(http, name, normalized_country, region)

    row = await pool.fetchrow(
        "INSERT INTO shared.destinations (name, country, lat, lng) "
        "VALUES ($1, $2, $3, $4) "
        "ON CONFLICT (lower(name)) DO UPDATE SET name = shared.destinations.name "
        "RETURNING id, name, country, lat, lng",
        name,
        found_country or normalized_country,
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
