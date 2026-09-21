"""Extraction pipeline (offline/batch, run manually).

Flow:
  1. read active tours   (gold_aa_internal.published_tours, master_status='active')
  2. read + group atoms  (acp_contract.tour_atoms, deleted=false) by (tour_id, day)
  3. LLM categorize       each group -> proposed components (Sonnet-tier)
  4. verify (retry once)  deterministic activity grounding (verify.py)
  5. geocode              place -> shared.destinations (Mapbox, cached)
  6. insert               tripplanner.itinerary_components (+ embedding)

Nothing at runtime calls this. Usage:  python -m backend.extraction.run

The pure transformation logic (grouping, per-group validation with a
retry) is factored into testable functions that take injected callables,
so the pipeline is unit-tested offline without DB / Mapbox / Bedrock.
"""
from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

import httpx
from pydantic import ValidationError

from backend import config
from backend.extraction import prompts
from backend.extraction.geocode import Destination, geocode_place
from backend.extraction.verify import verify_activity
from backend.shared import bedrock_satellite
from backend.shared.schemas import ExtractedComponent


@dataclass
class AtomGroup:
    tour_id: str
    itinerary_day: int
    texts: list[str]
    primary_destination: Optional[str]

    @property
    def joined_text(self) -> str:
        return "\n".join(self.texts)


# A callable that runs the LLM categorize step for a group and returns the
# raw model text. Injected so tests can stub it.
CategorizeFn = Callable[[AtomGroup], Awaitable[str]]


def group_atoms(rows: list[dict]) -> list[AtomGroup]:
    """Group atom rows by (tour_id, itinerary_day).

    rows: dicts with keys tour_id, itinerary_day, text, and optionally
    primary_destination (the tour's already-known primary place).
    Days with zero atoms simply don't appear (no fallback re-parse).
    """
    grouped: dict[tuple[str, int], list[str]] = defaultdict(list)
    primary: dict[str, Optional[str]] = {}
    for r in rows:
        key = (r["tour_id"], r["itinerary_day"])
        grouped[key].append(r["text"])
        primary.setdefault(r["tour_id"], r.get("primary_destination"))

    out: list[AtomGroup] = []
    for (tour_id, day), texts in grouped.items():
        out.append(
            AtomGroup(
                tour_id=tour_id,
                itinerary_day=day,
                texts=texts,
                primary_destination=primary.get(tour_id),
            )
        )
    return out


def validate_and_ground(
    raw_model_text: str, group: AtomGroup
) -> list[ExtractedComponent]:
    """Parse model output, validate against the schema, and ground each
    component's activity against the group's raw text.

    Returns only the components that pass BOTH Pydantic validation and the
    deterministic activity check. Raises ValueError if the JSON itself is
    unparseable (so the caller can trigger a retry).
    """
    comp_dicts = prompts.parse_components_json(raw_model_text)
    accepted: list[ExtractedComponent] = []
    for cd in comp_dicts:
        try:
            comp = ExtractedComponent(**cd)
        except ValidationError:
            continue  # invented/malformed value — drop this component
        if verify_activity(comp.activity.value, group.joined_text):
            accepted.append(comp)
    return accepted


async def categorize_group(
    group: AtomGroup, categorize: CategorizeFn
) -> list[ExtractedComponent]:
    """Run categorize with a single retry on empty/failed grounding.

    Per requirements: if verify fails, retry the LLM once; if it fails
    again, discard (return []) rather than persist unverified components.
    """
    for attempt in (1, 2):
        try:
            raw = await categorize(group)
            accepted = validate_and_ground(raw, group)
        except (ValueError, json.JSONDecodeError):
            accepted = []
        if accepted:
            return accepted
    return []


def _default_categorize_fn() -> CategorizeFn:
    async def _fn(group: AtomGroup) -> str:
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1500,
            "system": prompts.SYSTEM_PROMPT,
            "messages": [
                {
                    "role": "user",
                    "content": prompts.build_user_prompt(
                        group.tour_id,
                        group.itinerary_day,
                        group.texts,
                        group.primary_destination,
                    ),
                }
            ],
        }
        resp = bedrock_satellite.invoke(config.BEDROCK_MODEL_COMPOSE, body)
        # Claude messages API response shape.
        parts = resp.get("content", [])
        return "".join(p.get("text", "") for p in parts)

    return _fn


async def _read_active_atom_rows(pool) -> list[dict]:
    """Read atoms for active tours, joined with the tour's primary
    destination/country. Read-only against AA-CIS-App schemas."""
    rows = await pool.fetch(
        """
        SELECT a.tour_id, a.itinerary_day, a.text,
               pt.primary_destination AS primary_destination,
               pt.country AS country
        FROM acp_contract.tour_atoms a
        JOIN gold_aa_internal.published_tours pt ON pt.tour_id = a.tour_id
        WHERE pt.master_status = 'active' AND a.deleted = false
        ORDER BY a.tour_id, a.itinerary_day
        """
    )
    return [dict(r) for r in rows]


async def _persist(
    pool, group: AtomGroup, comp: ExtractedComponent, dest: Destination
) -> None:
    embedding = bedrock_satellite.embed(comp.text_extract)
    await pool.execute(
        """
        INSERT INTO tripplanner.itinerary_components
            (source_tour_id, source_day_index, destination_id, name,
             activity, intensity_level, season_months, duration_hint,
             text_extract, embedding)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        """,
        group.tour_id,
        group.itinerary_day,
        dest.id,
        comp.name,
        comp.activity.value,
        comp.intensity_level.value,
        comp.season_months,
        comp.duration_hint.value,
        comp.text_extract,
        embedding,
    )


async def main() -> None:
    from backend.shared.db import close_pool, get_pool

    pool = await get_pool()
    http = httpx.AsyncClient(timeout=20)
    categorize = _default_categorize_fn()
    country_by_tour: dict[str, str] = {}

    try:
        rows = await _read_active_atom_rows(pool)
        for r in rows:
            country_by_tour.setdefault(r["tour_id"], r.get("country") or "")
        groups = group_atoms(rows)
        total = 0
        for group in groups:
            comps = await categorize_group(group, categorize)
            for comp in comps:
                dest = await geocode_place(
                    comp.name,
                    country_by_tour.get(group.tour_id, ""),
                    pool=pool,
                    http=http,
                )
                await _persist(pool, group, comp, dest)
                total += 1
        print(f"Extraction complete: {total} components from {len(groups)} groups.")
    finally:
        await http.aclose()
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
