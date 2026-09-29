"""AA-675 — the extraction entry point inside the assembly Lambda (direct invoke only).

    aws lambda invoke --function-name aa-tripplanner-dev-assembly \
        --payload '{"extraction": {"op": "tour_days", "tour_ids": ["<uuid>"]}}' out.json

ops:
  tour_days  {"tour_ids": [...] | omitted = every active tour, "force": false}
             One tour per invoke is the safe size for the 60 s timeout (one extraction call, one
             locate call for new places); the operator script loops over tours.
  list       {} — active tours and whether each one's itinerary changed since its last extraction.
  prune      {"include_components": false} — delete rows of tours that are no longer active.
  relink     {"after": "", "limit": 40} — re-locate every night anchor of the active tours from
             scratch (model + Mapbox, locate.py) and relink tour_day; page with the returned "last".
  tour_graph {"junction_km": 150} — AA-673: rebuild the Tour Graph from tour_day (no model call).
  neighbours {"destination_id": "<uuid>", "limit": 20} — AA-673 sample query, with its latency.

The assembly Lambda has the Bedrock satellite role (tp_extract, logged to shared.llm_call_log via
the Model Gateway) and reads the Mapbox token from Secrets Manager (MAPBOX_GEOCODING_TOKEN_ARN).
"""
from __future__ import annotations

import time
from typing import Optional

import httpx

from backend import config
from backend.extraction import locate, tour_days, tour_graph
from backend.extraction.geocode import AA_ISOS, _TYPES, _mapbox_token
from backend.shared import llm_gateway

OPS = ["list", "tour_days", "prune", "relink", "tour_graph", "neighbours"]


def _generator(conn, route, op: str):
    async def generate(system: str, user: str) -> str:
        text, call = llm_gateway.generate_text(route, system, user, 4000)
        await llm_gateway.record(conn, call, {"op": op, "output_len_chars": len(text)})
        return text
    return generate


def _searcher(http: httpx.AsyncClient):
    """Mapbox search for a name around the model's point, limited to the AA countries."""
    async def search(name: str, lat: float, lng: float) -> list[dict]:
        token = _mapbox_token()
        if not token:
            return []
        from urllib.parse import quote
        resp = await http.get(f"{config.MAPBOX_GEOCODING_URL}/{quote(name)}.json", params={
            "access_token": token, "limit": "5", "types": _TYPES, "country": AA_ISOS,
            "autocomplete": "false", "language": "en", "proximity": f"{lng},{lat}"})
        resp.raise_for_status()
        return resp.json().get("features") or []
    return search


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
    if op == "relink":
        async with httpx.AsyncClient(timeout=15) as http, pool.acquire() as conn:
            route = await llm_gateway.load_route(conn, "tp_extract")
            result = await tour_days.relink(conn, _generator(conn, route, "relink"), _searcher(http),
                                            spec.get("after") or "", int(spec.get("limit") or locate.BATCH))
        return {"op": op, "seconds": round(time.monotonic() - started, 1), **result}
    if op != "tour_days":
        return {"error": f"unknown extraction op {op!r}", "ops": OPS}

    tour_ids: Optional[list[str]] = spec.get("tour_ids") or None
    async with httpx.AsyncClient(timeout=15) as http, pool.acquire() as conn:
        route = await llm_gateway.load_route(conn, "tp_extract")
        generate = _generator(conn, route, "tour_days")
        search = _searcher(http)

        async def link(places: list[str], country: str, tour_name: str) -> dict:
            """Known places reuse their destination row; new ones are located in one call."""
            rows = await conn.fetch("SELECT id, lower(name) AS key FROM shared.destinations "
                                    "WHERE lower(name) = ANY($1::text[])", [p.lower() for p in places])
            known = {r["key"]: str(r["id"]) for r in rows}
            out = {p: known[p.lower()] for p in places if p.lower() in known}
            new = [p for p in places if p.lower() not in known]
            found = await locate.locate_many([(p, country, tour_name) for p in new], generate, search)
            for p in new:
                if found.get(p):
                    out[p] = str(await locate.upsert(conn, p, found[p]))
            return out

        result = await tour_days.run_tours(conn, tour_ids, generate, link, force=bool(spec.get("force")))
    return {"op": op, "seconds": round(time.monotonic() - started, 1), **result}
