"""AA-675 — the extraction entry point inside the assembly Lambda (direct invoke only).

    aws lambda invoke --function-name aa-tripplanner-dev-assembly \
        --payload '{"extraction": {"op": "orphans"}}' out.json

Since AA-712 slice 6a (ADR 0007) the place pipeline runs in CIS (job kinds place_extract /
place_geocode / place_graph, schema geo). This repo no longer extracts, geocodes or builds the Tour
Graph, so only read-only checks over the frozen tables are left:

  list       {} — active tours and whether each one's itinerary changed since tour_day was built.
  orphans    {} — AA-731: components / tour_day rows of tours no longer active.
  neighbours {"destination_id": "<uuid>", "limit": 20} — AA-673 sample query, with its latency.

The former writer ops (tour_days, components, relink, tour_graph, prune) answer status 410 with
an "unknown extraction op" error, before any DB connection is opened.
"""
from __future__ import annotations

import time

from backend.extraction import checks

OPS = ["list", "orphans", "neighbours"]

# Writer ops removed in AA-712 slice 6a. Kept by name so an old operator script gets a clear 410
# that says where the work moved, not a generic unknown op.
REMOVED_OPS = ["tour_days", "components", "relink", "tour_graph", "prune"]
MOVED_TO = "CIS place pipeline (ADR 0007, AA-712): place_extract / place_geocode / place_graph jobs"


def _unknown(op) -> dict:
    out = {"status": 410 if op in REMOVED_OPS else 400,
           "error": f"unknown extraction op {op!r}", "ops": OPS}
    if op in REMOVED_OPS:
        out["removed"] = True
        out["moved_to"] = MOVED_TO
    return out


async def run_extraction_event(spec: dict) -> dict:
    op = (spec or {}).get("op")
    if op not in OPS:
        return _unknown(op)

    from backend.shared.db import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        if op == "orphans":
            return {"op": op, **await checks.orphans(conn)}
        if op == "list":
            return {"op": op, "tours": await checks.list_tours(conn)}
        t0 = time.perf_counter()
        rows = await checks.neighbours(conn, spec["destination_id"], int(spec.get("limit") or 20))
        ms = round((time.perf_counter() - t0) * 1000, 1)
    return {"op": op, "query_ms": ms, "neighbours": checks.jsonable(rows)}
