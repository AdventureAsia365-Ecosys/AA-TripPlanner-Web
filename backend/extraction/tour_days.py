"""AA-675 — day-by-day skeleton of every published AA tour (tripplanner.tour_day).

For each (tour, itinerary day): the title, where the day starts and ends, where the traveller
sleeps (`overnight_place`, the anchor the Tour Graph links days and tours on, AA-673) and the
other named places. Source: `gold_aa_internal.published_tours.aa_itineraries`, whose text is a
series of "Day N — title" blocks.

Measured on Dev (29/09/2026, 121 active tours, 1,489 days): the rules below find a start/end or
an overnight place for 55% of days, and an explicit "overnight in X" for only 21%. So:

  1. parse_days()       split the text into day blocks (pure);
  2. rule_day()         "A to B" titles and "overnight/stay in X" sentences (pure);
  3. the tp_extract model reads the whole tour once and returns every day as JSON;
  4. ground_llm_day()   every place the model returns must appear in THAT day's own text,
                        otherwise it is dropped (the model never adds a place);
  5. merge_day()        model value when grounded, else the rule value;
  6. persist            upsert per (tour, day), geocode the overnight place into
                        shared.destinations, delete days the text no longer has.

A tour whose itinerary text is unchanged (same sha256) is skipped, so re-runs after each CIS
rerun wave only pay for tours that changed. Runs inside the assembly Lambda (direct invoke, see
handler.py), which has the Bedrock satellite role; one tour per invoke fits the 60 s timeout.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

# "Day 3 — Stok to Sakti", "Day 4-5: Trek to Base Camp", "Day 12 - Departure"
_DAY_HEAD = re.compile(
    r"^\s*Day\s+(\d{1,2})(?:\s*[-–—&]\s*(\d{1,2}))?\s*[—–:\-]\s*(.+?)\s*$", re.M | re.I)
# "Stok to Sakti via Thiksay—48 Kilometers" -> ("Stok", "Sakti")
_A_TO_B = re.compile(
    r"^(?:(?:drive|trek|fly|ride|cycle|transfer|travel|walk|hike)\s+(?:from\s+)?)?"
    r"(?P<a>[A-Z][^,:;—–]*?)\s+to\s+(?P<b>[A-Z][^,:;—–(]*?)"
    r"(?:\s+via\s+[^—–]*)?(?:\s*[—–-]\s*.*)?$", re.I)
# "Overnight stay in Sakti.", "overnight at Hotel X", "Stay in Leh", "Night in the camp"
# Keywords are case-insensitive; the place itself must start with a capital letter.
_OVERNIGHT = re.compile(
    r"\b(?i:overnight(?:\s+stay)?|stay|night|sleep)\s+(?i:is\s+)?(?i:in|at)\s+(?i:a\s+|an\s+|the\s+)?"
    r"(?P<p>[A-Z][\w'’.\- ]*?)(?=\s*(?:[.,;:()]|$|\s+(?:with|near|before|after|where|and|for|on)\b))",
    re.M)
_TRAILING_NOISE = re.compile(r"\s*(?:\d+\s*(?:km|kilometers|kilometres|miles|hours?|hrs?)\b.*)$", re.I)


@dataclass
class DayBlock:
    day_from: int
    day_to: int
    title: str
    body: str

    @property
    def text(self) -> str:
        return f"{self.title}\n{self.body}"


@dataclass
class DayRecord:
    day_index: int
    title: str
    start_place: Optional[str] = None
    end_place: Optional[str] = None
    overnight_place: Optional[str] = None
    places: list[str] = field(default_factory=list)
    extracted_by: str = "rule"


def source_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def parse_days(text: str) -> list[DayBlock]:
    """Split an itinerary into "Day N — title" blocks. A range ("Day 4-5") covers both days."""
    heads = list(_DAY_HEAD.finditer(text or ""))
    blocks: list[DayBlock] = []
    for i, m in enumerate(heads):
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else start
        if end < start:
            end = start
        body_end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        blocks.append(DayBlock(start, end, m.group(3).strip(), text[m.end():body_end].strip()))
    return blocks


def _clean(place: Optional[str]) -> Optional[str]:
    if not place:
        return None
    p = _TRAILING_NOISE.sub("", place).strip(" .,-–—'\"")
    return p or None


def rule_day(block: DayBlock) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """(start, end, overnight) found by the rules alone; None where they find nothing."""
    start = end = None
    m = _A_TO_B.match(block.title)
    if m:
        start, end = _clean(m.group("a")), _clean(m.group("b"))
    o = _OVERNIGHT.search(block.body) or _OVERNIGHT.search(block.title)
    overnight = _clean(o.group("p")) if o else None
    return start, end, overnight


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def grounded(place: Optional[str], text: str) -> bool:
    """True when `place` (case/punctuation-insensitive) appears in `text`."""
    if not place:
        return False
    p = _norm(place)
    return bool(p) and f" {p} " in f" {_norm(text)} "


SYSTEM_PROMPT = (
    "You read an Adventure Asia tour itinerary and return, for every day, where the day starts, "
    "where it ends, where the traveller sleeps, and the other named places visited. You copy "
    "place names exactly as they are written in that day's text. You never add a place that is "
    "not written in that day's text, and you never guess."
)


def build_user_prompt(tour_name: str, blocks: list[DayBlock], max_body_chars: int = 900) -> str:
    days = "\n\n".join(
        f"[Day {b.day_from}{f'-{b.day_to}' if b.day_to != b.day_from else ''}] {b.title}\n"
        f"{b.body[:max_body_chars]}"
        for b in blocks)
    return f"""Tour: {tour_name}

{days}

Return ONLY a JSON object:
{{"days": [{{"day": <first day number of the block>, "start": <place or null>, "end": <place or null>,
            "overnight": <place or null>, "places": [<other named places visited>]}}]}}

Rules:
- One entry per [Day ...] block above, in order.
- "overnight" is the town/camp/lodge where the traveller sleeps that night. Use null on the last
  day when the tour ends (departure) or when the text does not say.
- If the day stays in one place, start and end are that place.
- Every name must be copied from that day's own text. Do not use a place from another day."""


def parse_llm_days(raw: str) -> dict[int, dict]:
    """{day_from: {...}} from the model's JSON (code fences tolerated). Raises ValueError."""
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.S)
    data = json.loads(s)
    days = data.get("days") if isinstance(data, dict) else data
    if not isinstance(days, list):
        raise ValueError("model output has no 'days' list")
    out: dict[int, dict] = {}
    for d in days:
        if isinstance(d, dict) and isinstance(d.get("day"), int):
            out[d["day"]] = d
    return out


def ground_llm_day(d: dict, block: DayBlock) -> dict:
    """Keeps only the places that appear in the block's own text."""
    text = block.text

    def one(v):
        return v.strip() if isinstance(v, str) and grounded(v, text) else None

    places = d.get("places") if isinstance(d.get("places"), list) else []
    return {
        "start": one(d.get("start")), "end": one(d.get("end")), "overnight": one(d.get("overnight")),
        "places": [p.strip() for p in places if isinstance(p, str) and grounded(p, text)],
    }


def merge_day(block: DayBlock, rule: tuple, llm: Optional[dict]) -> list[DayRecord]:
    """One record per day the block covers. Grounded model values win; rules fill the gaps."""
    r_start, r_end, r_over = rule
    g = llm or {}
    start = g.get("start") or r_start
    end = g.get("end") or r_end
    overnight = g.get("overnight") or r_over
    used_llm = any(g.get(k) for k in ("start", "end", "overnight")) or bool(g.get("places"))
    used_rule = any((r_start, r_end, r_over))
    by = "rule+llm" if used_llm and used_rule else ("llm" if used_llm else "rule")
    places = [p for p in dict.fromkeys(g.get("places") or []) if p not in (start, end, overnight)]
    return [DayRecord(day, block.title, start, end, overnight, places, by)
            for day in range(block.day_from, block.day_to + 1)]


def needs_model(blocks: list[DayBlock], rules: list[tuple]) -> bool:
    """The model is only called when some day has no overnight place from the rules."""
    return any(r[2] is None for b, r in zip(blocks, rules) if b is not blocks[-1] or len(blocks) == 1)


GenerateFn = Callable[[str, str], Awaitable[str]]          # (system, user) -> raw text


async def extract_tour(tour: dict, generate: Optional[GenerateFn]) -> list[DayRecord]:
    """Day records for one tour dict {tour_id, aa_name, aa_itineraries}. Pure except `generate`.
    A model failure falls back to the rules alone (never raises for a bad model answer)."""
    blocks = parse_days(tour.get("aa_itineraries") or "")
    if not blocks:
        return []
    rules = [rule_day(b) for b in blocks]
    llm_by_day: dict[int, dict] = {}
    if generate is not None and needs_model(blocks, rules):
        try:
            raw = await generate(SYSTEM_PROMPT, build_user_prompt(tour.get("aa_name") or "", blocks))
            parsed = parse_llm_days(raw)
            llm_by_day = {b.day_from: ground_llm_day(parsed[b.day_from], b)
                          for b in blocks if b.day_from in parsed}
        except Exception as e:  # noqa: BLE001 — rules alone are still a usable answer
            print(f"[tour_days] model step failed for {tour.get('tour_id')}: {str(e)[:200]}")
    records: list[DayRecord] = []
    for b, r in zip(blocks, rules):
        records.extend(merge_day(b, r, llm_by_day.get(b.day_from)))
    return records


# A night on a train, bus, ferry or flight has no place to put on the map (29/09/2026: "overnight
# bullet train" was geocoded to a point in the sea). The text stays in overnight_place.
_TRANSIT = re.compile(r"\b(train|sleeper|flight|plane|bus|ferry|in transit)\b", re.I)


def is_transit(place: Optional[str]) -> bool:
    return bool(place and _TRANSIT.search(place))


# A hotel, lodge or camp is not on the map by name (Mapbox and the model both know towns, not
# guesthouses): anchor the night on the day's end place instead. "Base camp" is a place.
_LODGING = re.compile(
    r"\b(hotel|lodge|(?<!base )camp|guest ?house|home ?stay|resort|hostel|farm ?stay|farmstead|inn|manor|"
    r"tea ?house|ger)\b", re.I)


def anchor_place(overnight: Optional[str], end: Optional[str]) -> Optional[str]:
    """Where a night sits on the map: the overnight place, else (lodging) the day's end place.
    None for a night in transit or with nothing usable."""
    for p in (overnight, end):
        if p and p.strip() and not is_transit(p) and not _LODGING.search(p):
            return p.strip()
    return None


LinkFn = Callable[[list[str], str, str], Awaitable[dict]]  # (places, tour country, tour name) -> {place: dest id}


async def persist_tour(db, tour_id: str, text_hash: str, records: list[DayRecord],
                       country: str, link: Optional[LinkFn], tour_name: str = "") -> dict:
    """Upserts the tour's days, links each night's anchor place once, deletes stale days."""
    anchors = {r.day_index: anchor_place(r.overnight_place, r.end_place) for r in records}
    places = list(dict.fromkeys(a for a in anchors.values() if a))
    dest_ids: dict = {}
    if places and link is not None:
        try:
            dest_ids = await link(places, country, tour_name)
        except Exception as e:  # noqa: BLE001 — keep the days, relink later
            print(f"[tour_days] link failed for {tour_id}: {str(e)[:120]}")
    for rec in records:
        await db.execute(
            """
            INSERT INTO tripplanner.tour_day
                (source_tour_id, day_index, title, start_place, end_place, overnight_place,
                 overnight_destination_id, places, extracted_by, source_hash, extracted_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7::uuid, $8, $9, $10, now())
            ON CONFLICT (source_tour_id, day_index) DO UPDATE SET
                title = excluded.title, start_place = excluded.start_place,
                end_place = excluded.end_place, overnight_place = excluded.overnight_place,
                overnight_destination_id = excluded.overnight_destination_id,
                places = excluded.places, extracted_by = excluded.extracted_by,
                source_hash = excluded.source_hash, extracted_at = now()
            """,
            tour_id, rec.day_index, rec.title, rec.start_place, rec.end_place, rec.overnight_place,
            dest_ids.get(anchors[rec.day_index] or ""), rec.places, rec.extracted_by, text_hash)
    max_day = max((r.day_index for r in records), default=0)
    await db.execute(
        "DELETE FROM tripplanner.tour_day WHERE source_tour_id = $1 AND day_index > $2", tour_id, max_day)
    return {"days": len(records), "overnight": sum(1 for r in records if r.overnight_place),
            "anchored": sum(1 for a in anchors.values() if a),
            "linked": sum(1 for d, a in anchors.items() if a and dest_ids.get(a)),
            "by": {k: sum(1 for r in records if r.extracted_by == k) for k in ("rule", "llm", "rule+llm")}}


async def fetch_catalog(db, sql: str, *args) -> list:
    """Reads CIS catalog tables (published_tours, raw_tours). Both enforce tenant RLS
    (tenant_id = current_setting('app.tenant_id')), so without the AA tenant they return no rows.
    SET LOCAL inside a transaction, as master_content.py does, so it never leaks to other queries
    on a pooled connection."""
    from backend import config

    async with db.transaction():
        await db.execute("SELECT set_config('app.tenant_id', $1, true)", config.MASTER_CONTENT_TENANT_ID)
        return await db.fetch(sql, *args)


ACTIVE_TOURS_SQL = """
    SELECT pt.tour_id::text AS tour_id, pt.aa_name, pt.aa_itineraries, rt.country
    FROM gold_aa_internal.published_tours pt
    JOIN silver_aa_internal.raw_tours rt ON rt.tour_id = pt.tour_id
    WHERE pt.master_status = 'active'
"""


async def run_tours(db, tour_ids: Optional[list[str]], generate: Optional[GenerateFn],
                    link: Optional[LinkFn], force: bool = False) -> dict:
    """Extracts the given active tours (all when `tour_ids` is None). Skips a tour whose text hash
    matches what tour_day already holds, unless `force`."""
    sql = ACTIVE_TOURS_SQL + (" AND pt.tour_id::text = ANY($1::text[])" if tour_ids else "")
    tours = await (fetch_catalog(db, sql, tour_ids) if tour_ids else fetch_catalog(db, sql))
    done, skipped, out = 0, 0, {}
    for t in tours:
        h = source_hash(t["aa_itineraries"] or "")
        if not force:
            prev = await db.fetchval(
                "SELECT min(source_hash) FROM tripplanner.tour_day WHERE source_tour_id = $1", t["tour_id"])
            if prev == h:
                skipped += 1
                continue
        records = await extract_tour(dict(t), generate)
        out[t["tour_id"]] = await persist_tour(db, t["tour_id"], h, records, t["country"] or "", link,
                                               t["aa_name"] or "")
        done += 1
    return {"tours": len(tours), "extracted": done, "skipped_unchanged": skipped, "per_tour": out}


async def list_tours(db) -> list[dict]:
    """Active tours with `stale` = True when tour_day is missing or was built from other text."""
    rows = await fetch_catalog(db, ACTIVE_TOURS_SQL)
    out = []
    for r in rows:
        prev = await db.fetchval(
            "SELECT min(source_hash) FROM tripplanner.tour_day WHERE source_tour_id = $1", r["tour_id"])
        out.append({"tour_id": r["tour_id"], "country": r["country"], "name": r["aa_name"],
                    "stale": prev != source_hash(r["aa_itineraries"] or "")})
    return out


async def prune(db, include_components: bool = False) -> dict:
    """Removes tour_day rows of tours that are no longer active. `include_components` also removes
    their itinerary_components — opt-in, because saved trips reference component ids in their
    events (no FK), so old drafts would lose those stops.

    The active set is read first (tenant-scoped) and must not be empty: an empty read means RLS or
    a CIS reset, and deleting "everything not active" would then wipe every row."""
    active = [r["tour_id"] for r in await fetch_catalog(db, ACTIVE_TOURS_SQL)]
    if not active:
        return {"error": "no active tours visible; refusing to prune"}
    out = {"active_tours": len(active), "tour_day": await db.execute(
        "DELETE FROM tripplanner.tour_day WHERE NOT (source_tour_id = ANY($1::text[]))", active)}
    if include_components:
        out["itinerary_components"] = await db.execute(
            "DELETE FROM tripplanner.itinerary_components WHERE NOT (source_tour_id = ANY($1::text[]))", active)
    return out


async def relink(db, generate, search, after: str = "", limit: int = 40) -> dict:
    """Re-locates every night anchor of the active tours from scratch (model + Mapbox, see
    locate.py) and relinks tour_day, one page of anchor names at a time (the Lambda's 60 s).
    Page with the returned `last`; the first page also unlinks nights that have no anchor."""
    from backend.extraction import locate

    tours = {r["tour_id"]: r for r in await fetch_catalog(db, ACTIVE_TOURS_SQL)}
    if not tours:
        return {"error": "no active tours visible"}
    groups: dict[str, dict] = {}
    unanchored = []
    for r in await db.fetch("SELECT source_tour_id, day_index, overnight_place, end_place FROM tripplanner.tour_day"):
        if r["source_tour_id"] not in tours:
            continue
        a = anchor_place(r["overnight_place"], r["end_place"])
        if a is None:
            unanchored.append((r["source_tour_id"], r["day_index"]))
            continue
        g = groups.setdefault(a.lower(), {"name": a, "days": [], "tour": r["source_tour_id"]})
        g["days"].append((r["source_tour_id"], r["day_index"]))
    keys = sorted(k for k in groups if k > after.lower())
    page = keys[:limit]
    set_sql = ("UPDATE tripplanner.tour_day SET overnight_destination_id = $3 "
               "WHERE source_tour_id = $1 AND day_index = $2")
    if not after and unanchored:
        await db.executemany(set_sql, [(t, d, None) for t, d in unanchored])
    items = [(groups[k]["name"], tours[groups[k]["tour"]]["country"] or "",
              tours[groups[k]["tour"]]["aa_name"] or "") for k in page]
    found = await locate.locate_many(items, generate, search)
    out = {"anchors": len(groups), "unanchored_nights": len(unanchored), "page": len(page),
           "mapbox": 0, "llm": 0, "unlinked": 0, "last": page[-1] if page else None,
           "done": len(keys) <= limit, "samples": []}
    for k in page:
        name = groups[k]["name"]
        loc = found.get(name)
        dest = await locate.upsert(db, name, loc) if loc else None
        await db.executemany(set_sql, [(t, d, dest) for t, d in groups[k]["days"]])
        if loc:
            out[loc.source] += 1
        else:
            out["unlinked"] += 1
        if len(out["samples"]) < 12:
            out["samples"].append([name, round(loc.lat, 3), round(loc.lng, 3), loc.country, loc.source]
                                  if loc else [name, None])
    return out
