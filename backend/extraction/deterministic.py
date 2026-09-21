"""Deterministic (no-LLM) categorizer for the extraction pipeline.

Used by run_deterministic.py to seed real components from real atoms
WITHOUT Bedrock, so the map/browse/trip loop can be exercised end-to-end
before the Bedrock satellite trust is in place.

The real tour_atoms schema is richer than a plain text blob: each atom
already carries `place` (a clean destination name), `activity_type` (a
7-value enum), and `action` (a clean phrase). So the deterministic path
is 1 atom -> 1 component:
- name: atom.place
- activity: map atom.activity_type -> our taxonomy; refine 'other' by
  keyword; skip 'transit' (not a pinnable destination)
- text_extract: atom.text (already "Place — action")
- intensity/duration/season: simple transparent defaults
- embedding: left NULL by the caller (no Bedrock)

The final activity is still run through verify.py by the caller so a
component is only kept if its activity is grounded in the atom text.
This is a TEMPORARY seeding path; the LLM step (run.py) supersedes it.
"""
from __future__ import annotations

from typing import Optional

from backend.shared.schemas import (
    Activity,
    DurationHint,
    ExtractedComponent,
    Intensity,
)

# tour_atoms.activity_type -> our Activity taxonomy.
_ACTIVITY_TYPE_MAP = {
    "trek": Activity.trekking,
    "culture": Activity.cultural_heritage,
    "food": Activity.culinary,
    "bike": Activity.adventure_sport,
    "stay": Activity.wellness_relaxation,
    # 'other' resolved by keyword below; 'transit' skipped (not pinnable).
}

# Keyword refinement for activity_type='other'.
_OTHER_KEYWORDS = [
    ("wildlife_nature", ("safari", "national park", "wildlife", "jungle",
                          "elephant", "bird", "nature", "forest", "tea estate",
                          "tea ")),
    ("water_activities", ("beach", "cruise", "kayak", "snorkel", "dive",
                          "boat", "river", "lagoon", "swim", "sail")),
    ("culinary", ("food", "lunch", "dinner", "cooking", "market", "tasting")),
    ("trekking", ("trek", "hike", "walk", "climb", "summit")),
]

_INTENSITY_BY_ACTIVITY = {
    Activity.trekking: Intensity.active,
    Activity.adventure_sport: Intensity.strenuous,
    Activity.water_activities: Intensity.moderate,
    Activity.wildlife_nature: Intensity.moderate,
    Activity.cultural_heritage: Intensity.leisurely,
    Activity.culinary: Intensity.leisurely,
    Activity.wellness_relaxation: Intensity.leisurely,
    Activity.local_immersion: Intensity.leisurely,
}


def map_activity(activity_type: Optional[str], text: str) -> Optional[Activity]:
    """Map an atom's activity_type to our taxonomy. Returns None for
    'transit' or unmappable rows (caller skips those)."""
    at = (activity_type or "").strip().lower()
    if at == "transit":
        return None
    if at in _ACTIVITY_TYPE_MAP:
        return _ACTIVITY_TYPE_MAP[at]
    if at == "other" or at == "":
        t = text.lower()
        for activity_value, kws in _OTHER_KEYWORDS:
            if any(k in t for k in kws):
                return Activity(activity_value)
        return Activity.local_immersion
    return Activity.local_immersion


def categorize_atom(
    place: Optional[str], activity_type: Optional[str], text: str
) -> Optional[ExtractedComponent]:
    """Build one component from a single atom, or None if not pinnable."""
    name = (place or "").strip()
    if not name:
        return None
    activity = map_activity(activity_type, text or "")
    if activity is None:
        return None
    intensity = _INTENSITY_BY_ACTIVITY.get(activity, Intensity.moderate)
    extract = (text or "").strip()[:280] or name
    try:
        return ExtractedComponent(
            name=name,
            activity=activity,
            intensity_level=intensity,
            duration_hint=DurationHint.full_day,
            season_months=list(range(1, 13)),
            text_extract=extract,
        )
    except Exception:
        return None
