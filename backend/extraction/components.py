"""AA-674 step 1 — rebuild tripplanner.itinerary_components from the atoms of active tours.

The components on Dev were built from the pre-reset tour set: 0 rows for the 121 active tours
(29/09/2026), so the Tour Graph has no activities, intensity or seasons per stop, and search and
"suggest next" only know old tours.

Per tour (one Lambda invoke, extraction op "components"):
  1. read its atoms (acp_contract.tour_atoms, column grant: CIS migration 180);
  2. map each pinnable atom to one component with the deterministic categorizer (no model call:
     atoms already carry place + activity_type); skip lodging and transit "places";
  3. link each place to shared.destinations (known names reuse their row, new names go through
     locate.py: one model call per 40 names, confirmed by Mapbox);
  4. embed the component texts (tp_component_embed, 96 per call);
  5. replace the tour's components.

Saved trips reference component ids in their events (no FK); only active tours are rebuilt, and
their old components (if any) are replaced.
"""
from __future__ import annotations

from typing import Awaitable, Callable, Optional

from backend.extraction.deterministic import categorize_atom
from backend.extraction.tour_days import anchor_place

EMBED_BATCH = 96

ATOMS_SQL = """
    SELECT atom_id, itinerary_day, place, activity_type, text
    FROM acp_contract.tour_atoms
    WHERE tour_id::text = $1 AND deleted = false AND NOT coalesce(is_empty_marker, false)
      AND place IS NOT NULL AND itinerary_day IS NOT NULL
    ORDER BY itinerary_day, atom_id
"""

LinkFn = Callable[[list[str], str, str], Awaitable[dict]]            # (places, country, tour) -> {place: id}
EmbedFn = Callable[[list[str]], Awaitable[list[list[float]]]]


def build_components(atoms: list[dict]) -> tuple[list[dict], int]:
    """Atoms -> component dicts (without destination/embedding) and the number skipped."""
    out, skipped = [], 0
    for a in atoms:
        # A hotel or a train is not a place to pin; anchor_place() says None or the same name.
        place = (a["place"] or "").strip()
        if not place or anchor_place(place, None) != place:
            skipped += 1
            continue
        comp = categorize_atom(place, a["activity_type"], a["text"] or "")
        if comp is None:
            skipped += 1
            continue
        out.append({"day_index": a["itinerary_day"], "name": comp.name, "activity": comp.activity.value,
                    "intensity_level": comp.intensity_level.value, "season_months": list(comp.season_months),
                    "duration_hint": comp.duration_hint.value, "text_extract": comp.text_extract})
    return out, skipped


def vector_literal(vec: list[float]) -> str:
    """pgvector text literal, the same shape search.py sends."""
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


async def rebuild_tour(db, tour: dict, link: Optional[LinkFn], embed: Optional[EmbedFn]) -> dict:
    atoms = [dict(r) for r in await db.fetch(ATOMS_SQL, tour["tour_id"])]
    comps, skipped = build_components(atoms)
    places = list(dict.fromkeys(c["name"] for c in comps))
    dest_ids = await link(places, tour["country"] or "", tour["aa_name"] or "") if (places and link) else {}
    comps = [c for c in comps if dest_ids.get(c["name"])]
    vectors: list[Optional[list[float]]] = [None] * len(comps)
    if embed is not None:
        for i in range(0, len(comps), EMBED_BATCH):
            vectors[i:i + EMBED_BATCH] = await embed([c["text_extract"] for c in comps[i:i + EMBED_BATCH]])
    async with db.transaction():
        await db.execute("DELETE FROM tripplanner.itinerary_components WHERE source_tour_id = $1", tour["tour_id"])
        if comps:
            await db.executemany(
                "INSERT INTO tripplanner.itinerary_components (source_tour_id, source_day_index, destination_id, "
                "name, activity, intensity_level, season_months, duration_hint, text_extract, embedding) "
                "VALUES ($1, $2, $3::uuid, $4, $5, $6, $7, $8, $9, $10::vector)",
                [(tour["tour_id"], c["day_index"], dest_ids[c["name"]], c["name"], c["activity"],
                  c["intensity_level"], c["season_months"], c["duration_hint"], c["text_extract"],
                  vector_literal(v) if v else None) for c, v in zip(comps, vectors)])
    return {"atoms": len(atoms), "components": len(comps), "skipped": skipped, "places": len(places),
            "linked_places": sum(1 for p in places if dest_ids.get(p)),
            "embedded": sum(1 for v in vectors if v)}
