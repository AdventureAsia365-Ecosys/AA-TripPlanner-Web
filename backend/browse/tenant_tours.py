"""AA-1094 (root ADR 0019 d10, S278 audit M20) — the tours of one agency tenant, with the tenant's own text.

GET /browse/tenant/{slug}/tours            the planner's tour list on /{slug}/plan
GET /browse/tenant/{slug}/tours/{tour}     the tenant's tour page /{slug}/tours/{tour}

Both read the CIS contract view catalog.v_tenant_tour_v1 (CIS 404, contract catalog.tenant_tour.v1, SELECT to role
tripplanner): one row per live AA tour with a version publishable on the tenant's site (approved by it, its own
edit, or AI text whose QA passed), with the served text and the AA facts of the live tour. AA liveness wins (a retired or paused tour is not in the view).

The slug is resolved first (tenant_site.lookup: only a SERVED tenant). The AA-hosted tenant aa-tripplanner
(config.is_aa_hosted: its fixed tenant id, never `kind`) has no agency text of its own: its list is the AA catalogue
(catalog.list_tours) with `source: "aa"`, so its planner keeps linking to the AA tour pages; it has no tenant tour page
(404). Every other served tenant, a synthetic demo agency (kind demo) included, lists its own adopted tours and has
tenant tour pages; one with no adopted tour lists nothing, never the AA catalogue (AA-1094, S278 finding: keying on
kind demo gave every synthetic agency the AA catalogue).

The view absent or not granted (42P01 / 42501: CIS 404 not applied yet): an agency lists no tour and has no tour
page; one log line says why. Any other read error raises (the handler answers 503 no-store).

No price is read or answered: the agency is the merchant of record (ADR 0019 b5) and AA's suggested retail price is
AA's catalogue price, not the agency's. No score, no personal data (the view carries none).
"""
from __future__ import annotations

import json
from typing import Any, Optional

from backend import config
from backend.browse import catalog

VIEW = "catalog.v_tenant_tour_v1"
VIEW_ABSENT_SQLSTATES = frozenset({"42P01", "42501"})
# The generator name of a tenant's T2 text (CIS ai_marking.version_mark GENERATOR is admin-side; this is the public
# label the page shows, as catalog.CATALOG_AI_GENERATOR is for AA's text).
TENANT_AI_GENERATOR = "Adventure Asia content writer"

# Column names, never * (the CIS test test_aa1094_migration_404.py pins the view's columns; the stand-in of
# backend/tests/test_aa1038_tenant_site_pg.py copies them).
COLUMNS = """
    t.source_system, t.source_id, t.tour_id::text AS tour_id, t.slug, t.title, t.subtitle, t.summary, t.highlights,
    t.itinerary_text, t.seo_title, t.seo_meta, t.country, t.duration_raw, t.duration_days, t.best_time_to_go,
    t.cover_photo_path, t.photo_count, t.version_id::text AS version_id, t.text_updated_at, t.human_edited,
    t.content_hash
"""

CARDS_SQL = f"""
    SELECT {COLUMNS}
    FROM {VIEW} t
    WHERE t.tenant_id = $1::uuid
      AND ($2::text IS NULL OR {catalog._COUNTRY_SLUG_SQL} = $2::text)
      AND ($3::int IS NULL OR t.duration_days >= $3::int)
      AND ($4::int IS NULL OR t.duration_days <= $4::int)
"""

BY_SLUG_SQL = f"""
    SELECT {COLUMNS}
    FROM {VIEW} t
    WHERE t.tenant_id = $1::uuid AND (t.slug = $2 OR ($3 <> '' AND left(replace(t.source_id, '-', ''), 8) = $3))
    ORDER BY (t.slug = $2) DESC
    LIMIT 1
"""


class TenantToursUnavailable(Exception):
    """The view could not be read (database or pool error): unknown, not "no tours"."""


def tenant_ai_mark(text_updated_at: Any, human_edited: Any) -> dict:
    """The machine-readable mark of a tenant's T2 text (EU AI Act Art. 50(2)): an AI rewrite made at the version's
    time; a tenant edit is AI-assisted and human-edited, with no generation time of its own (CIS version_mark)."""
    edited = bool(human_edited)
    return {
        "ai_generated": True,
        "generator": TENANT_AI_GENERATOR,
        "model": None,
        "generated_at": None if edited else catalog._iso(text_updated_at),
        "human_edited": edited,
    }


def _card(r: Any) -> dict:
    """A tenant tour as the planner's TourCard (the same keys as catalog._card)."""
    return {
        "source_id": r["source_id"],
        "tour_id": r["tour_id"],
        "slug": r["slug"],
        "title": r["title"],
        "subtitle": r["subtitle"],
        "summary": r["summary"],
        "country": r["country"],
        "country_slug": catalog.slugify(r["country"]),
        "duration_days": r["duration_days"],
        "duration_raw": r["duration_raw"],
        "best_time_to_go": r["best_time_to_go"],
        "highlights": catalog._str_list(r["highlights"])[:3],
        "cover_url": catalog.photo_url(r["cover_photo_path"], "large"),
        "cover_url_small": catalog.photo_url(r["cover_photo_path"], "small"),
        "cover_source": "tour" if r["cover_photo_path"] else None,
        "cover_place": None,
        "photo_count": r["photo_count"] or 0,
        "lifecycle_stage": None,
        # The tenant's text date stands in for the publish date (the "recent" / "featured" sorts).
        "published_at": catalog._iso(r["text_updated_at"]),
        "updated_at": catalog._iso(r["text_updated_at"]),
        "ai": tenant_ai_mark(r["text_updated_at"], r["human_edited"]),
    }


async def _fetch(pool: Any, sql: str, *args: Any) -> Optional[list]:
    """Rows of the view, None when the view is absent for this role (before CIS 404)."""
    try:
        return await pool.fetch(sql, *args)
    except Exception as exc:  # noqa: BLE001 — only the type is logged, never str(exc) (asyncpg echoes values)
        if getattr(exc, "sqlstate", None) in VIEW_ABSENT_SQLSTATES:
            print(json.dumps({"evt": "tenant_tour_view_unreadable", "error": type(exc).__name__}))
            return None
        print(json.dumps({"evt": "tenant_tours_failed", "error": type(exc).__name__}))
        raise TenantToursUnavailable(type(exc).__name__) from None


def serves_aa_catalogue(site: dict) -> bool:
    """True for the AA-hosted tenant only (config.is_aa_hosted on the tenant id); the tenant's `kind` plays no part."""
    return config.is_aa_hosted(site.get("tenant_id"))


async def list_tours(site: dict, q: dict, *, pool: Any) -> dict:
    """The planner's tour list of a served tenant: the AA catalogue for the AA-hosted tenant (`source: "aa"`), the
    tenant's own tours otherwise (`source: "tenant"`, empty when it adopted none). Same page shape as
    catalog.list_tours."""
    if serves_aa_catalogue(site):
        return {**await catalog.list_tours(q, pool=pool), "source": "aa"}
    rows = await _fetch(pool, CARDS_SQL, site["tenant_id"], q["country"], q["min_days"], q["max_days"]) or []
    cards = [_card(r) for r in rows]
    if q["month"]:
        cards = [c for c in cards
                 if (m := catalog.months_from_text(c["best_time_to_go"])) is None or q["month"] in m]
    cards = catalog.sort_cards(cards, q["sort"])
    page = cards[q["offset"]: q["offset"] + q["limit"]]
    await catalog.fill_fallback_covers(page, pool=pool)
    return {
        "filters": {k: q[k] for k in ("country", "min_days", "max_days", "month", "sort")},
        "total": len(cards),
        "limit": q["limit"],
        "offset": q["offset"],
        "tours": page,
        "source": "tenant",
    }


async def tour_detail(site: dict, slug: str, *, pool: Any) -> Optional[dict]:
    """One tour of a served agency tenant with the tenant's text, or None (the AA-hosted tenant, a tour the tenant did
    not make its own, a tour AA no longer serves, or the view absent). The slug may be an older spelling: its source-id
    suffix finds the tour and `slug` in the answer is the current one (the page redirects)."""
    if serves_aa_catalogue(site):
        return None
    slug = (slug or "").strip().lower()
    rows = await _fetch(pool, BY_SLUG_SQL, site["tenant_id"], slug, catalog.source_id_suffix(slug))
    if not rows:
        return None
    t = rows[0]
    photos = await pool.fetch(catalog.TOUR_PHOTOS_SQL, t["tour_id"])
    out = {
        "contract": "catalog.tenant_tour",
        "version": 1,
        "tenant": {"slug": site["slug"], "name": site["name"]},
        "source_system": t["source_system"],
        "source_id": t["source_id"],
        "tour_id": t["tour_id"],
        "slug": t["slug"],
        "title": t["title"],
        "subtitle": t["subtitle"],
        "summary": t["summary"],
        "highlights": catalog._str_list(t["highlights"]),
        "itinerary_text": t["itinerary_text"],
        "seo_title": t["seo_title"],
        "seo_meta": t["seo_meta"],
        "country": t["country"],
        "country_slug": catalog.slugify(t["country"]),
        "duration_raw": t["duration_raw"],
        "duration_days": t["duration_days"],
        "best_time_to_go": t["best_time_to_go"],
        "cover_url": catalog.photo_url(t["cover_photo_path"], "large"),
        "cover_source": "tour" if t["cover_photo_path"] else None,
        "cover_place": None,
        "photo_count": t["photo_count"] or 0,
        "content_hash": t["content_hash"],
        "ai": tenant_ai_mark(t["text_updated_at"], t["human_edited"]),
        "photos": [
            {"photo_id": p["photo_id"], "path": p["path"], "place_label": p["place_label"],
             "width": p["width"], "height": p["height"], "position": p["position"],
             "url": catalog.photo_url(p["path"], "large"), "url_small": catalog.photo_url(p["path"], "small")}
            for p in catalog._unique_paths(photos)
        ],
    }
    if not out["photos"]:
        await catalog.fill_fallback_covers([out], pool=pool)
    return out
