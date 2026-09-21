"""Deterministic geography-based day sequencing (nearest-neighbor).

Greedy nearest-neighbor ordering by destination lat/lng: start from a
stable anchor, then repeatedly append the nearest not-yet-placed
component. This is a straight-line-distance heuristic, not real routing
(acceptable per requirements §7). No LLM involved.

Determinism: for a given input set the output order is always the same —
the anchor is the west-most-then-south-most component, and ties in
distance are broken by component id. This matters because the same trip
must re-sequence identically on every add/remove.
"""
from __future__ import annotations

import math
from typing import Any


def _haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in km. Used only for relative ordering."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _anchor_key(c: dict) -> tuple[float, float, str]:
    # west-most (min lng), then south-most (min lat), then id for stability
    return (c["lng"], c["lat"], str(c["id"]))


def sequence(components: list[dict]) -> list[dict]:
    """Return components in nearest-neighbor visiting order.

    Each component dict must have: id, lat, lng (plus any other fields,
    which are preserved). Returns a new list; input is not mutated.
    """
    if len(components) <= 1:
        return list(components)

    remaining = list(components)
    start = min(remaining, key=_anchor_key)
    remaining.remove(start)
    ordered = [start]

    while remaining:
        last = ordered[-1]
        nxt = min(
            remaining,
            key=lambda c: (
                _haversine(last["lat"], last["lng"], c["lat"], c["lng"]),
                str(c["id"]),
            ),
        )
        remaining.remove(nxt)
        ordered.append(nxt)

    return ordered


def group_into_days(ordered: list[dict]) -> list[dict]:
    """Assign a 1-based day index to each component in visiting order.

    MVP rule: one component per day, in sequence. duration_hint could later
    pack multiple short components per day, but per scope we keep it simple
    and deterministic. Returns [{day, component_id, name, ...}].
    """
    out: list[dict] = []
    for i, c in enumerate(ordered, start=1):
        entry = dict(c)
        entry["day"] = i
        entry["component_id"] = str(c["id"])
        out.append(entry)
    return out


def sequence_and_group(components: list[dict]) -> list[dict]:
    return group_into_days(sequence(components))
