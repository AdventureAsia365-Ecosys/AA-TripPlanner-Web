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

NOTE ON STEERING: tech.md forbids *re-parsing aa_itineraries to synthesise
atoms during extraction* (that pipeline must use acp_contract.tour_atoms).
This is a different use: reading the same authored prose at narrate-time to
show it back to the traveller. It does not feed extraction or grounding.
"""
from __future__ import annotations

import re
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

    For each itinerary entry, look up the authored text by
    (source_tour_id, source_day_index). Returns:
      - narration: lines "Day <trip_day>: <authored text>" joined, in trip
        order, for the days we could resolve;
      - missing: the itinerary entries with no master text (caller may send
        these to the LLM as a fallback).

    The authored text keeps AA's own day title/prose but is re-labelled with
    the trip's day number (the component may be day 3 of the trip even if it
    was day 6 of the source tour).
    """
    lines: list[str] = []
    missing: list[dict] = []
    for e in itinerary:
        tid = e.get("source_tour_id")
        didx = e.get("source_day_index")
        text = None
        if tid is not None and didx is not None:
            text = day_texts.get((str(tid), int(didx)))
        if not text:
            missing.append(e)
            continue
        # Strip the source day header ("Day 6 — Title:") so we don't show a
        # day number that contradicts the trip order; keep the title/prose.
        body = _strip_leading_day_header(text)
        lines.append(f"Day {e['day']}: {body}")
    return "\n".join(lines), missing


def _strip_leading_day_header(text: str) -> str:
    """Remove a leading 'Day N —/:/- ' marker, keeping the rest (title +
    prose) intact so the trip's own day numbering is authoritative."""
    return _DAY_HEADER.sub("", text, count=1).strip()
