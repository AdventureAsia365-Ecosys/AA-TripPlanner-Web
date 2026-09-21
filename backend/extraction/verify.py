"""Deterministic grounding check for the extraction step.

The LLM assigns an `activity` to an atom group. Before we persist a
component, we confirm — deterministically, without a second LLM call —
that the assigned activity (or a close synonym) actually appears in the
group's raw atom text. This mirrors ACPv2's numeric-claim entailment
discipline, applied to category assignment.

If the check fails, run.py retries the LLM once for that group; on a
second failure the group is discarded (no component produced) rather than
persisting an unverified guess.
"""
from __future__ import annotations

import re

# Synonyms / trigger words per activity. Lowercased, matched as whole
# words (word-boundary) against the raw atom text. Kept intentionally
# conservative: a match should be a genuine signal, not a coincidence.
ACTIVITY_SYNONYMS: dict[str, list[str]] = {
    "trekking": [
        "trek", "trekking", "hike", "hiking", "hiked", "walk", "walking",
        "trail", "trails", "summit", "ascent", "climb", "climbing",
        "footpath", "trekker",
    ],
    "cultural_heritage": [
        "temple", "temples", "monastery", "shrine", "heritage", "ruins",
        "palace", "fort", "fortress", "museum", "historic", "historical",
        "ancient", "pagoda", "cathedral", "unesco", "monument", "citadel",
        "archaeological", "colonial", "architecture", "architectural",
    ],
    "wildlife_nature": [
        "wildlife", "safari", "national park", "jungle", "rainforest",
        "elephant", "elephants", "tiger", "birds", "birdwatching",
        "bird-watching", "nature", "forest", "reserve", "sanctuary",
        "orangutan", "leopard", "trek through", "flora", "fauna",
    ],
    "water_activities": [
        "kayak", "kayaking", "canoe", "canoeing", "snorkel", "snorkeling",
        "snorkelling", "dive", "diving", "scuba", "raft", "rafting",
        "cruise", "boat", "boating", "sail", "sailing", "swim", "swimming",
        "beach", "lagoon", "river cruise", "paddle", "paddling", "surf",
        "surfing",
    ],
    "culinary": [
        "cooking", "cuisine", "food", "dining", "dinner", "lunch",
        "tasting", "market", "street food", "culinary", "meal", "wine",
        "restaurant", "chef", "spices", "spice", "feast", "dishes",
        "gastronomy",
    ],
    "wellness_relaxation": [
        "spa", "massage", "yoga", "meditation", "wellness", "relax",
        "relaxation", "retreat", "hot spring", "onsen", "thermal",
        "rejuvenate", "unwind", "leisure", "tranquil", "serene",
    ],
    "adventure_sport": [
        "cycling", "cycle", "biking", "mountain bike", "zip line",
        "zipline", "ziplining", "rock climbing", "abseil", "abseiling",
        "paraglide", "paragliding", "bungee", "kitesurf", "windsurf",
        "off-road", "quad", "atv", "caving", "canyoning",
    ],
    "local_immersion": [
        "village", "villages", "homestay", "local", "community",
        "market", "craft", "crafts", "artisan", "workshop", "tribe",
        "tribal", "ethnic", "traditional", "immersion", "family",
        "handicraft", "weaving", "farm", "farming", "rural",
    ],
}


def _normalize(text: str) -> str:
    return text.lower()


def verify_activity(activity: str, source_text: str) -> bool:
    """Return True if `activity` (or a synonym) is grounded in the text.

    Matching is case-insensitive and word-boundary aware, so "walk" does
    not match "walkway"'s substring incidentally beyond a real word, and
    "hike" does not match "hiker"'s... actually multi-form synonyms are
    listed explicitly to keep this predictable.
    """
    if not source_text:
        return False
    text = _normalize(source_text)
    synonyms = ACTIVITY_SYNONYMS.get(activity)
    if synonyms is None:
        # Unknown activity value should never reach here (Pydantic enum
        # gates it upstream), but fail closed if it does.
        return False

    for term in synonyms:
        # Multi-word terms: plain substring on normalized text.
        if " " in term or "-" in term:
            if term in text:
                return True
            continue
        # Single-word terms: whole-word match.
        if re.search(rf"\b{re.escape(term)}\b", text):
            return True
    return False
