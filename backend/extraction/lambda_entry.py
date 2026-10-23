"""AA-675 — the extraction entry point inside the assembly Lambda (direct invoke only).

Reached only by a direct, IAM-authorised invoke of the assembly function with the payload
``{"extraction": {"op": "<op>"}}`` (assembly/handler.py; API Gateway events never take this branch).

Since AA-712 slice 6a (ADR 0007) the place pipeline runs in CIS (job kinds place_extract /
place_geocode / place_graph, schema geo). This repo no longer extracts, geocodes or builds the Tour
Graph. The read-only checks that were left over the frozen tables (list, orphans, neighbours) went
with those tables (AA-712 X4, tp-018).

Every op — the former writer ops (tour_days, components, relink, tour_graph, prune) and the former
checks — answers status 410 with "moved_to", before any DB connection is opened; anything else 400.
"""
from __future__ import annotations

OPS: list[str] = []

# Kept by name so an old operator script gets a clear 410 that says where the work moved, not a generic
# unknown op. Writers removed in AA-712 slice 6a; checks over the frozen tables removed in AA-712 X4.
REMOVED_OPS = ["tour_days", "components", "relink", "tour_graph", "prune", "list", "orphans", "neighbours"]
MOVED_TO = ("CIS place pipeline (ADR 0007, AA-712): place_extract / place_geocode / place_graph jobs; "
            "content checks on /admin/places")


def _unknown(op) -> dict:
    out = {"status": 410 if op in REMOVED_OPS else 400,
           "error": f"unknown extraction op {op!r}", "ops": OPS}
    if op in REMOVED_OPS:
        out["removed"] = True
        out["moved_to"] = MOVED_TO
    return out


async def run_extraction_event(spec: dict) -> dict:
    return _unknown((spec or {}).get("op"))
