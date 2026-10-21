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

A component row's tour day can be wrong (AA-819), so a day that does not name the pinned stop
is moved to another, unused day of the same tour that does (`resolve_days`).
"AA's real voice" is the CIS S1 writer model's output (checked by automated gates; only flagged
tours reach a human), so it is AI-assisted text: the frontend labels it "AA itinerary ·
AI-assisted" and the API reports it as ai_generated.

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
from backend.shared import places_source

# AA-847 slice 6c (PLACES_SOURCE=cis): component tour keys are catalog source_ids; catalog.v_tour carries the same
# aa_itineraries text as itinerary_text (live tours only, no AA tenant needed).
CIS_TEXTS_SQL = """
    SELECT source_id AS tour_id, itinerary_text AS aa_itineraries
    FROM catalog.v_tour
    WHERE source_id = ANY($1::text[]) AND itinerary_text IS NOT NULL
"""


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
    ids = sorted(t for t in {str(t) for t in tour_ids if t} if t)
    if not ids:
        return {}
    if places_source.is_cis():
        return _parsed(await conn.fetch(CIS_TEXTS_SQL, ids))
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
    return _parsed(rows)


def _parsed(rows) -> dict[tuple[str, int], str]:
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

    For each trip day, the authored text chosen by `resolve_days` (normally the entries' own tour
    day, source_tour_id + source_day_index). Returns:
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
    resolved = resolve_days(itinerary, day_texts)
    for day, entries in _group_by_day(itinerary).items():
        text = resolved.get(day)
        if not text:
            missing.extend(entries)
            continue
        # Strip the source day header ("Day 6 — Title:") so we don't show a
        # day number that contradicts the trip order; keep the title/prose.
        lines.append(f"Day {day}: {_strip_leading_day_header(text)}")
    return "\n".join(lines), missing


def resolve_days(itinerary: list[dict], day_texts: dict[tuple[str, int], str]) -> dict[Any, Optional[str]]:
    """The authored text to narrate each trip day with: {trip day: text or None}.

    Several entries can share a trip day (AA-674: activities of one AA tour day); the day is
    narrated once. AA-819 D1: a component row's tour day can be wrong (S239: Amber Fort pinned as
    day 5 of 'Rajasthan: Forts, Havelis & Desert', whose day 5 is "Khimsar to Jaisalmer"; the tour
    visits Amber Fort on day 16). The rule, per trip day:

      1. Confirmed own day: the own tour day text of any of the day's entries that names that
         entry's stop (component or place name; an entry with no name cannot be checked and counts
         as confirmed).
      2. Redirect: else, the nearest other day of the same tour that names the stop AND that no
         entry of this itinerary has as its own day and no earlier trip day already took. In a
         whole-tour "Customise this trip" itinerary every tour day is someone's own day, so a loose
         pin never pulls a neighbour's text in twice (review of S239 lane: EBC, Phuket cases).
      3. Else the own day text as it is: a day that names no stop is more often a spelling variant
         ("Koh Phi Phi" for "Koh Phi Phi Don") than a wrong day, and AA text beats a paid AI line.
      4. Else None (no own text): the day goes to the LLM, or to the neutral line over budget.
    """
    groups = _group_by_day(itinerary)
    own_keys = {k for e in itinerary if (k := _own_key(e)) is not None}
    taken: set[tuple[str, int]] = set()
    out: dict[Any, Optional[str]] = {}
    pending: list[Any] = []
    for day, entries in groups.items():
        key = next((k for e in entries if (k := _own_key(e)) is not None
                    and day_texts.get(k) and _confirms(e, day_texts[k])), None)
        if key is None:
            pending.append(day)
            continue
        out[day] = day_texts[key]
        taken.add(key)
    for day in pending:
        entries = groups[day]
        key = next((k for e in entries
                    if (k := _redirect(e, day_texts, own_keys | taken)) is not None), None)
        if key is None:
            key = next((k for e in entries if (k := _own_key(e)) is not None and day_texts.get(k)), None)
        if key is not None:
            taken.add(key)
        out[day] = day_texts[key] if key is not None else None
    return out


def guarded_days(itinerary: list[dict], day_texts: dict[tuple[str, int], str]) -> list[dict]:
    """AA-847 (TP #86, the hand-off twin of resolve_days): the itinerary with each entry's tour day checked
    against AA's day text. An entry whose own day text does not name its stop moves to the nearest other day of the
    same tour that does and that no other entry has as its own day (the narration rule); otherwise it keeps its
    day. Returns copies; a moved entry carries ``listed_day_index`` (the component row's day)."""
    own_keys = {k for e in itinerary if (k := _own_key(e)) is not None}
    out = []
    for e in itinerary:
        k = _own_key(e)
        entry = dict(e)
        if k is not None and day_texts.get(k) and not _confirms(e, day_texts[k]):
            moved = _redirect(e, day_texts, own_keys)
            if moved is not None:
                entry["listed_day_index"] = e.get("source_day_index")
                entry["source_day_index"] = moved[1]
        out.append(entry)
    return out


def _group_by_day(itinerary: list[dict]) -> dict[Any, list[dict]]:
    by_day: dict[Any, list[dict]] = {}
    for e in itinerary:
        by_day.setdefault(e.get("day"), []).append(e)
    return by_day


def _own_key(entry: dict) -> Optional[tuple[str, int]]:
    tid, didx = entry.get("source_tour_id"), entry.get("source_day_index")
    if tid is None or didx is None:
        return None
    return str(tid), int(didx)


def _confirms(entry: dict, text: str) -> bool:
    keys = _stop_keys(entry)
    return not keys or _mentions(text, keys)


def _redirect(entry: dict, day_texts: dict[tuple[str, int], str],
              unavailable: set[tuple[str, int]]) -> Optional[tuple[str, int]]:
    """The nearest other day of the entry's tour that names its stop and is free (ties: earlier)."""
    own = _own_key(entry)
    keys = _stop_keys(entry)
    if own is None or not keys:
        return None
    tid, didx = own
    candidates = sorted(
        (abs(d - didx), d) for (t, d), text in day_texts.items()
        if t == tid and d != didx and (t, d) not in unavailable and text and _mentions(text, keys)
    )
    return (tid, candidates[0][1]) if candidates else None


def _norm(text: str) -> str:
    """Case-, accent- and punctuation-insensitive form, padded so a key matches whole words."""
    ascii_text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return f" {_NON_ALNUM.sub(' ', ascii_text.lower()).strip()} "


# A trailing word that names the kind of place, not the place: "Pashupatinath Temple" is written
# "Pashupatinath" in day texts, "Koh Phi Phi Don" as "Koh Phi Phi". Dropped as a second key only
# when what is left is still a distinctive name (>= 4 letters).
_GENERIC_SUFFIXES = {
    "temple", "fort", "fortress", "palace", "street", "market", "gompa", "monastery", "pagoda",
    "village", "island", "islands", "don", "lake", "pass", "park", "national", "museum", "square",
}


def _stop_keys(entry: dict) -> list[str]:
    keys: list[str] = []
    for field in ("name", "destination_name"):
        key = _norm(entry.get(field) or "")
        if not key.strip():
            continue
        keys.append(key)
        words = key.split()
        while words and words[-1] in _GENERIC_SUFFIXES:
            words.pop()
        head = " ".join(words)
        if len(head.replace(" ", "")) >= 4 and f" {head} " != key:
            keys.append(f" {head} ")
    return keys


def _mentions(text: str, keys: list[str]) -> bool:
    t = _norm(text)
    return any(k in t for k in keys)


def _strip_leading_day_header(text: str) -> str:
    """Remove a leading 'Day N —/:/- ' marker, keeping the rest (title +
    prose) intact so the trip's own day numbering is authoritative."""
    return _DAY_HEADER.sub("", text, count=1).strip()
