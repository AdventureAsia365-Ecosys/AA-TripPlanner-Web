"""Prompt construction for the extraction categorize step.

The LLM only SELECTS from the closed taxonomy and writes clean copy for
places that already exist in the atom text. It never invents facts; the
downstream Pydantic validation + deterministic verify.py enforce this.
"""
from __future__ import annotations

import json

from backend.shared.schemas import Activity, DurationHint, Intensity

_ACTIVITIES = ", ".join(a.value for a in Activity)
_INTENSITIES = ", ".join(i.value for i in Intensity)
_DURATIONS = ", ".join(d.value for d in DurationHint)

SYSTEM_PROMPT = (
    "You extract structured itinerary components from an Adventure Asia "
    "tour's day of raw content. You never invent places, activities, or "
    "facts that are not present in the provided atom text. You only "
    "select from fixed category lists and lightly clean the wording."
)


def build_user_prompt(
    tour_id: str,
    itinerary_day: int,
    atom_texts: list[str],
    primary_destination: str | None,
) -> str:
    """Build the categorize prompt for one (tour_id, itinerary_day) group."""
    atoms_block = "\n".join(f"- {t}" for t in atom_texts)
    primary_hint = (
        f"If no atom names a clear place, use the tour's primary "
        f"destination: {primary_destination!r}."
        if primary_destination
        else "If no atom names a clear place, return an empty list."
    )
    return f"""Tour {tour_id}, itinerary day {itinerary_day}.

Raw atom texts for this day:
{atoms_block}

Decide whether these atoms describe ONE place or SEVERAL genuinely
distinct destinations (e.g. a travel day crossing two towns). Produce one
component per distinct place.

For each component, choose values strictly from these closed lists:
- activity: {_ACTIVITIES}
- intensity_level: {_INTENSITIES}
- duration_hint: {_DURATIONS}
- season_months: an array of integers 1..12 for months the activity is
  suitable; if unknown, use all 12.

{primary_hint}

Return ONLY valid JSON of this exact shape (no prose):
{{
  "components": [
    {{
      "name": "<clean place name>",
      "activity": "<one of the activity values>",
      "intensity_level": "<one of the intensity values>",
      "duration_hint": "<one of the duration values>",
      "season_months": [<ints>],
      "text_extract": "<one or two clean sentences drawn from the atoms>"
    }}
  ]
}}"""


def parse_components_json(raw: str) -> list[dict]:
    """Parse the model's JSON response into a list of component dicts.

    Tolerant of a leading/trailing code fence but not of invented shape.
    """
    text = raw.strip()
    if text.startswith("```"):
        # strip ```json ... ``` fences
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    data = json.loads(text)
    comps = data.get("components", [])
    if not isinstance(comps, list):
        raise ValueError("`components` must be a list")
    return comps
