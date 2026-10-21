"""AA-812 — fixture rows shaped like the CIS catalog views (migration 225), plus a fake asyncpg pool
that answers the catalog / tour-route queries from them.

TEST AND LOCAL DEV ONLY. backend/tests/ is dropped from the Lambda zip (package_lambdas.sh), so
nothing here can reach a deployed path. Used by test_catalog.py and by dev_browse_server.py (local
screenshots of the new pages without a database).

The fake applies the same filters the SQL does (country slug, duration range, retired, slug or
source-id suffix), so the tests exercise the Python side (month filter, sort, paging, shapes) on
realistic rows.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from backend.browse import catalog as cat
from backend.browse import routes as routes_mod


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def _slug(title: str, country: str, source_id: str) -> str:
    return cat.slugify(f"{title} {country}")[:80].strip("-") + "-" + source_id.replace("-", "")[:8]


_T = [
    # source_id, tour_id, title, country, days, best_time, lifecycle, photos, published
    ("3f0c2a8e-1b7d-4c55-9a7e-2d4b6f8a1c90", "8d2e4f60-5a1b-4c3d-9e8f-7a6b5c4d3e2f",
     "Hanoi and Ha Long Bay", "Vietnam", 4, "October to April", "active", 3, "2026-10-01T08:00:00"),
    ("5a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d", "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d",
     "Mekong Delta by Boat", "Vietnam", 9, "Nov - Mar", "active", 0, "2026-09-20T08:00:00"),
    ("7c8d9e0f-1a2b-4c3d-8e4f-5a6b7c8d9e0f", "2b3c4d5e-6f7a-4b8c-9d0e-1f2a3b4c5d6e",
     "Sri Lanka Hill Country Trek", "Sri Lanka", 7, "All year", "active", 2, "2026-09-25T08:00:00"),
    ("9e0f1a2b-3c4d-4e5f-8a6b-7c8d9e0f1a2b", "3c4d5e6f-7a8b-4c9d-8e0f-2a3b4c5d6e7f",
     "Old Saigon Food Walk", "Vietnam", 2, None, "retired", 1, "2026-05-01T08:00:00"),
]

TOURS: list[dict] = []
for sid, tid, title, country, days, best, stage, nphotos, pub in _T:
    TOURS.append({
        "source_system": "cis", "source_id": sid, "tour_id": tid, "source_version": 1, "sku": None,
        "slug": _slug(title, country, sid), "title": title, "subtitle": f"{days} days in {country}",
        "summary": f"{title}: a {days}-day Adventure Asia itinerary.", "description": None,
        "highlights": '["Private guide", "Hand-picked stays", "Local food"]',
        "country": country, "duration_raw": f"{days} days", "duration_days": days,
        "activities": '["walking", "boat trip"]', "inclusions": "Private guide\nAll transfers\nBreakfast daily",
        "exclusions": "International flights\nVisa fees", "best_time_to_go": best, "group_size": "2-12",
        "lifecycle_stage": stage,
        "cover_photo_path": f"/content/photos/{tid[:8]}-0000-4000-8000-000000000001" if nphotos else None,
        "photo_count": nphotos, "published_at": _ts(pub), "updated_at": _ts(pub),
        "content_hash": "9e107d9d372bb6826bd81d3542a419d6",
    })

HANOI = "5c6d7e8f-9a0b-4c1d-8e2f-3a4b5c6d7e8f"
HALONG = "6d7e8f9a-0b1c-4d2e-8f3a-4b5c6d7e8f9a"
CANTHO = "7e8f9a0b-1c2d-4e3f-8a4b-5c6d7e8f9a0b"
KANDY = "8f9a0b1c-2d3e-4f4a-8b5c-6d7e8f9a0b1c"
ELLA = "9a0b1c2d-3e4f-4a5b-8c6d-7e8f9a0b1c2d"
DESTS = {
    HANOI: ("Hanoi", "Vietnam", 21.0285, 105.8542),
    HALONG: ("Ha Long Bay", "Vietnam", 20.9101, 107.1839),
    CANTHO: ("Can Tho", "Vietnam", 10.0452, 105.7469),
    KANDY: ("Kandy", "Sri Lanka", 7.2906, 80.6337),
    ELLA: ("Ella", "Sri Lanka", 6.8667, 81.0466),
}

# (tour index, day, title, body, [(destination_id or None, place_name, activity_types)])
_DAYS = [
    (0, 1, "Arrive in Hanoi", "Meet your guide and walk the Old Quarter of Hanoi.", [(HANOI, "Hanoi", ["walking", "food"])]),
    (0, 2, "Hanoi to Ha Long Bay", "Drive to Ha Long Bay and board your junk.", [(HALONG, "Ha Long Bay", ["boat trip"])]),
    (0, 3, "Ha Long Bay", "Kayak between the karsts.", [(HALONG, "Ha Long Bay", ["kayaking"])]),
    (0, 4, "Back to Hanoi", "Return to Hanoi for your onward flight.", [(HANOI, "Hanoi", [])]),
    (1, 1, "Can Tho", "Floating markets at dawn.", [(CANTHO, "Can Tho", ["boat trip", "food"])]),
    (2, 1, "Kandy", "Temple of the Tooth.", [(KANDY, "Kandy", ["cultural"])]),
    (2, 2, "Kandy to Ella", "Scenic train through tea country.", [(ELLA, "Ella", ["train", "walking"]),
                                                                    (None, "Nuwara Eliya", ["tea"])]),
]
DAYS: list[dict] = []
PLACES: list[dict] = []
for ti, n, title, body, places in _DAYS:
    tid = TOURS[ti]["tour_id"]
    DAYS.append({"tour_id": tid, "day_number": n, "title": title, "body": body,
                 "places": [p[1] for p in places]})
    for pos, (did, name, acts) in enumerate(places, start=1):
        d = DESTS.get(did) if did else None
        PLACES.append({
            "tour_id": tid, "day_number": n, "position": pos, "place_name": name, "place_card_id": None,
            "place_display_name": None, "locality": None, "region": None,
            "country": TOURS[ti]["country"], "destination_id": did,
            "destination_name": d[0] if d else None, "lat": d[2] if d else None, "lng": d[3] if d else None,
            "activity_types": acts, "atom_count": 1,
        })

# itinerary_text: canonical "Day N — Title\nBody" blocks for tours with days; the retired tour has
# a legacy free-text itinerary (no `days`), which the page shows as a fallback.
for t in TOURS:
    blocks = [f"Day {d['day_number']} — {d['title']}\n{d['body']}" for d in DAYS if d["tour_id"] == t["tour_id"]]
    t["itinerary_text"] = "\n\n".join(blocks) or None
TOURS[3]["itinerary_text"] = ("Morning: street-food breakfast in District 1.\n"
                              "Afternoon: Ben Thanh market and a coffee stop.")

PHOTOS: list[dict] = []
for t in TOURS:
    for i in range(1, t["photo_count"] + 1):
        pid = f"{t['tour_id'][:8]}-0000-4000-8000-{i:012d}"
        PHOTOS.append({"tour_id": t["tour_id"], "photo_id": pid, "path": f"/content/photos/{pid}",
                       "place_label": None, "destination_id": None, "width": 1600, "height": 1067,
                       "position": i})

DEST_PHOTOS = [
    {"destination_id": HANOI, "photo_id": "a0000000-0000-4000-8000-000000000001",
     "path": "/content/photos/a0000000-0000-4000-8000-000000000001", "place_label": "Old Quarter",
     "width": 1600, "height": 1067, "position": 1},
    {"destination_id": HALONG, "photo_id": "a0000000-0000-4000-8000-000000000002",
     "path": "/content/photos/a0000000-0000-4000-8000-000000000002", "place_label": "Karsts",
     "width": 1600, "height": 1067, "position": 1},
]

# Planner tables (tripplanner.tour_stop / itinerary_components) for tour 0, for the tour-route read.
STOPS = [
    {"tour_id": TOURS[0]["tour_id"], "day_index": d, "destination_id": did, "name": DESTS[did][0],
     "lat": DESTS[did][2], "lng": DESTS[did][3], "country": "Vietnam"}
    for d, did in ((1, HANOI), (2, HALONG), (3, HALONG), (4, HANOI))
]

# AA-812 hotfix (D2), shaped like Dev: the Tour Graph is keyed by the published tour_id of the day it
# was extracted. Tour 1 (Mekong) was extracted under an OLDER tour_id and re-uploaded since (catalog
# tour_id is newer); its tour page link carries the tour_id it was rendered with.
MEKONG_OLD_ID = "0e1f2a3b-4c5d-4e6f-8a7b-8c9d0e1f2a3b"
STOPS.append({"tour_id": MEKONG_OLD_ID, "day_index": 1, "destination_id": CANTHO, "name": "Can Tho",
              "lat": DESTS[CANTHO][2], "lng": DESTS[CANTHO][3], "country": "Vietnam"})
# Sri Lanka (tour 2) was never extracted into the graph, but another (older, unpublished) Sri Lanka
# tour left planner experiences at Kandy (two) and Ella; Nuwara Eliya has no shared.destinations row.
OTHER_SL_TOUR = "4d5e6f7a-8b9c-4d0e-8f1a-2b3c4d5e6f7a"

# tripplanner.itinerary_components rows (embedding NULL = not searchable, ignored by the reads).
PLANNER_COMPONENTS: list[dict] = (
    [{"id": f"c0000000-0000-4000-8000-00000000000{s['day_index']}", "source_tour_id": TOURS[0]["tour_id"],
      "source_day_index": s["day_index"], "destination_id": s["destination_id"], "embedding": True}
     for s in STOPS if s["tour_id"] == TOURS[0]["tour_id"]]
    + [{"id": "c1000000-0000-4000-8000-000000000001", "source_tour_id": MEKONG_OLD_ID,
        "source_day_index": 1, "destination_id": CANTHO, "embedding": True},
       {"id": "c2000000-0000-4000-8000-000000000001", "source_tour_id": OTHER_SL_TOUR,
        "source_day_index": 3, "destination_id": KANDY, "embedding": True},
       {"id": "c2000000-0000-4000-8000-000000000002", "source_tour_id": OTHER_SL_TOUR,
        "source_day_index": 4, "destination_id": KANDY, "embedding": True},
       {"id": "c2000000-0000-4000-8000-000000000003", "source_tour_id": OTHER_SL_TOUR,
        "source_day_index": 5, "destination_id": ELLA, "embedding": True},
       {"id": "c2000000-0000-4000-8000-000000000009", "source_tour_id": OTHER_SL_TOUR,
        "source_day_index": 6, "destination_id": ELLA, "embedding": None}]
)
# Kept for older tests: tour 0's components in the graph-read shape.
COMPONENTS = [
    {"tour_id": c["source_tour_id"], "day_index": c["source_day_index"], "destination_id": c["destination_id"],
     "component_id": c["id"]}
    for c in PLANNER_COMPONENTS if c["source_tour_id"] == TOURS[0]["tour_id"]
]


def _visible(t: dict) -> bool:
    return t["lifecycle_stage"] != cat.HIDDEN_LIFECYCLE


def _cslug(t: dict) -> str:
    return cat.slugify(t["country"])


def _norm(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()


class FakeCatalogPool:
    """Answers the exact query constants of backend.browse.catalog / routes from the rows above.
    An unknown query returns no rows (so the planner's other reads show their empty states)."""

    def __init__(self, tours: list[dict] | None = None, stops: list[dict] | None = None,
                 components: list[dict] | None = None, dest_photos: list[dict] | None = None):
        self.tours = TOURS if tours is None else tours
        self.stops = STOPS if stops is None else stops
        self.components = PLANNER_COMPONENTS if components is None else components
        self.dest_photos = DEST_PHOTOS if dest_photos is None else dest_photos
        self.calls: list[tuple[str, tuple]] = []

    def _dest_cover(self, did: str) -> str | None:
        return next((p["path"] for p in sorted(self.dest_photos, key=lambda p: p["position"])
                     if p["destination_id"] == did), None)

    def _by_id(self, tid: str) -> dict | None:
        return next((t for t in self.tours if t["tour_id"] == tid), None)

    async def fetch(self, query: str, *args: Any) -> list[dict]:
        self.calls.append((query, args))
        q = _norm(query)
        if q == _norm(cat.TOUR_CARDS_SQL):
            country, lo, hi = args
            return [t for t in self.tours if _visible(t)
                    and (country is None or _cslug(t) == country)
                    and (lo is None or (t["duration_days"] or 0) >= lo)
                    and (hi is None or (t["duration_days"] or 0) <= hi)]
        if q == _norm(cat.TOUR_DAYS_SQL):
            return sorted([d for d in DAYS if d["tour_id"] == args[0]], key=lambda d: d["day_number"])
        if q == _norm(cat.TOUR_PLACES_SQL):
            return [p for p in PLACES if p["tour_id"] == args[0]]
        if q == _norm(cat.TOUR_PHOTOS_SQL):
            return [p for p in PHOTOS if p["tour_id"] == args[0]]
        if q == _norm(cat.COUNTRIES_SQL):
            out: dict[str, dict] = {}
            for t in self.tours:
                if not _visible(t):
                    continue
                c = out.setdefault(t["country"], {"country": t["country"], "tour_count": 0, "min_days": None,
                                                  "max_days": None, "cover_photo_path": None,
                                                  "updated_at": t["updated_at"]})
                c["tour_count"] += 1
                d = t["duration_days"]
                c["min_days"] = d if c["min_days"] is None else min(c["min_days"], d)
                c["max_days"] = d if c["max_days"] is None else max(c["max_days"], d)
                c["cover_photo_path"] = c["cover_photo_path"] or t["cover_photo_path"]
                c["updated_at"] = max(c["updated_at"], t["updated_at"])
            return sorted(out.values(), key=lambda c: (-c["tour_count"], c["country"]))
        if q == _norm(cat.COUNTRY_PLACES_SQL):
            ids = set(args[0])
            return [p for p in PLACES if p["tour_id"] in ids and p["destination_id"] and p["lat"] is not None]
        if q == _norm(cat.DESTINATION_COVERS_SQL):
            return [{"destination_id": d, "cover_photo_path": self._dest_cover(d),
                     "photo_count": sum(1 for p in self.dest_photos if p["destination_id"] == d),
                     "precision": "point", "shape_lo": None}
                    for d in args[0]]
        if q == _norm(cat.LINE_SHAPES_LO_SQL):     # AA-847 slice 6d: no line place in these fixtures
            return []
        if q == _norm(cat.DESTINATION_PHOTOS_SQL):
            return [p for p in self.dest_photos if p["destination_id"] in args[0]]
        if q == _norm(cat.TOUR_DESTINATIONS_SQL):
            ids = set(args[0])
            return [{"tour_id": p["tour_id"], "destination_id": p["destination_id"],
                     "destination_name": p["destination_name"], "day_number": p["day_number"]}
                    for p in PLACES if p["tour_id"] in ids and p["destination_id"]]
        if q == _norm(cat.COUNTRY_COVERS_SQL):
            rows = [{"country_key": c.lower(), "name": n, "cover_photo_path": self._dest_cover(d),
                     "photo_count": sum(1 for p in self.dest_photos if p["destination_id"] == d)}
                    for d, (n, c, _lat, _lng) in DESTS.items() if c.lower() in args[0] and self._dest_cover(d)]
            return sorted(rows, key=lambda r: (r["country_key"], -r["photo_count"], r["name"]))
        if q == _norm(cat.SITEMAP_SQL):
            return sorted([t for t in self.tours if _visible(t)], key=lambda t: t["slug"])
        if q == _norm(routes_mod.DAYS_SQL):
            return sorted([s for s in self.stops if s["tour_id"] in args[0]],
                          key=lambda s: (s["tour_id"], s["day_index"]))
        if q == _norm(routes_mod.COMPONENTS_SQL):
            return [{"tour_id": c["source_tour_id"], "day_index": c["source_day_index"],
                     "destination_id": c["destination_id"], "component_id": c["id"]}
                    for c in sorted(self.components, key=lambda c: (c["source_tour_id"], c["source_day_index"], c["id"]))
                    if c["source_tour_id"] in args[0] and c["embedding"]]
        if q == _norm(routes_mod.TOUR_ROUTE_PLACES_SQL):
            rows = sorted([p for p in PLACES if p["tour_id"] == args[0] and p["day_number"] is not None
                           and p["destination_id"] and p["lat"] is not None],
                          key=lambda p: (p["day_number"], p["position"]))
            return [{"day_number": p["day_number"], "position": p["position"],
                     "destination_id": p["destination_id"], "name": p["destination_name"],
                     "lat": p["lat"], "lng": p["lng"], "country": p["country"]} for p in rows]
        if q == _norm(routes_mod.PLACE_COMPONENTS_SQL):
            dests, keys = set(args[0]), set(args[1])
            rows = [c for c in self.components if c["destination_id"] in dests and c["embedding"]]
            rows.sort(key=lambda c: (c["destination_id"], c["source_tour_id"] not in keys, c["source_tour_id"],
                                     c["source_day_index"], c["id"]))
            return [{"destination_id": c["destination_id"], "component_id": c["id"]} for c in rows]
        return []

    async def fetchrow(self, query: str, *args: Any) -> dict | None:
        self.calls.append((query, args))
        if _norm(query) == _norm(routes_mod.TOUR_FALLBACK_SQL):
            t = self._by_id(args[0])
            if not t:
                return None
            days = [{"day": d["day_number"], "title": d["title"]}
                    for d in sorted(DAYS, key=lambda d: d["day_number"]) if d["tour_id"] == t["tour_id"]]
            return {"title": t["title"], "country": t["country"], "duration_days": t["duration_days"],
                    "days": json.dumps(days)}
        if _norm(query) == _norm(cat.TOUR_BY_SLUG_SQL):
            slug, suffix = args
            hit = next((t for t in self.tours if t["slug"] == slug), None)
            hit = hit or next((t for t in self.tours
                               if suffix and t["source_id"].replace("-", "")[:8] == suffix), None)
            return hit
        rows = await self.fetch(query, *args)
        return rows[0] if rows else None

    async def fetchval(self, query: str, *args: Any) -> Any:
        self.calls.append((query, args))
        if _norm(query) == _norm(routes_mod.TOUR_TITLE_SQL):
            t = self._by_id(args[0])
            return t["title"] if t else None
        if _norm(query) == _norm(routes_mod.TOUR_ID_BY_SOURCE_SQL):
            return next((t["tour_id"] for t in self.tours if t["source_id"] == args[0]), None)
        if _norm(query) == _norm(routes_mod.TOUR_DAYS_COUNT_SQL):
            t = self._by_id(args[0])
            return (t["duration_days"] or 0) if t else None
        return None

    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                return pool

            async def __aexit__(self, *a):
                return False

        return _Ctx()
