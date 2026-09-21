"""Free-text semantic search for the Map/Browse Lambda (Lambda A).

Embeds the query text (Bedrock), then ranks itinerary_components by
pgvector cosine similarity ONLY within the set already narrowed by the
active closed-enum filters. NOT cached (see design.md).

Returns the same destination-pin shape as tiles, aggregated to
destinations but ordered by best (lowest) cosine distance so the most
relevant places surface first.
"""
from __future__ import annotations

from typing import Any, Callable, Optional, Protocol

from backend.browse.filters import BrowseFilters
from backend.shared import bedrock_satellite


class _Pool(Protocol):
    async def fetch(self, query: str, *args: Any) -> Any: ...


# Injectable embedder so tests don't call Bedrock.
Embedder = Callable[[str], list[float]]

SEARCH_LIMIT = 60


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

    embed = embedder or bedrock_satellite.embed
    vec = embed(q)
    vec_literal = _vector_literal(vec)

    sql, _ = build_search_query(filters, limit=limit)
    _, filter_params = filters.where_sql(start_index=2)
    params: list[Any] = [vec_literal]
    params.extend(filter_params)

    rows = await pool.fetch(sql, *params)
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
