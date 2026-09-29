"""AA-675 follow-up — where a place is: the model proposes coordinates, Mapbox confirms.

Mapbox forward geocoding by name alone is unreliable for Asian place names, even limited to the
tour's country (29/09/2026, Dev): "Chiang Mai" -> a village in Roi Et, "Delhi" -> a village in
Madhya Pradesh, "Yala" -> "Yalabowa", nothing for Arugam Bay, Laya or Railay Beach, and relevance
scores do not separate right from wrong. The model knows these places. So:

  1. one tp_extract call per batch (~40 names) returns [lat, lng, country] per name, or null when
     it does not know the place (it must not guess);
  2. Mapbox searches the name *around that point* (proximity); a result within CONFIRM_KM of the
     model's point is taken (source "mapbox"), otherwise the model's point is kept ("llm");
  3. a country outside the AA countries, or coordinates out of range, is rejected.

The result is upserted into shared.destinations by lower(name), overwriting stale coordinates.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from backend.extraction.geocode import COUNTRY_ISO, ISO_COUNTRY, _feature_iso

CONFIRM_KM = 25.0
BATCH = 40

SYSTEM_PROMPT = (
    "You are a geographer for Adventure Asia. For each place named on a tour itinerary you give "
    "its coordinates and country. You answer only for places you actually know; for anything "
    "you are unsure of you return null. You never invent a location."
)


@dataclass(frozen=True)
class Located:
    lat: float
    lng: float
    country: str
    source: str  # mapbox | llm


def build_prompt(items: list[tuple[str, str, str]]) -> str:
    """items: (place name, tour country, tour name)."""
    lines = "\n".join(f"- {name} | tour country: {country} | tour: {tour}" for name, country, tour in items)
    return f"""Places (name | the tour's country | the tour's name, for context):
{lines}

Return ONLY a JSON object mapping each place name, copied exactly, to [latitude, longitude, country]
or null. Country is where the place really is (a Thailand tour may sleep in Siem Reap, Cambodia).
Use the town, village, park or landmark itself; for a hotel, lodge or camp, the town it is in.
Example: {{"Paro": [27.43, 89.41, "Bhutan"], "Unknown Lodge": null}}"""


def _norm_country(c: object) -> Optional[str]:
    if not isinstance(c, str):
        return None
    iso = COUNTRY_ISO.get(c.strip().lower())
    return ISO_COUNTRY.get(iso) if iso else None


def parse_answer(raw: str, names: list[str]) -> dict[str, Optional[tuple[float, float, str]]]:
    """Model JSON -> {name: (lat, lng, country) | None}; anything malformed becomes None."""
    try:
        start, end = raw.index("{"), raw.rindex("}") + 1
        data = json.loads(raw[start:end])
    except (ValueError, json.JSONDecodeError):
        return {n: None for n in names}
    lower = {str(k).strip().lower(): v for k, v in data.items()} if isinstance(data, dict) else {}
    out: dict[str, Optional[tuple[float, float, str]]] = {}
    for n in names:
        v = lower.get(n.lower())
        try:
            lat, lng, country = float(v[0]), float(v[1]), _norm_country(v[2])
        except (TypeError, ValueError, IndexError):
            out[n] = None
            continue
        ok = country and -90 <= lat <= 90 and -180 <= lng <= 180 and not (lat == 0 and lng == 0)
        out[n] = (lat, lng, country) if ok else None
    return out


def km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2)
    return 2 * 6371.0 * math.asin(math.sqrt(a))


GenerateFn = Callable[[str, str], Awaitable[str]]
SearchFn = Callable[[str, float, float], Awaitable[list[dict]]]  # (name, lat, lng) -> Mapbox features


def confirm(features: list[dict], lat: float, lng: float) -> Optional[Located]:
    """The first Mapbox feature within CONFIRM_KM of the model's point, if any."""
    for f in features:
        c = f.get("center") or []
        if len(c) == 2 and km(lat, lng, float(c[1]), float(c[0])) <= CONFIRM_KM:
            country = ISO_COUNTRY.get(_feature_iso(f), "")
            return Located(float(c[1]), float(c[0]), country, "mapbox") if country else None
    return None


async def locate_many(items: list[tuple[str, str, str]], generate: GenerateFn,
                      search: Optional[SearchFn]) -> dict[str, Optional[Located]]:
    """Resolves (name, tour country, tour name) items in batches of BATCH names per model call."""
    out: dict[str, Optional[Located]] = {}
    for i in range(0, len(items), BATCH):
        batch = items[i:i + BATCH]
        names = [n for n, _, _ in batch]
        try:
            answer = parse_answer(await generate(SYSTEM_PROMPT, build_prompt(batch)), names)
        except Exception as e:  # noqa: BLE001 — a failed batch leaves its places unlinked
            print(f"[locate] model call failed: {str(e)[:120]}")
            answer = {n: None for n in names}
        for n in names:
            point = answer[n]
            if point is None:
                out[n] = None
                continue
            lat, lng, country = point
            confirmed = None
            if search is not None:
                try:
                    confirmed = confirm(await search(n, lat, lng), lat, lng)
                except Exception as e:  # noqa: BLE001 — Mapbox is only a refinement
                    print(f"[locate] mapbox failed for {n!r}: {str(e)[:120]}")
            out[n] = confirmed or Located(lat, lng, country, "llm")
    return out


UPSERT_SQL = """
    INSERT INTO shared.destinations (name, country, lat, lng) VALUES ($1, $2, $3, $4)
    ON CONFLICT (lower(name)) DO UPDATE SET country = EXCLUDED.country, lat = EXCLUDED.lat, lng = EXCLUDED.lng
    RETURNING id
"""


async def upsert(db, name: str, loc: Located):
    return await db.fetchval(UPSERT_SQL, name, loc.country, loc.lat, loc.lng)
