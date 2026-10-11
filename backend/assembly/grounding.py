"""Numeric grounding for LLM narration (AA-731 item 4).

Customer-facing narration may only state figures that the trip's own components say. The check
is the same narrow, high-precision rule CIS uses for its rewrites
(AA-CIS-App services/acp_shared/grounding.py::find_novel_numeric_claims, ADR-2026-033): a number
or measurement in the narration that appears in none of the source texts is an invented fact.
It does not judge qualitative claims — that class has no reliable lexical signal.

Applied to the LLM-written days only; authored master-content days are AA's own text.
A "Day N:" line with an invented number is replaced by a neutral line built from the
components' own names; any other line carrying one is dropped.
"""
from __future__ import annotations

import re

# Same number shape as CIS (AA-639): digit run with optional thousands separators/decimals, not
# starting inside a word, unit allowed right after ("4,460m"). "4,460" == "4460".
_NUM_RE = re.compile(r"(?<![\w.,])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")
_DAY_LABEL_RE = re.compile(r"\bDay\s+\d+\b\s*:?", re.IGNORECASE)
_DAY_LINE_RE = re.compile(r"^\s*Day\s*(\d+)\s*:", re.IGNORECASE)


def _numbers(text: str) -> set[str]:
    return {m.replace(",", "") for m in _NUM_RE.findall(text or "")}


def novel_numbers(text: str, sources: list[str], allowed: set[str] = frozenset()) -> list[str]:
    """Numbers in `text` (Day labels stripped) found in none of `sources` nor in `allowed`."""
    found = _numbers(_DAY_LABEL_RE.sub("", text))
    if not found:
        return []
    supported = set(allowed)
    for s in sources:
        supported |= _numbers(s)
    return sorted(found - supported)


def ground_narration(narration: str, itinerary: list[dict]) -> tuple[str, list[dict]]:
    """Return (narration with invented figures removed, [{line, numbers}] removed/replaced).

    Each "Day N:" line is checked against that day's components (name + text_extract); other
    lines against all of them. Day numbers themselves are structural and always allowed."""
    by_day: dict[int, list[dict]] = {}
    for e in itinerary:
        by_day.setdefault(int(e["day"]), []).append(e)
    day_numbers = {str(d) for d in by_day}
    all_sources = [f"{e.get('name') or ''} {e.get('text_extract') or ''}" for e in itinerary]

    kept: list[str] = []
    removed: list[dict] = []
    for line in narration.splitlines():
        m = _DAY_LINE_RE.match(line)
        day = int(m.group(1)) if m else None
        entries = by_day.get(day, []) if day is not None else []
        sources = ([f"{e.get('name') or ''} {e.get('text_extract') or ''}" for e in entries]
                   if entries else all_sources)
        bad = novel_numbers(line, sources, day_numbers)
        if not bad:
            kept.append(line)
            continue
        removed.append({"line": line.strip(), "numbers": bad})
        if day is not None:
            names = ", ".join(e.get("name") for e in entries if e.get("name"))
            kept.append(f"Day {day}: {names}." if names else f"Day {day}.")
    return "\n".join(kept), removed
