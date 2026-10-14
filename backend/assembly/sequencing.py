"""Deterministic geography-based day sequencing (nearest-neighbor over tour blocks).

Components from the same AA source tour form one block, kept in the tour's own day order
(source_day_index, then id) — an AA tour is sold in that order, and the hand-off legs
(handoff.leg_chain) depend on it (AA-805: S235 sent one tour as days 3-4, 7, 2, 1). A component
without a source tour is a block of one.

Blocks are then ordered greedily by nearest neighbour on destination lat/lng: start from a
stable anchor block, then repeatedly append the block whose first stop is nearest to the last
stop of the previous block. This is a straight-line-distance heuristic, not real routing
(acceptable per requirements §7). No LLM involved. A traveller's manual reorder bypasses this
module entirely (events._project_itinerary).

Determinism: for a given input set the output order is always the same — the anchor is the
block whose first stop is west-most-then-south-most, and ties in distance are broken by the
first stop's id. This matters because the same trip must re-sequence identically on every
add/remove.
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


def _blocks(components: list[dict]) -> list[list[dict]]:
    """Group components by source tour; each tour block is in its own day order."""
    by_tour: dict[str, list[dict]] = {}
    singles: list[list[dict]] = []
    for c in components:
        tour = c.get("source_tour_id")
        if tour is None or tour == "":
            singles.append([c])
        else:
            by_tour.setdefault(str(tour), []).append(c)
    blocks = [
        sorted(members, key=lambda c: (c.get("source_day_index") is None,
                                       c.get("source_day_index") or 0, str(c["id"])))
        for members in by_tour.values()
    ]
    return blocks + singles


def sequence(components: list[dict]) -> list[dict]:
    """Return components in visiting order: tour blocks in day order, blocks by nearest neighbour.

    Each component dict must have: id, lat, lng; source_tour_id / source_day_index are used when
    present. Other fields are preserved. Returns a new list; input is not mutated.
    """
    if len(components) <= 1:
        return list(components)

    remaining = _blocks(components)
    start = min(remaining, key=lambda b: _anchor_key(b[0]))
    remaining.remove(start)
    ordered = list(start)

    while remaining:
        last = ordered[-1]
        nxt = min(
            remaining,
            key=lambda b: (
                _haversine(last["lat"], last["lng"], b[0]["lat"], b[0]["lng"]),
                str(b[0]["id"]),
            ),
        )
        remaining.remove(nxt)
        ordered.extend(nxt)

    return ordered


def group_into_days(ordered: list[dict]) -> list[dict]:
    """Assign a 1-based day index to each component in visiting order.

    One component per day, except that consecutive components from the same AA tour day
    (same source_tour_id and source_day_index) share that day. A route started from an AA
    tour therefore keeps its real day structure: several activities on one day (AA-674).
    Returns [{day, component_id, name, ...}].
    """
    out: list[dict] = []
    day = 0
    prev_key = None
    for c in ordered:
        key = (c.get("source_tour_id"), c.get("source_day_index"))
        if not (key[0] is not None and key[1] is not None and key == prev_key):
            day += 1
        prev_key = key
        entry = dict(c)
        entry["day"] = day
        entry["component_id"] = str(c["id"])
        out.append(entry)
    return out


def sequence_and_group(components: list[dict]) -> list[dict]:
    return group_into_days(sequence(components))
