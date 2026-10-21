"""Lambda A entrypoint — Map/Browse.

Routes:
  GET /browse/tiles/{tile_id}
  GET /browse/destinations/{destination_id}
  GET /browse/search?q=...
  GET /browse/shapes?ids=<uuid>,…&level=lo|mid   (AA-847 slice 6d: line geometry by id, CIS 256)
  GET /browse/routes?country=Nepal&days=10   (AA-674: whole-route proposals from real tours)
  GET /browse/tour-route?tour_id=<uuid>&source_id=<id>  (AA-812: one tour's whole route, for
      "Customise"; source_id, when known, resolves the current tour_id)
  GET /browse/catalog/tours?country=&min_days=&max_days=&month=&sort=&limit=&offset=
  GET /browse/catalog/tours/{slug}           (AA-812: tour page, catalog.tour v1)
  GET /browse/catalog/destinations           (AA-812: countries with live tours)
  GET /browse/catalog/destinations/{country} (AA-812: country page data)
  GET /browse/catalog/sitemap                (AA-812: slugs for sitemap.xml)

The /browse/catalog/* reads use only the CIS catalog views (ADR 0001, CIS migration 225). The place /
tour-graph reads (tiles, countries, by-country, destinations, search, routes, tour-route) follow
PLACES_SOURCE: legacy = the frozen tripplanner tables, cis = the CIS catalog views of migration 228
(AA-712 slice 6b; backend/shared/places_source.py). Every response says which in x-places-source.

Read routes (tiles, destinations) are CDN-cacheable with a fixed TTL;
/search sends no-store. Never calls Bedrock except for the search
embedding. The core `route()` coroutine is framework-agnostic and unit-
tested directly; `handler()` adapts a Lambda Function URL / API Gateway
event onto it.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from backend import config
from backend.browse import catalog as catalog_mod
from backend.browse import countries as countries_mod
from backend.browse import destinations as dest_mod
from backend.browse import routes as routes_mod
from backend.browse import tiles as tiles_mod
from backend.browse import search as search_mod
from backend.browse import shapes as shapes_mod
from backend.browse.filters import BrowseFilters
from backend.browse.tiles import TileError
from backend.shared import places_source

_TILE_RE = re.compile(r"^/browse/tiles/([^/]+)$")
_DEST_RE = re.compile(r"^/browse/destinations/([^/]+)$")
_SEARCH_RE = re.compile(r"^/browse/search$")
_COUNTRIES_RE = re.compile(r"^/browse/countries$")
_BY_COUNTRY_RE = re.compile(r"^/browse/by-country$")
_ROUTES_RE = re.compile(r"^/browse/routes$")
_TOUR_ROUTE_RE = re.compile(r"^/browse/tour-route$")
_SHAPES_RE = re.compile(r"^/browse/shapes$")
# catalog source ids are uuids today; accept any short id-like token, never free text.
_SOURCE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_CAT_TOURS_RE = re.compile(r"^/browse/catalog/tours$")
_CAT_TOUR_RE = re.compile(r"^/browse/catalog/tours/([a-z0-9-]{1,120})$")
_CAT_DESTS_RE = re.compile(r"^/browse/catalog/destinations$")
_CAT_DEST_RE = re.compile(r"^/browse/catalog/destinations/([a-z0-9-]{1,80})$")
_CAT_SITEMAP_RE = re.compile(r"^/browse/catalog/sitemap$")
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

_CACHE_HEADERS = {
    "content-type": "application/json",
    "cache-control": f"public, max-age={config.CDN_TTL_SECONDS}",
}
_NO_STORE_HEADERS = {
    "content-type": "application/json",
    "cache-control": "no-store",
}


def _resp(status: int, body: dict, headers: dict) -> dict:
    # AA-712 slice 6b: which place store served the read (legacy | cis), for the flip checks.
    headers = {**headers, "x-places-source": places_source.current()}
    return {"statusCode": status, "headers": headers, "body": json.dumps(body)}


async def route(
    method: str,
    path: str,
    query: dict[str, Any],
    *,
    pool,
) -> dict:
    if method != "GET":
        return _resp(405, {"error": "method not allowed"}, _NO_STORE_HEADERS)

    filters = BrowseFilters.from_query(query)

    m = _TILE_RE.match(path)
    if m:
        try:
            data = await tiles_mod.query_tile(m.group(1), filters, pool=pool)
        except TileError as e:
            return _resp(400, {"error": str(e)}, _NO_STORE_HEADERS)
        return _resp(200, data, _CACHE_HEADERS)

    if _ROUTES_RE.match(path):
        def _q(key: str) -> str:
            v = query.get(key)
            return str((v[0] if v else "") if isinstance(v, list) else (v or "")).strip()
        country, days_raw = _q("country"), _q("days")
        if not country or not days_raw.isdigit() or not 2 <= int(days_raw) <= 30:
            return _resp(400, {"error": "country and days (2-30) required"}, _NO_STORE_HEADERS)
        async with pool.acquire() as conn:
            data = await routes_mod.proposals(conn, country, int(days_raw))
        return _resp(200, data, _CACHE_HEADERS)

    # --- AA-812: catalog reads (CIS catalog views only) ---------------------------------------
    if _CAT_TOURS_RE.match(path):
        try:
            q = catalog_mod.parse_list_query(query)
        except catalog_mod.QueryError as e:
            return _resp(400, {"error": str(e)}, _NO_STORE_HEADERS)
        return _resp(200, await catalog_mod.list_tours(q, pool=pool), _CACHE_HEADERS)

    m = _CAT_TOUR_RE.match(path)
    if m:
        data = await catalog_mod.tour_detail(m.group(1), pool=pool)
        if data is None:
            return _resp(404, {"error": "tour not found"}, _NO_STORE_HEADERS)
        return _resp(200, data, _CACHE_HEADERS)

    if _CAT_DESTS_RE.match(path):
        return _resp(200, await catalog_mod.list_countries(pool=pool), _CACHE_HEADERS)

    m = _CAT_DEST_RE.match(path)
    if m:
        data = await catalog_mod.destination_page(m.group(1), pool=pool)
        if data is None:
            return _resp(404, {"error": "destination not found"}, _NO_STORE_HEADERS)
        return _resp(200, data, _CACHE_HEADERS)

    if _CAT_SITEMAP_RE.match(path):
        return _resp(200, await catalog_mod.sitemap(pool=pool), _CACHE_HEADERS)

    if _TOUR_ROUTE_RE.match(path):
        def _q(key: str) -> str:
            v = query.get(key)
            return str((v[0] if v else "") if isinstance(v, list) else (v or "")).strip()
        tid, sid = _q("tour_id"), _q("source_id")
        if tid and not _UUID_RE.match(tid):
            return _resp(400, {"error": "tour_id must be a uuid"}, _NO_STORE_HEADERS)
        if sid and not _SOURCE_ID_RE.match(sid):
            return _resp(400, {"error": "source_id is malformed"}, _NO_STORE_HEADERS)
        if not tid and not sid:
            return _resp(400, {"error": "tour_id (uuid) or source_id required"}, _NO_STORE_HEADERS)
        async with pool.acquire() as conn:
            current = await routes_mod.resolve_tour_id(conn, tid or None, sid or None)
            # AA-812 hotfix: the tour_id the link carried may be the one the Tour Graph knows.
            proposal = await routes_mod.tour_route(conn, current, (tid or None,)) if current else None
            fallback = await routes_mod.tour_fallback(conn, current) if current and proposal is None else None
        if proposal is None:
            # The planner pre-fills country + days and shows the tour's day list from `tour`.
            return _resp(404, {"error": "no planner route for this tour", "tour": fallback},
                         _NO_STORE_HEADERS)
        return _resp(200, {"tour_id": current, "proposal": proposal}, _CACHE_HEADERS)

    if _COUNTRIES_RE.match(path):
        data = await countries_mod.list_countries(pool=pool)
        return _resp(200, data, _CACHE_HEADERS)

    if _BY_COUNTRY_RE.match(path):
        country = query.get("country")
        if isinstance(country, list):
            country = country[0] if country else None
        if not country:
            return _resp(400, {"error": "country required"}, _NO_STORE_HEADERS)
        data = await countries_mod.destinations_in_country(country, pool=pool)
        return _resp(200, data, _CACHE_HEADERS)

    m = _DEST_RE.match(path)
    if m:
        data = await dest_mod.destination_detail(m.group(1), pool=pool)
        if data is None:
            return _resp(404, {"error": "destination not found"}, _NO_STORE_HEADERS)
        return _resp(200, data, _CACHE_HEADERS)

    if _SHAPES_RE.match(path):
        def _q1(key: str) -> Optional[str]:
            v = query.get(key)
            return (v[0] if v else None) if isinstance(v, list) else v
        try:
            data = await shapes_mod.shapes(_q1("ids"), _q1("level"), pool=pool)
        except shapes_mod.ShapesError as e:
            return _resp(400, {"error": str(e)}, _NO_STORE_HEADERS)
        return _resp(200, data, _CACHE_HEADERS)

    if _SEARCH_RE.match(path):
        q = query.get("q")
        if isinstance(q, list):
            q = q[0] if q else None
        try:
            data = await search_mod.search(q or "", filters, pool=pool)
        except search_mod.SearchUnavailable as e:  # AA-805: daily spend cap reached
            return _resp(503, {"error": "search_unavailable", "detail": str(e)}, _NO_STORE_HEADERS)
        return _resp(200, data, _NO_STORE_HEADERS)

    return _resp(404, {"error": "not found"}, _NO_STORE_HEADERS)


# --- Lambda adapter ---------------------------------------------------------

def _extract_request(event: dict) -> tuple[str, str, dict]:
    """Pull (method, path, query) from a Function URL / API Gateway v2 event."""
    ctx = event.get("requestContext", {})
    http = ctx.get("http", {})
    method = http.get("method") or event.get("httpMethod") or "GET"
    path = http.get("path") or event.get("rawPath") or event.get("path") or "/"
    query = event.get("queryStringParameters") or {}
    return method, path, query


def handler(event, context):  # pragma: no cover - thin AWS adapter
    import asyncio

    from backend.shared import auth
    from backend.shared.db import get_pool

    denied = auth.check_event(event)
    if denied is not None:
        return denied

    method, path, query = _extract_request(event)

    async def _run():
        pool = await get_pool()
        return await route(method, path, query, pool=pool)

    return asyncio.get_event_loop().run_until_complete(_run())
