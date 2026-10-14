"""Free-text semantic search for the Map/Browse Lambda (Lambda A).

Embeds the query text (Bedrock), then ranks itinerary_components by
pgvector cosine similarity ONLY within the set already narrowed by the
active closed-enum filters. NOT cached (see design.md).

Returns the same destination-pin shape as tiles, aggregated to
destinations but ordered by best (lowest) cosine distance so the most
relevant places surface first.

AA-805: the query is embedded with input_type "search_query" (components were embedded as
"search_document" — Cohere's asymmetric retrieval pair); only components of ACTIVE tours
(published_tours.master_status = 'active') are ranked, read tenant-scoped like fetch_catalog;
the daily TripPlanner spend cap stops the embed call (SearchUnavailable -> 503); failed embed
attempts are logged.
"""
from __future__ import annotations

from typing import Any, Callable, Optional, Protocol

from backend.browse.filters import BrowseFilters
from backend.extraction.tour_days import fetch_catalog
from backend.shared import llm_gateway

STAGE = "tp_search_embed"


class _Pool(Protocol):
    async def fetch(self, query: str, *args: Any) -> Any: ...
    async def execute(self, query: str, *args: Any) -> Any: ...


# Injectable embedder so tests don't call Bedrock.
Embedder = Callable[[str], list[float]]

SEARCH_LIMIT = 60
QUERY_INPUT_TYPE = "search_query"


class SearchUnavailable(Exception):
    """The model call needed for free-text search is refused (daily spend cap reached)."""


def _vector_literal(vec: list[float]) -> str:
    """pgvector accepts a text literal like '[0.1,0.2,...]'."""
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


def build_search_query(
    filters: BrowseFilters, limit: int = SEARCH_LIMIT
) -> tuple[str, int]:
    """Build the search SQL. The query embedding is $1; filters continue
    from $2; the LIMIT is the returned int (bound separately by caller).
    Returns (sql, next_param_index_for_limit)."""
    where_frag, _ = filters.where_sql(start_index=2)
    # We need the actual filter params at call time; here we only need the
    # SQL text. The distance operator <=> is cosine distance.
    sql = f"""
        SELECT d.id, d.name, d.lat, d.lng,
               COUNT(c.id) AS component_count,
               MIN(c.embedding <=> $1::vector) AS best_distance
        FROM shared.destinations d
        JOIN tripplanner.itinerary_components c ON c.destination_id = d.id
        WHERE c.embedding IS NOT NULL
          AND EXISTS (SELECT 1 FROM gold_aa_internal.published_tours pt
                      WHERE pt.tour_id::text = c.source_tour_id
                        AND pt.master_status = 'active')
          {where_frag}
        GROUP BY d.id, d.name, d.lat, d.lng
        ORDER BY best_distance ASC
        LIMIT {int(limit)}
    """
    return sql, 0


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

    sql, _ = build_search_query(filters, limit=limit)
    _, filter_params = filters.where_sql(start_index=2)
    params: list[Any] = [vec_literal]
    params.extend(filter_params)

    # published_tours enforces tenant RLS: run the query with the AA tenant set (SET LOCAL in a
    # transaction on one connection), or the active-tour filter would match nothing.
    if hasattr(pool, "acquire"):
        async with pool.acquire() as conn:
            rows = await fetch_catalog(conn, sql, *params)
    else:
        rows = await fetch_catalog(pool, sql, *params)
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
