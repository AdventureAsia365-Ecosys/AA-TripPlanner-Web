"""AA-674 — coverage check: can real Adventure Asia tours cover this trip, and how?

Every pinned place must sit on a chain of **legs** (consecutive days of one real tour), linked
by **transfers** between tours no longer than JUNCTION_KM (the Tour Graph's junction rule,
AA-673). The chain is what an advisor can actually sell as a Trip Case.

For the pins in trip order, each pin can be served by any (tour, day) whose components visit
its destination (itinerary_components). A small dynamic programme picks one option per pin,
minimising, in order:
  1. gaps: a hop between tours longer than JUNCTION_KM (the trip is not coverable there);
  2. transfers: changes of tour (fewer tours are easier to book);
  3. days: total days of the legs used.
Staying on the same tour on the same or a later day continues the current leg; anything else
starts a new leg. Deterministic, no model call.
"""
from __future__ import annotations

from typing import Any, Optional

from backend.assembly import events as events_mod
from backend.assembly.sequencing import _haversine  # noqa: PLC2701

JUNCTION_KM = 150.0

OPTIONS_SQL = """
    SELECT DISTINCT destination_id::text AS destination_id, source_tour_id, source_day_index
    FROM tripplanner.itinerary_components
    WHERE destination_id = ANY($1::uuid[])
"""

TOUR_NAMES_SQL = """
    SELECT tour_id::text AS tour_id, aa_name FROM gold_aa_internal.published_tours
    WHERE tour_id::text = ANY($1::text[])
"""


def _km(a: dict, b: dict) -> Optional[float]:
    if not all(isinstance(p.get(k), (int, float)) for p in (a, b) for k in ("lat", "lng")):
        return None
    return _haversine(float(a["lat"]), float(a["lng"]), float(b["lat"]), float(b["lng"]))


def solve(pins: list[dict], options: dict[str, set], junction_km: float = JUNCTION_KM) -> dict:
    """pins in trip order: {id, name, destination_id, lat, lng, source_tour_id, source_day_index}.
    options: destination_id -> {(tour_id, day)} of every tour day visiting it."""
    if not pins:
        return {"coverable": True, "legs": [], "transfers": [], "gaps": [], "total_days": 0, "tours": 0}
    choices = []
    for p in pins:
        opts = set(options.get(str(p.get("destination_id")), set()))
        if p.get("source_tour_id") is not None and p.get("source_day_index") is not None:
            opts.add((p["source_tour_id"], int(p["source_day_index"])))
        choices.append(sorted(opts))

    # best[i][k] = (cost tuple, back pointer) for pin i served by choices[i][k]
    best: list[list[tuple]] = [[((0, 0, 1), None) for _ in choices[0]]]
    for i in range(1, len(pins)):
        hop = _km(pins[i - 1], pins[i])
        row = []
        for t2, d2 in choices[i]:
            cand = []
            for k, (t1, d1) in enumerate(choices[i - 1]):
                (gaps, transfers, days), _ = best[i - 1][k]
                if t1 == t2 and d2 >= d1:
                    cand.append(((gaps, transfers, days + d2 - d1), k))
                else:
                    gap = 1 if hop is None or hop > junction_km else 0
                    cand.append(((gaps + gap, transfers + 1, days + 1), k))
            row.append(min(cand) if cand else ((len(pins), len(pins), 0), None))
        best.append(row)

    # walk back
    k = min(range(len(best[-1])), key=lambda j: best[-1][j][0])
    (gaps, transfers, total_days), _ = best[-1][k]
    picked = [None] * len(pins)
    for i in range(len(pins) - 1, -1, -1):
        picked[i] = choices[i][k]
        k = best[i][k][1] if i > 0 else k

    legs: list[dict] = []
    transfers_out, gaps_out = [], []
    for i, (tour, day) in enumerate(picked):
        pin = pins[i]
        if legs and legs[-1]["tour_id"] == tour and day >= legs[-1]["day_to"]:
            legs[-1]["day_to"] = day
            legs[-1]["pins"].append(str(pin["id"]))
            continue
        if legs:
            hop = _km(pins[i - 1], pin)
            t = {"from_pin": str(pins[i - 1]["id"]), "to_pin": str(pin["id"]),
                 "from_name": pins[i - 1].get("name"), "to_name": pin.get("name"),
                 "km": round(hop, 1) if hop is not None else None,
                 "ok": hop is not None and hop <= junction_km}
            transfers_out.append(t)
            if not t["ok"]:
                gaps_out.append({**t, "reason": "no Adventure Asia tour links these two places"})
        legs.append({"tour_id": tour, "day_from": day, "day_to": day, "pins": [str(pin["id"])]})
    for leg in legs:
        leg["days"] = leg["day_to"] - leg["day_from"] + 1
    return {"coverable": not gaps_out, "legs": legs, "transfers": transfers_out, "gaps": gaps_out,
            "total_days": sum(leg["days"] for leg in legs), "tours": len({leg["tour_id"] for leg in legs})}


async def coverage(conn: Any, trip_id: str) -> dict:
    components = await events_mod._current_components(conn, trip_id)  # noqa: SLF001
    if not components:
        return solve([], {})
    explicit = await events_mod._latest_explicit_order(conn, trip_id)  # noqa: SLF001
    order = [str(e["component_id"]) for e in events_mod._project_itinerary(components, explicit)]  # noqa: SLF001
    by_id = {str(c["id"]): c for c in components}
    pins = [by_id[i] for i in order if i in by_id]
    dests = sorted({str(p["destination_id"]) for p in pins if p.get("destination_id")})
    options: dict[str, set] = {}
    for r in await conn.fetch(OPTIONS_SQL, dests):
        options.setdefault(r["destination_id"], set()).add((r["source_tour_id"], int(r["source_day_index"])))
    result = solve(pins, options)
    tour_ids = sorted({leg["tour_id"] for leg in result["legs"]})
    if tour_ids:
        from backend.extraction.tour_days import fetch_catalog  # tenant-scoped (RLS)
        names = {r["tour_id"]: r["aa_name"] for r in await fetch_catalog(conn, TOUR_NAMES_SQL, tour_ids)}
        for leg in result["legs"]:
            leg["tour_name"] = names.get(leg["tour_id"])
    return result
