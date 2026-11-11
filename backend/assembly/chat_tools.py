"""AA-1041 slice 2 — the read-only catalog tools of the planning chat (backend/assembly/plan_chat.py).

The chat model never writes a stop. It asks for one of these tools, the server runs it over the same CIS catalog views
the planner already reads (``catalog.v_tour``, ``catalog.v_tour_component``, ``catalog.v_destination`` and the tour
graph of ``backend/browse/routes.py``), and every tour or experience it returns gets a short reference (``t1``,
``c4``) in a per-turn :class:`Refs` table. A proposal can only name those references (or the trip's own stops), so it
can never carry a place, a tour or an id the catalog did not return on this turn.

Every read is bounded (a few rows), has no traveller data in it and no free text from the model reaches SQL except as
a bound parameter (search words are reduced to letters and digits first). The views are live-only and need no tenant:
the same rows every planner sees.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Optional

from backend.browse import routes as routes_mod

TOOL_NAMES = ("route_options", "find_tours", "tour_route", "find_experiences")
MAX_TOURS = 6
MAX_EXPERIENCES = 8
MAX_ROUTE_OPTIONS = 3
MAX_DAYS = 30
MAX_TERMS = 3

# The countries AA sells tours in (live tours only), most tours first.
COUNTRIES_SQL = """
    SELECT t.country, count(*)::int AS tour_count, min(t.duration_days) AS min_days, max(t.duration_days) AS max_days
    FROM catalog.v_tour t
    WHERE t.country IS NOT NULL AND t.country <> '' AND t.lifecycle_stage IS DISTINCT FROM 'retired'
    GROUP BY t.country
    ORDER BY count(*) DESC, t.country
"""

# Live tours by country, words of the title / summary and a longest length. $2 = search words (already reduced to
# letters and digits, never a pattern character), NULL or empty = any.
FIND_TOURS_SQL = """
    SELECT t.tour_id::text AS tour_id, t.title, t.country, t.duration_days
    FROM catalog.v_tour t
    WHERE t.lifecycle_stage IS DISTINCT FROM 'retired'
      AND ($1::text IS NULL OR lower(t.country) = lower($1::text))
      AND (cardinality(coalesce($2::text[], '{}')) = 0 OR (
            SELECT bool_and((t.title || ' ' || coalesce(t.summary, '')) ILIKE '%' || w || '%')
            FROM unnest($2::text[]) AS w))
      AND ($3::int IS NULL OR t.duration_days <= $3::int)
    ORDER BY t.duration_days NULLS LAST, t.title
    LIMIT $4::int
"""

# Planner experiences (the components a trip pins) whose name, activity or place matches every word, one per name and
# place. Same "pinnable" rule as the route reads (embedding IS NOT NULL).
FIND_EXPERIENCES_SQL = """
    SELECT DISTINCT ON (lower(c.name), d.name)
           c.component_id::text AS component_id, c.name, c.activity, c.country, d.name AS place
    FROM catalog.v_tour_component c
    JOIN catalog.v_destination d ON d.destination_id = c.destination_id
    WHERE c.embedding IS NOT NULL
      AND ($1::text IS NULL OR lower(c.country) = lower($1::text))
      AND (SELECT bool_and((c.name || ' ' || coalesce(c.activity, '') || ' ' || d.name) ILIKE '%' || w || '%')
           FROM unnest($2::text[]) AS w)
    ORDER BY lower(c.name), d.name, c.component_id
    LIMIT $3::int
"""

# The names of planner experiences by id (for a route proposal's days, whose rows carry the place but not the name).
COMPONENT_NAMES_SQL = """
    SELECT c.component_id::text AS component_id, c.name, c.country, d.name AS place
    FROM catalog.v_tour_component c
    JOIN catalog.v_destination d ON d.destination_id = c.destination_id
    WHERE c.component_id = ANY($1::uuid[])
"""

_STOPWORDS = frozenset(
    "a an and are as at be by can do for from has have i in into is it its me my of on or our please show some "
    "that the their them then there this to tour tours trip trips want we what where which with would you your day "
    "days night nights week weeks".split())


class ToolError(ValueError):
    """A tool call the server refuses (unknown tool, bad arguments). The message is shown to the model, never the
    traveller."""


@dataclass
class Refs:
    """The references a turn may name: trip stops (``s<n>``), experiences (``c<n>``) and tours (``t<n>``), each with
    the catalog facts the reply may use. ``vocabulary`` collects every name the catalog gave on this turn (the
    grounding check of the reply)."""

    items: dict[str, dict] = field(default_factory=dict)
    _by_key: dict[tuple, str] = field(default_factory=dict)
    vocabulary: list[str] = field(default_factory=list)

    def add(self, kind: str, key: str, facts: dict) -> str:
        found = self._by_key.get((kind, key))
        if found:
            return found
        prefix = {"stop": "s", "component": "c", "tour": "t"}[kind]
        ref = f"{prefix}{sum(1 for r in self.items if r.startswith(prefix)) + 1}"
        self.items[ref] = {"kind": kind, **facts}
        self._by_key[(kind, key)] = ref
        self.vocabulary.extend(str(v) for k, v in facts.items() if k in _VOCAB_KEYS and v)
        return ref

    def get(self, ref: Any) -> Optional[dict]:
        return self.items.get(ref) if isinstance(ref, str) else None

    def component_id(self, ref: Any) -> Optional[str]:
        """The component id behind a stop or experience reference; None for a tour or an unknown reference."""
        item = self.get(ref)
        if item and item["kind"] in ("stop", "component"):
            return item["component_id"]
        return None


_VOCAB_KEYS = ("name", "place", "country", "title", "activity", "tour_name")


def normalise(text: Any) -> str:
    return unicodedata.normalize("NFKC", str(text or "")).strip()


def search_words(text: Any) -> list[str]:
    """Up to MAX_TERMS search words: letters and digits only (no LIKE pattern character can pass), 3+ characters, no
    stop word."""
    words = re.findall(r"[^\W_]+", normalise(text).lower())
    out: list[str] = []
    for w in words:
        if len(w) >= 3 and w not in _STOPWORDS and w not in out:
            out.append(w)
        if len(out) == MAX_TERMS:
            break
    return out


async def countries(conn: Any) -> list[dict]:
    return [{"country": r["country"], "tours": int(r["tour_count"]), "min_days": r["min_days"],
             "max_days": r["max_days"]} for r in await conn.fetch(COUNTRIES_SQL)]


def canonical_country(raw: Any, offered: list[dict]) -> Optional[str]:
    """The catalog spelling of a country AA sells (case and spacing ignored), else None."""
    want = re.sub(r"\s+", " ", normalise(raw).lower())
    for c in offered:
        if c["country"].lower() == want:
            return c["country"]
    return None


def _int_arg(args: dict, key: str, lo: int, hi: int, required: bool = False) -> Optional[int]:
    raw = args.get(key)
    if raw is None or raw == "":
        if required:
            raise ToolError(f"{key} is required")
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ToolError(f"{key} must be a whole number") from None
    if not lo <= value <= hi:
        raise ToolError(f"{key} must be between {lo} and {hi}")
    return value


def _not_offered(country: Any, offered: list[dict]) -> dict:
    return {"not_offered": True, "asked": normalise(country)[:60],
            "countries_offered": [c["country"] for c in offered]}


async def run(conn: Any, name: str, args: Any, refs: Refs, offered: list[dict]) -> dict:
    """Run one tool and return its JSON-able result (references in place of ids). Raises ToolError for a call the
    server refuses."""
    if name not in TOOL_NAMES:
        raise ToolError(f"unknown tool {str(name)[:40]!r}; tools: {', '.join(TOOL_NAMES)}")
    if not isinstance(args, dict):
        raise ToolError("args must be an object")
    if name == "route_options":
        return await _route_options(conn, args, refs, offered)
    if name == "find_tours":
        return await _find_tours(conn, args, refs, offered)
    if name == "tour_route":
        return await _tour_route(conn, args, refs)
    return await _find_experiences(conn, args, refs, offered)


def _country_arg(args: dict, offered: list[dict], required: bool) -> tuple[Optional[str], Optional[dict]]:
    """(catalog country or None, a not-offered answer or None)."""
    raw = args.get("country")
    if raw in (None, ""):
        if required:
            raise ToolError("country is required")
        return None, None
    country = canonical_country(raw, offered)
    return (country, None) if country else (None, _not_offered(raw, offered))


async def _component_names(conn: Any, ids: list[str]) -> dict[str, dict]:
    if not ids:
        return {}
    return {r["component_id"]: dict(r) for r in await conn.fetch(COMPONENT_NAMES_SQL, ids)}


async def _days_with_refs(conn: Any, proposal: dict, refs: Refs) -> list[dict]:
    """A routes.py proposal's days, each pinned component as a reference (an unpinned day keeps its place only)."""
    names = await _component_names(conn, [d["component_id"] for d in proposal["days"] if d.get("component_id")])
    out = []
    for d in proposal["days"]:
        row: dict[str, Any] = {"day": d["day"], "place": d.get("name")}
        cid = d.get("component_id")
        meta = names.get(cid) if cid else None
        if meta:
            row["ref"] = refs.add("component", cid, {"component_id": cid, "name": meta["name"],
                                                     "place": meta["place"], "country": meta["country"]})
            row["name"] = meta["name"]
        if row["place"]:
            refs.vocabulary.append(str(row["place"]))
        out.append(row)
    return out


async def _route_options(conn: Any, args: dict, refs: Refs, offered: list[dict]) -> dict:
    country, refused = _country_arg(args, offered, required=True)
    if refused:
        return refused
    days = _int_arg(args, "days", 1, MAX_DAYS, required=True)
    found = await routes_mod.proposals(conn, country, days)
    options = []
    for p in found.get("proposals", [])[:MAX_ROUTE_OPTIONS]:
        tours = []
        for s in p["segments"]:
            if s.get("tour_name"):
                refs.vocabulary.append(str(s["tour_name"]))
            tours.append({"tour": s.get("tour_name"), "days": s.get("days")})
        options.append({"total_days": p["total_days"], "tours": tours,
                        "days": await _days_with_refs(conn, p, refs)})
    return {"country": country, "days": days, "options": options}


async def _find_tours(conn: Any, args: dict, refs: Refs, offered: list[dict]) -> dict:
    country, refused = _country_arg(args, offered, required=False)
    if refused:
        return refused
    words = search_words(args.get("text"))
    max_days = _int_arg(args, "max_days", 1, MAX_DAYS)
    rows = await conn.fetch(FIND_TOURS_SQL, country, words or None, max_days, MAX_TOURS)
    tours = []
    for r in rows:
        ref = refs.add("tour", r["tour_id"], {"tour_id": r["tour_id"], "title": r["title"], "country": r["country"],
                                              "days": r["duration_days"]})
        tours.append({"ref": ref, "title": r["title"], "country": r["country"], "days": r["duration_days"]})
    return {"tours": tours}


async def _tour_route(conn: Any, args: dict, refs: Refs) -> dict:
    item = refs.get(args.get("tour"))
    if not item or item["kind"] != "tour":
        raise ToolError("tour must be a tour reference (t1, t2, ...) from find_tours on this turn")
    proposal = await routes_mod.tour_route(conn, item["tour_id"])
    if proposal is None:
        return {"tour": item["title"], "on_map": False}
    return {"tour": item["title"], "on_map": True, "total_days": proposal["total_days"],
            "days": await _days_with_refs(conn, proposal, refs)}


async def _find_experiences(conn: Any, args: dict, refs: Refs, offered: list[dict]) -> dict:
    country, refused = _country_arg(args, offered, required=False)
    if refused:
        return refused
    words = search_words(args.get("text"))
    if not words:
        raise ToolError("text needs at least one word of 3 or more letters")
    rows = await conn.fetch(FIND_EXPERIENCES_SQL, country, words, MAX_EXPERIENCES)
    out = []
    for r in rows:
        ref = refs.add("component", r["component_id"], {"component_id": r["component_id"], "name": r["name"],
                                                        "place": r["place"], "country": r["country"],
                                                        "activity": r["activity"]})
        out.append({"ref": ref, "name": r["name"], "place": r["place"], "country": r["country"],
                    "activity": r["activity"]})
    return {"experiences": out}
