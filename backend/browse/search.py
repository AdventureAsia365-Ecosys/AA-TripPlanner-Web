"""Free-text semantic search for the Map/Browse Lambda (Lambda A).

Embeds the query text (Bedrock), then ranks itinerary_components by
pgvector cosine similarity ONLY within the set already narrowed by the
active closed-enum filters. NOT cached (see design.md).

Returns the same destination-pin shape as tiles, aggregated to
destinations but ordered by best (lowest) cosine distance so the most
relevant places surface first.

AA-805: the query is embedded with input_type "search_query" (components were embedded as
"search_document" — Cohere's asymmetric retrieval pair); only components of ACTIVE tours
(published_tours.master_status = 'active', not soft-deleted — the v_active_tour_atoms rule) are
ranked, read tenant-scoped like fetch_catalog; the daily TripPlanner spend cap stops the embed call
(SearchUnavailable -> 503); failed embed attempts are logged.

AA-712: ranking goes through backend.shared.vector_topk — the nearest components come from the HNSW
index (ORDER BY embedding <=> q LIMIT k) and are grouped per destination afterwards, instead of
MIN(embedding <=> q) over every component (a full scan of every vector per search).
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional, Protocol

from backend.browse.filters import BrowseFilters
from backend.extraction.tour_days import set_catalog_tenant
from backend.shared import llm_gateway, vector_topk

STAGE = "tp_search_embed"
log = logging.getLogger(__name__)


class _Pool(Protocol):
    async def fetch(self, query: str, *args: Any) -> Any: ...
    async def execute(self, query: str, *args: Any) -> Any: ...
    async def fetchval(self, query: str, *args: Any) -> Any: ...

    def transaction(self) -> Any: ...


# Injectable embedder so tests don't call Bedrock.
Embedder = Callable[[str], list[float]]

SEARCH_LIMIT = 60
DEST_COLS = ("id", "name", "lat", "lng")
QUERY_INPUT_TYPE = "search_query"


class SearchUnavailable(Exception):
    """The model call needed for free-text search is refused (daily spend cap reached)."""


def _vector_literal(vec: list[float]) -> str:
    """pgvector accepts a text literal like '[0.1,0.2,...]'."""
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


# Only components of ACTIVE tours (AA-805); reads published_tours, so it is applied after the
# index-ordered scan (vector_topk keep_where).
ACTIVE_TOUR_SQL = """
    AND EXISTS (SELECT 1 FROM gold_aa_internal.published_tours pt
                WHERE pt.tour_id::text = c.source_tour_id
                  AND pt.master_status = 'active'
                  AND pt.deleted_at IS NULL)
"""


def search_where(filters: BrowseFilters) -> tuple[str, str, list[Any]]:
    """(scan_where, keep_where, filter_params) for vector_topk; the query embedding is $1, filter
    params follow from $2."""
    frag, filter_params = filters.component_where_sql(start_index=2)
    return f"c.embedding IS NOT NULL {frag}", ACTIVE_TOUR_SQL, filter_params


async def search(
    q: str,
    filters: BrowseFilters,
    *,
    pool: _Pool,
    embedder: Optional[Embedder] = None,
    limit: int = SEARCH_LIMIT,
) -> dict:
    if not q or not q.strip():
        return {"destinations": []}

    if embedder is not None:
        vec = embedder(q)
    else:
        if (await llm_gateway.budget_status(pool))["exhausted"]:
            raise SearchUnavailable("daily AI spend cap reached")
        # AA-685: model from the gateway route, one llm_call_log row per search (AA-805: and
        # one per failed attempt).
        route = await llm_gateway.load_route(pool, STAGE)
        signal = {"texts": 1, "query_len_chars": len(q)}
        failures: list = []
        try:
            vectors, call = llm_gateway.embed_texts(route, [q], input_type=QUERY_INPUT_TYPE,
                                                    failures=failures)
        finally:
            for failed in failures:
                await llm_gateway.record(pool, failed, signal)
        vec = vectors[0]
        await llm_gateway.record(pool, call, signal)
    vec_literal = _vector_literal(vec)

    scan_where, keep_where, filter_params = search_where(filters)
    params: list[Any] = [vec_literal, *filter_params]

    # published_tours enforces tenant RLS: run with the AA tenant set (SET LOCAL in a transaction on
    # one connection), or the active-tour filter would match nothing. The same transaction scopes
    # hnsw.ef_search (AA-712).
    async def _run(conn) -> list:
        async with conn.transaction():
            await set_catalog_tenant(conn)
            rows, mode = await vector_topk.ranked_destinations(
                conn, scan_where=scan_where, keep_where=keep_where, params=params,
                dest_cols=DEST_COLS, limit=limit)
        log.info("browse search: %d destinations via %s", len(rows), mode)
        return rows

    if hasattr(pool, "acquire"):
        async with pool.acquire() as conn:
            rows = await _run(conn)
    else:
        rows = await _run(pool)
    return {
        "destinations": [
            {
                "id": str(r["id"]),
                "name": r["name"],
                "lat": r["lat"],
                "lng": r["lng"],
                "component_count": r["component_count"],
            }
            for r in rows
        ]
    }
