"""AA-675 — the extraction entry point inside the assembly Lambda (direct invoke only).

    aws lambda invoke --function-name aa-tripplanner-dev-assembly \
        --payload '{"extraction": {"op": "tour_days", "tour_ids": ["<uuid>"]}}' out.json

ops:
  tour_days  {"tour_ids": [...] | omitted = every active tour, "force": false}
             One tour per invoke is the safe size for the 60 s timeout (one model call + a few
             geocodes); the operator script loops over tours.
  list       {} — active tours and whether each one's itinerary changed since its last extraction.
  prune      {"include_components": false} — delete rows of tours that are no longer active.

The assembly Lambda has the Bedrock satellite role (tp_extract, logged to shared.llm_call_log via
the Model Gateway) and reads the Mapbox token from Secrets Manager (MAPBOX_GEOCODING_TOKEN_ARN).
"""
from __future__ import annotations

import time
from typing import Optional

import httpx

from backend.extraction import tour_days
from backend.extraction.geocode import geocode_place
from backend.shared import llm_gateway


async def run_extraction_event(spec: dict) -> dict:
    from backend.shared.db import get_pool

    started = time.monotonic()
    op = (spec or {}).get("op")
    pool = await get_pool()
    if op == "prune":
        async with pool.acquire() as conn:
            return {"op": op, **await tour_days.prune(conn, bool(spec.get("include_components")))}
    if op == "list":
        async with pool.acquire() as conn:
            return {"op": op, "tours": await tour_days.list_tours(conn)}
    if op != "tour_days":
        return {"error": f"unknown extraction op {op!r}", "ops": ["list", "tour_days", "prune"]}

    tour_ids: Optional[list[str]] = spec.get("tour_ids") or None
    async with httpx.AsyncClient(timeout=15) as http, pool.acquire() as conn:
        route = await llm_gateway.load_route(conn, "tp_extract")

        async def generate(system: str, user: str) -> str:
            text, call = llm_gateway.generate_text(route, system, user, 4000)
            await llm_gateway.record(conn, call, {"op": "tour_days", "tour_ids": tour_ids,
                                                  "output_len_chars": len(text)})
            return text

        async def geocode(place: str, country: str) -> Optional[str]:
            dest = await geocode_place(place, country, pool=conn, http=http, region=country)
            return dest.id

        result = await tour_days.run_tours(conn, tour_ids, generate, geocode,
                                           force=bool(spec.get("force")))
    return {"op": op, "seconds": round(time.monotonic() - started, 1), **result}
