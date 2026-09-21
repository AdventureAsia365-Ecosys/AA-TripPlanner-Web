"""Deterministic (no-Bedrock) extraction run — TEMPORARY seeding path.

Reads real tour_atoms (which already carry place / activity_type /
action / text), maps each pinnable atom to one itinerary_component,
geocodes the place via Mapbox (cached in shared.destinations), and
inserts with embedding=NULL.

Three phases, optimised for a high-latency tunnel + slow geocoding:
  1. categorize all atoms in memory; collect the set of unique places
  2. geocode unique places CONCURRENTLY (bounded), persisting each new
     one to shared.destinations, updating an in-memory cache
  3. build all component rows from the cache and bulk-insert (1 round-trip)

Lets the map/browse/trip loop run against real data before the Bedrock
satellite trust is wired. Swap for `python -m backend.extraction.run`
(LLM + embeddings) once Bedrock is available — data-source change only.

Usage:
    set -a; . backend/.env; set +a
    PYTHONPATH=. .venv/bin/python -m backend.extraction.run_deterministic
"""
from __future__ import annotations

import asyncio
import uuid as _uuid

import httpx

from backend.extraction.deterministic import categorize_atom
from backend.extraction.geocode import Destination, geocode_place, preload_cache

GEOCODE_CONCURRENCY = 10


async def _read_atoms(pool) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT a.tour_id, a.itinerary_day, a.place, a.activity_type, a.text,
               pt.aa_name AS aa_name
        FROM acp_contract.tour_atoms a
        JOIN gold_aa_internal.published_tours pt ON pt.tour_id = a.tour_id
        WHERE pt.master_status = 'active'
          AND a.deleted = false
          AND a.place IS NOT NULL
          AND a.itinerary_day IS NOT NULL
        ORDER BY a.tour_id, a.itinerary_day
        """
    )
    return [dict(r) for r in rows]


# Words at the start of a tour name that aren't the region itself.
_REGION_STOPWORDS = {"northern", "southern", "eastern", "western", "central",
                     "north", "south", "east", "west", "the", "greater"}


def region_from_tour_name(aa_name: str | None) -> str:
    """Derive a geocoding region hint from a tour name.

    Tour names lead with the region, e.g. "Southern Laos: Plateau…" or
    "Luang Prabang: Temples…". Take the text before the first ':' or '—',
    drop directional stopwords, and use what's left as a Mapbox query
    suffix so places resolve on the right continent.
    """
    if not aa_name:
        return ""
    head = aa_name.split(":")[0].split("—")[0].split("-")[0].strip()
    words = [w for w in head.split() if w.lower() not in _REGION_STOPWORDS]
    return " ".join(words).strip()


async def main() -> None:
    from backend.shared.db import close_pool, get_pool

    pool = await get_pool()
    http = httpx.AsyncClient(timeout=20)
    skipped = 0

    try:
        # Clean-slate: reset both the owned components table AND the
        # destinations we seeded, so region-aware geocoding re-resolves
        # coordinates (earlier runs geocoded without region context and
        # placed some pins on the wrong continent). shared.destinations was
        # empty before this project, so all rows in it are ours to reset.
        await pool.execute("TRUNCATE tripplanner.itinerary_components")
        await pool.execute("TRUNCATE shared.destinations CASCADE")
        mem_cache = await preload_cache(pool)
        print(f"Geocode cache preloaded: {len(mem_cache)} known places.", flush=True)
        atoms = await _read_atoms(pool)

        # --- Phase 1: categorize + collect (atom, component), unique places,
        #     and a place -> region hint (first tour that mentions the place)
        pairs: list[tuple[dict, object]] = []
        unique_places: set[str] = set()
        region_by_place: dict[str, str] = {}
        for a in atoms:
            comp = categorize_atom(a["place"], a["activity_type"], a["text"] or "")
            if comp is None:
                skipped += 1
                continue
            pairs.append((a, comp))
            unique_places.add(comp.name)
            region_by_place.setdefault(comp.name, region_from_tour_name(a.get("aa_name")))
        print(
            f"Categorized {len(pairs)} atoms; {len(unique_places)} unique places; "
            f"{skipped} skipped.",
            flush=True,
        )

        # --- Phase 2: geocode unique places concurrently (bounded), region-aware
        to_geocode = [p for p in unique_places if p.lower() not in mem_cache]
        print(f"Geocoding {len(to_geocode)} new places (conc={GEOCODE_CONCURRENCY})…",
              flush=True)
        sem = asyncio.Semaphore(GEOCODE_CONCURRENCY)
        geo_fail = 0

        async def _one(place: str):
            nonlocal geo_fail
            async with sem:
                try:
                    await geocode_place(place, "", pool=pool, http=http,
                                        mem_cache=mem_cache,
                                        region=region_by_place.get(place, ""))
                except Exception as e:  # noqa: BLE001
                    geo_fail += 1
                    print(f"  geocode skip: {place!r} ({type(e).__name__})",
                          flush=True)

        await asyncio.gather(*(_one(p) for p in to_geocode))
        print(f"Geocoding done: {geo_fail} failures.", flush=True)

        # --- Phase 3: build rows from cache + bulk insert
        records: list[tuple] = []
        for a, comp in pairs:
            dest: Destination | None = mem_cache.get(comp.name.lower())
            if dest is None:
                continue  # geocode failed for this place — skip
            records.append(
                (
                    str(a["tour_id"]),
                    a["itinerary_day"],
                    _uuid.UUID(str(dest.id)),
                    comp.name,
                    comp.activity.value,
                    comp.intensity_level.value,
                    list(comp.season_months),
                    comp.duration_hint.value,
                    comp.text_extract,
                    # embedding omitted — defaults to NULL. (asyncpg binary
                    # COPY has no encoder for the pgvector type; the real
                    # run.py sets embeddings via a normal INSERT.)
                )
            )

        async with pool.acquire() as conn:
            await conn.copy_records_to_table(
                "itinerary_components",
                schema_name="tripplanner",
                columns=[
                    "source_tour_id", "source_day_index", "destination_id",
                    "name", "activity", "intensity_level", "season_months",
                    "duration_hint", "text_extract",
                ],
                records=records,
            )

        print(
            f"Deterministic seed complete: {len(records)} components seeded "
            f"from {len(atoms)} atoms ({len(unique_places)} places, "
            f"{geo_fail} geocode failures, {skipped} skipped).",
            flush=True,
        )
    finally:
        await http.aclose()
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
