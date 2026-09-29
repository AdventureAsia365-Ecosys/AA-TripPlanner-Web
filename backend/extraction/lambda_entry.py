"""AA-675 — the extraction entry point inside the assembly Lambda (direct invoke only).

    aws lambda invoke --function-name aa-tripplanner-dev-assembly \
        --payload '{"extraction": {"op": "tour_days", "tour_ids": ["<uuid>"]}}' out.json

ops:
  tour_days  {"tour_ids": [...] | omitted = every active tour, "force": false}
             One tour per invoke is the safe size for the 60 s timeout (one model call + a few
             geocodes); the operator script loops over tours.
  list       {} — active tours and whether each one's itinerary changed since its last extraction.
  prune      {"include_components": false} — delete rows of tours that are no longer active.
  regeocode  {"since": "2026-09-29", "after": "", "limit": 60} — re-geocode overnight destinations
             created since a date with the current rules; page with the returned "last".
  tour_graph {"junction_km": 150} — AA-673: rebuild the Tour Graph from tour_day (no model call).
  neighbours {"destination_id": "<uuid>", "limit": 20} — AA-673 sample query, with its latency.

The assembly Lambda has the Bedrock satellite role (tp_extract, logged to shared.llm_call_log via
the Model Gateway) and reads the Mapbox token from Secrets Manager (MAPBOX_GEOCODING_TOKEN_ARN).
"""
from __future__ import annotations

import time
from typing import Optional

import httpx

from backend.extraction import tour_days, tour_graph
from backend.extraction.geocode import _mapbox_forward, geocode_place
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
    if op == "regeocode":
        async with httpx.AsyncClient(timeout=15) as http, pool.acquire() as conn:
            async def forward(name: str, country: str):
                return await _mapbox_forward(http, name, country)

            return {"op": op, **await tour_days.regeocode(
                conn, forward, spec.get("since") or time.strftime("%Y-%m-%d"),
                spec.get("after") or "", int(spec.get("limit") or 60))}
    if op == "tour_graph":
        async with pool.acquire() as conn:
            stats = await tour_graph.rebuild(conn, float(spec.get("junction_km") or tour_graph.DEFAULT_JUNCTION_KM))
        return {"op": op, "seconds": round(time.monotonic() - started, 1), "stats": stats}
    if op == "neighbours":
        async with pool.acquire() as conn:
            t0 = time.perf_counter()
            rows = await tour_graph.neighbours(conn, spec["destination_id"], int(spec.get("limit") or 20))
            ms = round((time.perf_counter() - t0) * 1000, 1)
        return {"op": op, "query_ms": ms, "neighbours": tour_graph.jsonable(rows)}
    if op != "tour_days":
        return {"error": f"unknown extraction op {op!r}",
                "ops": ["list", "tour_days", "prune", "regeocode", "tour_graph", "neighbours"]}

    tour_ids: Optional[list[str]] = spec.get("tour_ids") or None
    async with httpx.AsyncClient(timeout=15) as http, pool.acquire() as conn:
        route = await llm_gateway.load_route(conn, "tp_extract")

        async def generate(system: str, user: str) -> str:
            text, call = llm_gateway.generate_text(route, system, user, 4000)
            await llm_gateway.record(conn, call, {"op": "tour_days", "tour_ids": tour_ids,
                                                  "output_len_chars": len(text)})
            return text

        async def geocode(place: str, country: str) -> Optional[str]:
            dest = await geocode_place(place, country, pool=conn, http=http)
            return dest.id

        result = await tour_days.run_tours(conn, tour_ids, generate, geocode,
                                           force=bool(spec.get("force")))
    return {"op": op, "seconds": round(time.monotonic() - started, 1), **result}
