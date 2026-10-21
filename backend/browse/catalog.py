"""AA-812 — public catalog reads for the home, tour pages (PDP) and destination pages.

Every query here reads ONLY the CIS catalog read model (schema `catalog`, CIS migration 225,
contract AA-Ecosys/docs/contracts/catalog.tour.v1.json; ecosystem ADR 0001 decision 1). Never
gold_aa_internal / silver_aa_internal / acp_* — those are CIS internals.

Stateless and cacheable (same CDN TTL as the other browse reads): no per-visitor state, no model
call. Filtering that the views cannot express in SQL cheaply (travel month from the free-text
`best_time_to_go`) runs in Python over the country/duration-filtered rows; the catalog is a few
hundred tours, so this stays small.

Photos: catalog paths are relative to the CIS public API base ({base}{path}?size=large|small,
CIS migration 200). The responses carry absolute URLs so the frontend never needs the base.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any, Iterable, Optional

from backend import config

# Tours whose source is being retired stay reachable by URL (old links keep working) but are not
# listed, featured or put in the sitemap.
HIDDEN_LIFECYCLE = "retired"

SORTS = ("featured", "duration_asc", "duration_desc", "recent", "title")
DEFAULT_LIMIT = 24
MAX_LIMIT = 60
TOP_TOURS_PER_COUNTRY = 12
TOP_PLACES_PER_COUNTRY = 24
GALLERY_PHOTOS_PER_COUNTRY = 12

MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")


def slugify(text: Optional[str]) -> str:
    """Lowercase, non [a-z0-9] runs -> '-', trimmed. The same rule as catalog.v_tour.slug."""
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


# SQL twin of slugify() for the country column, so `sri-lanka` and `Sri Lanka` both match.
_COUNTRY_SLUG_SQL = "trim(BOTH '-' FROM regexp_replace(lower(t.country), '[^a-z0-9]+', '-', 'g'))"

CARD_COLUMNS = """
    t.source_id, t.tour_id::text AS tour_id, t.slug, t.title, t.subtitle, t.summary, t.country,
    t.duration_days, t.duration_raw, t.best_time_to_go, t.highlights, t.cover_photo_path,
    t.photo_count, t.lifecycle_stage, t.published_at, t.updated_at
"""

TOUR_CARDS_SQL = f"""
    SELECT {CARD_COLUMNS}
    FROM catalog.v_tour t
    WHERE ($1::text IS NULL OR {_COUNTRY_SLUG_SQL} = $1::text)
      AND ($2::int IS NULL OR t.duration_days >= $2::int)
      AND ($3::int IS NULL OR t.duration_days <= $3::int)
      AND t.lifecycle_stage IS DISTINCT FROM '{HIDDEN_LIFECYCLE}'
"""

TOUR_BY_SLUG_SQL = """
    SELECT t.source_system, t.source_id, t.tour_id::text AS tour_id, t.source_version, t.sku, t.slug,
           t.title, t.subtitle, t.summary, t.description, t.highlights, t.country, t.duration_raw,
           t.duration_days, t.activities, t.inclusions, t.exclusions, t.best_time_to_go, t.group_size,
           t.lifecycle_stage, t.itinerary_text, t.cover_photo_path, t.photo_count, t.published_at,
           t.updated_at, t.content_hash
    FROM catalog.v_tour t
    WHERE t.slug = $1 OR left(replace(t.source_id, '-', ''), 8) = $2
    ORDER BY (t.slug = $1) DESC
    LIMIT 1
"""

TOUR_DAYS_SQL = """
    SELECT day_number, title, body, places
    FROM catalog.v_tour_day WHERE tour_id = $1::uuid ORDER BY day_number
"""

TOUR_PLACES_SQL = """
    SELECT day_number, position, place_name, place_card_id::text AS place_card_id, place_display_name,
           locality, region, country, destination_id::text AS destination_id, destination_name, lat, lng,
           activity_types, atom_count, located_by, precision,
           segment, segment_basis, segment_from_destination_id::text AS segment_from_destination_id,
           segment_to_destination_id::text AS segment_to_destination_id, segment_km
    FROM catalog.v_tour_place WHERE tour_id = $1::uuid
    ORDER BY day_number NULLS LAST, position
"""
# AA-847 slice 6d (CIS 256, catalog.tour v1.2): the stretch of a LINE place its day follows is on v_tour_place
# (segment …); the line's own geometry for static maps (≤ 60 vertices) is v_destination.shape_lo, read by id.
SEGMENT_KEYS = ("segment_basis", "segment_from_destination_id", "segment_to_destination_id")
LINE_SHAPES_LO_SQL = """
    SELECT destination_id::text AS destination_id, shape_lo
    FROM catalog.v_destination
    WHERE destination_id = ANY($1::uuid[]) AND precision = 'line' AND shape_lo IS NOT NULL
"""

TOUR_PHOTOS_SQL = """
    SELECT photo_id::text AS photo_id, path, place_label, destination_id::text AS destination_id,
           width, height, position
    FROM catalog.v_tour_photo WHERE tour_id = $1::uuid ORDER BY position
"""

COUNTRIES_SQL = f"""
    SELECT t.country, count(*) AS tour_count, min(t.duration_days) AS min_days,
           max(t.duration_days) AS max_days,
           (array_agg(t.cover_photo_path ORDER BY t.photo_count DESC, t.published_at DESC)
                FILTER (WHERE t.cover_photo_path IS NOT NULL))[1] AS cover_photo_path,
           max(t.updated_at) AS updated_at
    FROM catalog.v_tour t
    WHERE t.country IS NOT NULL AND t.country <> ''
      AND t.lifecycle_stage IS DISTINCT FROM '{HIDDEN_LIFECYCLE}'
    GROUP BY t.country
    ORDER BY count(*) DESC, t.country
"""

# Places of the country's listed tours, by tour id (the ids come from TOUR_CARDS_SQL). A plain
# uuid = ANY(...) can be pushed into the security_barrier view; filtering v_tour_place through a
# join on the computed country slug would make it build every tour's places first (AA-812 review).
COUNTRY_PLACES_SQL = """
    SELECT p.destination_id::text AS destination_id, p.destination_name, p.lat, p.lng,
           p.tour_id::text AS tour_id, p.activity_types, p.located_by, p.precision
    FROM catalog.v_tour_place p
    WHERE p.tour_id = ANY($1::uuid[])
      AND p.destination_id IS NOT NULL AND p.lat IS NOT NULL AND p.lng IS NOT NULL
"""

DESTINATION_COVERS_SQL = """
    SELECT destination_id::text AS destination_id, cover_photo_path, photo_count, precision, shape_lo
    FROM catalog.v_destination WHERE destination_id = ANY($1::uuid[])
"""

DESTINATION_PHOTOS_SQL = """
    SELECT destination_id::text AS destination_id, photo_id::text AS photo_id, path, place_label,
           width, height, position
    FROM catalog.v_destination_photo
    WHERE destination_id = ANY($1::uuid[]) AND position <= 2
    ORDER BY position, destination_id
"""

# Cover fallback (AA-812 D4/D5): many tours have no matched AA photo yet (cover_photo_path NULL,
# CIS photo matching AA-708). Their card / hero then shows a photo of the tour's main destination,
# else of a destination in the tour's country. The tour read filters by uuid = ANY (pushed into the
# security_barrier view). The country read matches lower(country) like CIS does when it joins
# shared.destinations to a tour's country (migration 225, v_tour_place); v_destination is small.
TOUR_DESTINATIONS_SQL = """
    SELECT p.tour_id::text AS tour_id, p.destination_id::text AS destination_id, p.destination_name,
           p.day_number
    FROM catalog.v_tour_place p
    WHERE p.tour_id = ANY($1::uuid[]) AND p.destination_id IS NOT NULL
"""

COUNTRY_COVERS_SQL = """
    SELECT lower(d.country) AS country_key, d.name, d.cover_photo_path
    FROM catalog.v_destination d
    WHERE lower(d.country) = ANY($1::text[]) AND d.cover_photo_path IS NOT NULL
    ORDER BY lower(d.country), d.photo_count DESC, d.name
"""

SITEMAP_SQL = f"""
    SELECT t.slug, t.country, t.updated_at
    FROM catalog.v_tour t
    WHERE t.lifecycle_stage IS DISTINCT FROM '{HIDDEN_LIFECYCLE}'
    ORDER BY t.slug
"""


# --- small value helpers ----------------------------------------------------------------------

def photo_url(path: Optional[str], size: str = "large") -> Optional[str]:
    if not path:
        return None
    base = (config.CIS_PUBLIC_API_BASE or "").rstrip("/")
    sep = "&" if "?" in path else "?"
    return f"{base}{path}{sep}size={size}"


def _iso(v: Any) -> Any:
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return v


def _num(v: Any) -> Optional[float]:
    return None if v is None else float(v)


def _json(v: Any) -> Any:
    """jsonb arrives as str from asyncpg without a codec; decode it, keep anything else."""
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _str_list(v: Any) -> list[str]:
    """Highlights / activities as a list of non-empty strings, whatever JSON shape CIS stored."""
    v = _json(v)
    if v is None:
        return []
    if isinstance(v, str):
        parts = re.split(r"\n+|;\s*|•", v)
        return [p.strip(" -*\t") for p in parts if p.strip(" -*\t")]
    if isinstance(v, dict):
        v = list(v.values())
    if isinstance(v, (list, tuple)):
        out = []
        for item in v:
            if isinstance(item, dict):
                item = item.get("text") or item.get("title") or item.get("name")
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
        return out
    return []


_MONTH_RE = (r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
             r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?")
_TOKEN_RE = re.compile(rf"\b({_MONTH_RE})\b|(\bto\b|\btill\b|\buntil\b|\bthrough\b|[-–—])", re.I)
_ALL_YEAR_RE = re.compile(r"all[\s-]*year|year[\s-]*round|any\s*time|all\s+seasons", re.I)
# Clause breaks; a negating word also starts a new clause ("October to April but avoid Tet").
_CLAUSE_RE = re.compile(r"[.;,()\n]+|\s+(?=(?:but|except|excluding|avoid|avoiding|not)\b)", re.I)
_NEGATED_RE = re.compile(r"^\s*(?:but\s+)?(?:avoid|avoiding|not|except|excluding)\b", re.I)


def _clause_months(clause: str) -> set[int]:
    """Months a clause names: single months and ranges (a range wraps the year end)."""
    seq: list[Any] = []
    for m in _TOKEN_RE.finditer(clause):
        name, sep = m.group(1), m.group(2)
        if name:
            # "may" is a month only when written "May" (not the verb: "rain may fall").
            if name.lower() == "may" and not name[0].isupper():
                continue
            seq.append(MONTHS.index(name[:3].lower()) + 1)
        elif sep and seq and isinstance(seq[-1], int):
            seq.append("-")
    months: set[int] = set()
    i = 0
    while i < len(seq):
        m = seq[i]
        if isinstance(m, int) and i + 2 < len(seq) and seq[i + 1] == "-" and isinstance(seq[i + 2], int):
            k, b = m, seq[i + 2]
            while True:
                months.add(k)
                if k == b:
                    break
                k = k % 12 + 1
            i += 3
        else:
            if isinstance(m, int):
                months.add(m)
            i += 1
    return months


def months_from_text(text: Optional[str]) -> Optional[set[int]]:
    """Months (1-12) a free-text `best_time_to_go` covers, or None when it names no month.

    Handles "October to April", "Oct - Mar", "Nov–Feb and Jul–Aug", "March, April and May",
    "all year" / "year-round". A range wraps the year end (Nov to Feb = 11, 12, 1, 2). Clauses
    that start with "avoid" / "not" / "except" / "excluding" remove their months instead
    ("All year except July", "October to April, avoid June to August").
    """
    if not text:
        return None
    good: set[int] = set()
    bad: set[int] = set()
    all_year = False
    for clause in _CLAUSE_RE.split(text):
        if not clause or not clause.strip():
            continue
        if _NEGATED_RE.match(clause):
            bad |= _clause_months(clause)
        else:
            all_year = all_year or bool(_ALL_YEAR_RE.search(clause))
            good |= _clause_months(clause)
    if all_year or (bad and not good):
        good = set(range(1, 13))
    months = good - bad
    return months or None


def _card(r: Any) -> dict:
    return {
        "source_id": r["source_id"],
        "tour_id": r["tour_id"],
        "slug": r["slug"],
        "title": r["title"],
        "subtitle": r["subtitle"],
        "summary": r["summary"],
        "country": r["country"],
        "country_slug": slugify(r["country"]),
        "duration_days": r["duration_days"],
        "duration_raw": r["duration_raw"],
        "best_time_to_go": r["best_time_to_go"],
        "highlights": _str_list(r["highlights"])[:3],
        "cover_url": photo_url(r["cover_photo_path"], "large"),
        "cover_url_small": photo_url(r["cover_photo_path"], "small"),
        # "tour" = the tour's own cover; "destination" / "country" = a fallback place photo
        # (fill_fallback_covers), named by cover_place. None = no photo at all.
        "cover_source": "tour" if r["cover_photo_path"] else None,
        "cover_place": None,
        "photo_count": r["photo_count"] or 0,
        "lifecycle_stage": r["lifecycle_stage"],
        "published_at": _iso(r["published_at"]),
        "updated_at": _iso(r["updated_at"]),
    }


def _sort_key(sort: str):
    big = 10_000
    if sort == "duration_asc":
        return lambda c: (c["duration_days"] if c["duration_days"] is not None else big, c["title"] or "")
    if sort == "duration_desc":
        return lambda c: (-(c["duration_days"] or -1), c["title"] or "")
    if sort == "title":
        return lambda c: (c["title"] or "").lower()
    if sort == "recent":
        return lambda c: c["published_at"] or ""
    # featured: tours with photos first (they make the page), then the most recent. Not a
    # popularity claim — there is no sales/traffic signal in the catalog yet.
    return lambda c: (c["photo_count"] > 0, c["published_at"] or "")


def sort_cards(cards: list[dict], sort: str) -> list[dict]:
    reverse = sort in ("recent", "featured")
    return sorted(cards, key=_sort_key(sort), reverse=reverse)


# --- query parsing ----------------------------------------------------------------------------

class QueryError(ValueError):
    pass


def _one(query: dict, key: str) -> Optional[str]:
    v = query.get(key)
    if isinstance(v, list):
        v = v[0] if v else None
    if v is None:
        return None
    v = str(v).strip()
    return v or None


def _int(query: dict, key: str, lo: int, hi: int) -> Optional[int]:
    v = _one(query, key)
    if v is None:
        return None
    if not v.isdigit() or not lo <= int(v) <= hi:
        raise QueryError(f"{key} must be an integer {lo}-{hi}")
    return int(v)


def parse_list_query(query: dict) -> dict:
    sort = _one(query, "sort") or "featured"
    if sort not in SORTS:
        raise QueryError(f"sort must be one of {', '.join(SORTS)}")
    min_days = _int(query, "min_days", 1, 60)
    max_days = _int(query, "max_days", 1, 60)
    if min_days and max_days and min_days > max_days:
        raise QueryError("min_days must be <= max_days")
    country = _one(query, "country")
    return {
        "country": slugify(country) if country else None,
        "min_days": min_days,
        "max_days": max_days,
        "month": _int(query, "month", 1, 12),
        "sort": sort,
        "limit": _int(query, "limit", 1, MAX_LIMIT) or DEFAULT_LIMIT,
        "offset": _int(query, "offset", 0, 100_000) or 0,
    }


# --- cover fallback ---------------------------------------------------------------------------

def main_destinations(rows: Iterable[Any]) -> dict[str, list[tuple[str, Optional[str]]]]:
    """Per tour, its destinations ranked as the "main" one first: most itinerary days there, then
    the earliest day (a place with no day number last), then the name. -> {tour_id: [(id, name)]}"""
    by: dict[str, dict[str, dict]] = {}
    for r in rows:
        d = by.setdefault(r["tour_id"], {}).setdefault(
            r["destination_id"], {"name": r["destination_name"], "days": set(), "first": None})
        n = r["day_number"]
        if n is not None:
            d["days"].add(n)
            d["first"] = n if d["first"] is None else min(d["first"], n)
    out: dict[str, list[tuple[str, Optional[str]]]] = {}
    for tid, dests in by.items():
        ranked = sorted(dests.items(), key=lambda kv: (
            -len(kv[1]["days"]), kv[1]["first"] if kv[1]["first"] is not None else 10_000,
            kv[1]["name"] or ""))
        out[tid] = [(did, d["name"]) for did, d in ranked]
    return out


async def fill_fallback_covers(items: list[dict], *, pool: Any) -> None:
    """Give tours without their own cover a destination photo, in place (AA-812 D4/D5).

    Order: the cover of the tour's main destination (catalog.v_tour_place -> v_destination), else
    the best-covered destination of the tour's country (v_destination.country, case-insensitive). Sets
    cover_url (+ cover_url_small when the item has it), cover_source ("destination" | "country")
    and cover_place (the destination's name), so the page labels it a destination photo and never
    presents it as a photo of the tour. Items that already have a cover are not touched; when no
    place photo exists the item keeps cover_url None (the placeholder)."""
    missing = [it for it in items if not it.get("cover_url")]
    if not missing:
        return

    def put(it: dict, path: str, source: str, place: Optional[str]) -> None:
        it["cover_url"] = photo_url(path, "large")
        if "cover_url_small" in it:
            it["cover_url_small"] = photo_url(path, "small")
        it["cover_source"] = source
        it["cover_place"] = place

    ranked = main_destinations(await pool.fetch(TOUR_DESTINATIONS_SQL, [it["tour_id"] for it in missing]))
    ids = sorted({did for dests in ranked.values() for did, _ in dests})
    covers = ({r["destination_id"]: r["cover_photo_path"]
               for r in await pool.fetch(DESTINATION_COVERS_SQL, ids) if r["cover_photo_path"]}
              if ids else {})
    for it in missing:
        hit = next(((did, name) for did, name in ranked.get(it["tour_id"], []) if did in covers), None)
        if hit:
            put(it, covers[hit[0]], "destination", hit[1])

    countries = sorted({it["country"].lower() for it in missing if not it.get("cover_url") and it.get("country")})
    if not countries:
        return
    best: dict[str, Any] = {}
    for r in await pool.fetch(COUNTRY_COVERS_SQL, countries):
        best.setdefault(r["country_key"], r)  # rows come best first per country
    for it in missing:
        r = None if it.get("cover_url") else best.get((it.get("country") or "").lower())
        if r:
            put(it, r["cover_photo_path"], "country", r["name"])


# --- resources --------------------------------------------------------------------------------

async def list_tours(q: dict, *, pool: Any) -> dict:
    """Tour cards, filtered (country, duration range, travel month) and sorted, one page."""
    rows = await pool.fetch(TOUR_CARDS_SQL, q["country"], q["min_days"], q["max_days"])
    cards = [_card(r) for r in rows]
    if q["month"]:
        # A tour whose best time names no month is not excluded: unknown is not "wrong season".
        cards = [c for c in cards
                 if (m := months_from_text(c["best_time_to_go"])) is None or q["month"] in m]
    cards = sort_cards(cards, q["sort"])
    page = cards[q["offset"]: q["offset"] + q["limit"]]
    await fill_fallback_covers(page, pool=pool)
    return {
        "filters": {k: q[k] for k in ("country", "min_days", "max_days", "month", "sort")},
        "total": len(cards),
        "limit": q["limit"],
        "offset": q["offset"],
        "tours": page,
    }


async def tour_detail(slug: str, *, pool: Any) -> Optional[dict]:
    """One tour as catalog.tour v1 (+ photo URLs). Matches the slug, else the slug's source-id
    suffix (a title change moves the slug; `slug` in the answer is the current one, so the page
    can redirect). None when the tour is not in the catalog."""
    slug = (slug or "").strip().lower()
    suffix = slug.rsplit("-", 1)[-1] if re.fullmatch(r".*-[0-9a-f]{8}", slug) else ""
    t = await pool.fetchrow(TOUR_BY_SLUG_SQL, slug, suffix)
    if t is None:
        return None
    tour_id = t["tour_id"]
    days = await pool.fetch(TOUR_DAYS_SQL, tour_id)
    places = await pool.fetch(TOUR_PLACES_SQL, tour_id)
    photos = await pool.fetch(TOUR_PHOTOS_SQL, tour_id)
    line_ids = sorted({p["destination_id"] for p in places if _geo(p)["precision"] == "line" and p["destination_id"]})
    shapes = {r["destination_id"]: _json(r["shape_lo"])
              for r in await pool.fetch(LINE_SHAPES_LO_SQL, line_ids)} if line_ids else {}
    out = {
        "contract": "catalog.tour",
        "version": 1.2,
        "source_system": t["source_system"],
        "source_id": t["source_id"],
        "tour_id": tour_id,
        "source_version": t["source_version"],
        "sku": t["sku"],
        "slug": t["slug"],
        "title": t["title"],
        "subtitle": t["subtitle"],
        "summary": t["summary"],
        "description": t["description"],
        "highlights": _str_list(t["highlights"]),
        "country": t["country"],
        "country_slug": slugify(t["country"]),
        "duration_raw": t["duration_raw"],
        "duration_days": t["duration_days"],
        "activities": _str_list(t["activities"]),
        "inclusions": t["inclusions"],
        "exclusions": t["exclusions"],
        "best_time_to_go": t["best_time_to_go"],
        "group_size": t["group_size"],
        "lifecycle_stage": t["lifecycle_stage"],
        # Shown by the page when `days` is empty (a legacy, non-canonical itinerary text).
        "itinerary_text": t["itinerary_text"],
        "cover_url": photo_url(t["cover_photo_path"], "large"),
        "cover_source": "tour" if t["cover_photo_path"] else None,
        "cover_place": None,
        "photo_count": t["photo_count"] or 0,
        "published_at": _iso(t["published_at"]),
        "updated_at": _iso(t["updated_at"]),
        "content_hash": t["content_hash"],
        "days": [
            {"day_number": d["day_number"], "title": d["title"], "body": d["body"],
             "places": list(d["places"] or [])}
            for d in days
        ],
        "places": [
            {k: p[k] for k in ("day_number", "position", "place_name", "place_card_id",
                               "place_display_name", "locality", "region", "country",
                               "destination_id", "destination_name", "atom_count")}
            | {"lat": _num(p["lat"]), "lng": _num(p["lng"]),
               "activity_types": list(p["activity_types"] or []),
               # AA-847 (ADR 0007): 'area' is drawn as an area; 'geoapify' needs the attribution
               **_geo(p),
               # AA-847 slice 6d (catalog.tour v1.2): a 'line' place is drawn as a line, never a pin
               **_segment(p, shapes)}
            for p in places
        ],
        "photos": [
            {"photo_id": p["photo_id"], "path": p["path"], "place_label": p["place_label"],
             "destination_id": p["destination_id"], "width": p["width"], "height": p["height"],
             "position": p["position"], "url": photo_url(p["path"], "large"),
             "url_small": photo_url(p["path"], "small")}
            for p in photos
        ],
    }
    # The hero falls back to a destination photo only when the tour has no photo at all.
    if not out["photos"]:
        await fill_fallback_covers([out], pool=pool)
    return out


async def list_countries(*, pool: Any) -> dict:
    rows = await pool.fetch(COUNTRIES_SQL)
    # A country none of whose tours has a photo shows its best-covered destination instead.
    bare = sorted({r["country"].lower() for r in rows if not r["cover_photo_path"]})
    fallback: dict[str, str] = {}
    if bare:
        for r in await pool.fetch(COUNTRY_COVERS_SQL, bare):
            fallback.setdefault(r["country_key"], r["cover_photo_path"])
    out = []
    for r in rows:
        path = r["cover_photo_path"] or fallback.get(r["country"].lower())
        out.append({"country": r["country"], "slug": slugify(r["country"]), "tour_count": r["tour_count"],
                    "min_days": r["min_days"], "max_days": r["max_days"],
                    "cover_url": photo_url(path, "large"), "cover_url_small": photo_url(path, "small"),
                    "updated_at": _iso(r["updated_at"])})
    return {"countries": out}


def _geo(r: Any) -> dict:
    """``located_by`` / ``precision`` of a v_tour_place row (None when the row has no such column)."""
    keys = r.keys() if hasattr(r, "keys") else ()
    return {k: (r[k] if k in keys else None) for k in ("located_by", "precision")}


def _segment(r: Any, shapes: dict) -> dict:
    """catalog.tour v1.2 segment fields of a v_tour_place row (+ the line's shape_lo); {} on a row without them."""
    keys = r.keys() if hasattr(r, "keys") else ()
    if "segment" not in keys:
        return {}
    out = {"segment": _json(r["segment"]), "segment_km": _num(r["segment_km"])}
    out.update({k: r[k] for k in SEGMENT_KEYS})
    if r["precision"] == "line":
        out["shape_lo"] = shapes.get(r["destination_id"])
    return out


def aggregate_places(rows: Iterable[Any], limit: int = TOP_PLACES_PER_COUNTRY) -> list[dict]:
    """Group place rows of a country's tours by destination: how many tours pass, top activities."""
    by: dict[str, dict] = {}
    for r in rows:
        d = by.setdefault(r["destination_id"], {
            "destination_id": r["destination_id"], "name": r["destination_name"],
            "lat": _num(r["lat"]), "lng": _num(r["lng"]), "_tours": set(), "_acts": {}, **_geo(r),
        })
        d["_tours"].add(r["tour_id"])
        for a in r["activity_types"] or []:
            d["_acts"][a] = d["_acts"].get(a, 0) + 1
    out = []
    for d in by.values():
        acts = sorted(d.pop("_acts").items(), key=lambda kv: (-kv[1], kv[0]))
        d["tour_count"] = len(d.pop("_tours"))
        d["activity_types"] = [a for a, _ in acts[:4]]
        out.append(d)
    out.sort(key=lambda d: (-d["tour_count"], d["name"] or ""))
    return out[:limit]


async def destination_page(country: str, *, pool: Any) -> Optional[dict]:
    """Country page data: stats, top tours, place highlights with lat/lng, photos.

    No editorial intro exists in the catalog yet (CIS has no country content in contract v1), so
    the answer carries facts only (counts, day range, top activities); the page writes its intro
    from those. None when the country has no listed tour."""
    cslug = slugify(country)
    if not cslug:
        return None
    rows = await pool.fetch(TOUR_CARDS_SQL, cslug, None, None)
    if not rows:
        return None
    cards = sort_cards([_card(r) for r in rows], "featured")
    days = [c["duration_days"] for c in cards if c["duration_days"]]
    place_rows = await pool.fetch(COUNTRY_PLACES_SQL, [c["tour_id"] for c in cards])
    places = aggregate_places(place_rows)
    ids = [p["destination_id"] for p in places]
    covers = {r["destination_id"]: r for r in await pool.fetch(DESTINATION_COVERS_SQL, ids)} if ids else {}
    for p in places:
        c = covers.get(p["destination_id"])
        p["cover_url"] = photo_url(c["cover_photo_path"], "small") if c else None
        # AA-847 slice 6d: a line place (river, road, trail) is drawn from its shape, never a pin
        if c is not None and "shape_lo" in c.keys() and p.get("precision") == "line":
            p["shape_lo"] = _json(c["shape_lo"])
    photos = []
    if ids:
        rank = {d: i for i, d in enumerate(ids)}  # gallery follows the place ranking
        prow = sorted(await pool.fetch(DESTINATION_PHOTOS_SQL, ids),
                      key=lambda r: (r["position"], rank.get(r["destination_id"], 1_000)))
        seen: set[str] = set()
        for r in prow:
            if r["photo_id"] in seen:
                continue
            seen.add(r["photo_id"])
            photos.append({"photo_id": r["photo_id"], "destination_id": r["destination_id"],
                           "place_label": r["place_label"], "width": r["width"], "height": r["height"],
                           "url": photo_url(r["path"], "large"), "url_small": photo_url(r["path"], "small")})
            if len(photos) == GALLERY_PHOTOS_PER_COUNTRY:
                break
    acts: dict[str, int] = {}
    for r in place_rows:
        for a in r["activity_types"] or []:
            acts[a] = acts.get(a, 0) + 1
    name = cards[0]["country"]
    # Hero: a tour's own cover, else the first place photo of the gallery (taken before the
    # listed tours get their destination fallbacks, so a tour photo always wins).
    hero = next((c["cover_url"] for c in cards if c["cover_url"]), None) or (photos[0]["url"] if photos else None)
    top = cards[:TOP_TOURS_PER_COUNTRY]
    await fill_fallback_covers(top, pool=pool)
    return {
        "country": name,
        "slug": cslug,
        "tour_count": len(cards),
        "min_days": min(days) if days else None,
        "max_days": max(days) if days else None,
        "top_activities": [a for a, _ in sorted(acts.items(), key=lambda kv: (-kv[1], kv[0]))[:6]],
        "cover_url": hero,
        "tours": top,
        "places": places,
        "photos": photos,
    }


async def sitemap(*, pool: Any) -> dict:
    rows = await pool.fetch(SITEMAP_SQL)
    countries: dict[str, dict] = {}
    for r in rows:
        if r["country"]:
            s = slugify(r["country"])
            c = countries.setdefault(s, {"slug": s, "country": r["country"], "updated_at": None})
            u = _iso(r["updated_at"])
            if u and (c["updated_at"] is None or u > c["updated_at"]):
                c["updated_at"] = u
    return {
        "tours": [{"slug": r["slug"], "updated_at": _iso(r["updated_at"])} for r in rows],
        "countries": sorted(countries.values(), key=lambda c: c["slug"]),
    }
