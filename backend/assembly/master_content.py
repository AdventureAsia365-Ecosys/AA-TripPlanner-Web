"""Master itinerary content lookup.

AA already has editorial, day-by-day itinerary prose on
`gold_aa_internal.published_tours.aa_itineraries` (one block of text per
tour, structured as "Day N — <title>: <prose>"). We prefer that authored
text for a trip's day-by-day narration over asking the LLM to rewrite atom
fragments — it is AA's real voice and costs no Bedrock call.

This module reads those blocks for the tours referenced by a trip and
parses them into `{(source_tour_id, day_index): text}`. The narration path
uses it first and only falls back to the LLM for days it can't resolve
(e.g. a tour whose itinerary isn't structured by day).

The day text is used only when it names the pinned stop (AA-819): a component row's tour day can
be wrong, and showing another place's day under the traveller's stop is worse than an AI line.
"AA's real voice" is the CIS S1 writer's reviewed output, so it is AI-assisted text: the
frontend labels it as such ("AA itinerary", written with AI assistance and reviewed).

NOTE ON STEERING: tech.md forbids *re-parsing aa_itineraries to synthesise
atoms during extraction* (that pipeline must use acp_contract.tour_atoms).
This is a different use: reading the same authored prose at narrate-time to
show it back to the traveller. It does not feed extraction or grounding.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Optional, Protocol


from backend import config


class _Conn(Protocol):
    async def fetch(self, query: str, *args: Any) -> Any: ...
    async def execute(self, query: str, *args: Any) -> Any: ...
    def transaction(self) -> Any: ...


# Matches a day header at the start of a line, tolerant of the formats seen
# in the data: "Day 1 — ...", "Day 01: ...", "DAY 1 - ...". Captures the day
# number. En/em dashes and colon/hyphen all accepted as the separator.
_DAY_HEADER = re.compile(
    r"^\s*Day\s*(\d{1,2})\s*[:\-\u2013\u2014]",
    re.IGNORECASE | re.MULTILINE,
)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def parse_days(block: str) -> dict[int, str]:
    """Split one tour's aa_itineraries block into {day_number: text}.

    Each day's text is everything from its header up to the next day header
    (header line included, trimmed). Returns {} if no day headers are found
    (an unstructured itinerary — caller falls back to the LLM)."""
    if not block:
        return {}
    matches = list(_DAY_HEADER.finditer(block))
    if not matches:
        return {}
    out: dict[int, str] = {}
    for i, m in enumerate(matches):
        day = int(m.group(1))
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(block)
        text = block[start:end].strip()
        # First occurrence wins if a day number somehow repeats.
        out.setdefault(day, text)
    return out


async def load_for_tours(
    conn: _Conn, tour_ids: list[str]
) -> dict[tuple[str, int], str]:
    """Load and parse aa_itineraries for the given source_tour_ids.

    Returns {(tour_id, day_index): day_text}. Tours with no itinerary text
    or no parseable day headers simply contribute nothing."""
    ids = [t for t in {str(t) for t in tour_ids} if t]
    if not ids:
        return {}
    sql = """
        SELECT tour_id::text AS tour_id, aa_itineraries
        FROM gold_aa_internal.published_tours
        WHERE tour_id::text = ANY($1::text[])
          AND aa_itineraries IS NOT NULL
    """
    # published_tours enforces tenant RLS. Set the AA tenant for the scope of
    # this read only (SET LOCAL, inside a transaction, so it never leaks to
    # other queries on a pooled connection). Without it RLS returns no rows
    # and the caller falls back to the LLM.
    tenant = config.MASTER_CONTENT_TENANT_ID
    if tenant:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant
            )
            rows = await conn.fetch(sql, ids)
    else:
        rows = await conn.fetch(sql, ids)
    result: dict[tuple[str, int], str] = {}
    for r in rows:
        tour_id = str(r["tour_id"])
        for day, text in parse_days(r["aa_itineraries"]).items():
            result[(tour_id, day)] = text
    return result


def build_narration(
    itinerary: list[dict],
    day_texts: dict[tuple[str, int], str],
) -> tuple[str, list[dict]]:
    """Assemble day-by-day narration from master content where available.

    For each trip day, look up the authored text of its entries' tour day
    (source_tour_id, source_day_index), see `resolve_day_text`. Returns:
      - narration: "Day <trip_day>: <authored text>" blocks joined, in trip
        order, for the days we could resolve. A block can span several lines
        (the authored day title, then its prose);
      - missing: the itinerary entries of the days with no master text
        (caller may send these to the LLM as a fallback).

    The authored text keeps AA's own day title/prose but is re-labelled with
    the trip's day number (the component may be day 3 of the trip even if it
    was day 6 of the source tour).
    """
    lines: list[str] = []
    missing: list[dict] = []
    # Several activities of one AA tour day share a trip day (AA-674): narrate it once, from the
    # first of its entries whose tour day text resolves.
    by_day: dict[Any, list[dict]] = {}
    for e in itinerary:
        by_day.setdefault(e.get("day"), []).append(e)
    for day, entries in by_day.items():
        text = next((t for t in (resolve_day_text(e, day_texts) for e in entries) if t), None)
        if not text:
            missing.extend(entries)
            continue
        # Strip the source day header ("Day 6 — Title:") so we don't show a
        # day number that contradicts the trip order; keep the title/prose.
        lines.append(f"Day {day}: {_strip_leading_day_header(text)}")
    return "\n".join(lines), missing


def resolve_day_text(entry: dict, day_texts: dict[tuple[str, int], str]) -> Optional[str]:
    """The authored day text to narrate `entry` with, or None.

    AA-819 D1: the component row's tour day can be wrong (S239: Amber Fort pinned as day 5 of
    'Rajasthan: Forts, Havelis & Desert', whose day 5 is "Khimsar to Jaisalmer"; the tour visits
    Amber Fort on day 16). So the text is used only when it names the stop (component or place
    name). If the row's own day does not, the nearest day of the same tour that does is used;
    if none does, None (the day goes to the LLM, or to the neutral line when the budget is spent).
    An entry with no name at all cannot be checked and keeps its own day."""
    tid, didx = entry.get("source_tour_id"), entry.get("source_day_index")
    if tid is None or didx is None:
        return None
    tid, didx = str(tid), int(didx)
    keys = _stop_keys(entry)
    own = day_texts.get((tid, didx))
    if own and (not keys or _mentions(own, keys)):
        return own
    if not keys:
        return None
    others = sorted(
        (abs(d - didx), d, text) for (t, d), text in day_texts.items()
        if t == tid and d != didx and text and _mentions(text, keys)
    )
    return others[0][2] if others else None


def _norm(text: str) -> str:
    """Case-, accent- and punctuation-insensitive form, padded so a key matches whole words."""
    ascii_text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return f" {_NON_ALNUM.sub(' ', ascii_text.lower()).strip()} "


def _stop_keys(entry: dict) -> list[str]:
    keys = (_norm(entry.get(k) or "") for k in ("name", "destination_name"))
    return [k for k in keys if k.strip()]


def _mentions(text: str, keys: list[str]) -> bool:
    t = _norm(text)
    return any(k in t for k in keys)


def _strip_leading_day_header(text: str) -> str:
    """Remove a leading 'Day N —/:/- ' marker, keeping the rest (title +
    prose) intact so the trip's own day numbering is authoritative."""
    return _DAY_HEADER.sub("", text, count=1).strip()
