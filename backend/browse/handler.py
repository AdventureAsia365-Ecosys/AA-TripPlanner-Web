"""Lambda A entrypoint — Map/Browse.

Routes:
  GET /browse/tiles/{tile_id}
  GET /browse/destinations/{destination_id}
  GET /browse/search?q=...

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
from backend.browse import countries as countries_mod
from backend.browse import destinations as dest_mod
from backend.browse import tiles as tiles_mod
from backend.browse import search as search_mod
from backend.browse.filters import BrowseFilters
from backend.browse.tiles import TileError

_TILE_RE = re.compile(r"^/browse/tiles/([^/]+)$")
_DEST_RE = re.compile(r"^/browse/destinations/([^/]+)$")
_SEARCH_RE = re.compile(r"^/browse/search$")
_COUNTRIES_RE = re.compile(r"^/browse/countries$")
_BY_COUNTRY_RE = re.compile(r"^/browse/by-country$")

_CACHE_HEADERS = {
    "content-type": "application/json",
    "cache-control": f"public, max-age={config.CDN_TTL_SECONDS}",
}
_NO_STORE_HEADERS = {
    "content-type": "application/json",
    "cache-control": "no-store",
}


def _resp(status: int, body: dict, headers: dict) -> dict:
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

    if _SEARCH_RE.match(path):
        q = query.get("q")
        if isinstance(q, list):
            q = q[0] if q else None
        data = await search_mod.search(q or "", filters, pool=pool)
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
