"""Free-text semantic search for the Map/Browse Lambda (Lambda A).

Embeds the query text (Bedrock), then ranks itinerary_components by
pgvector cosine similarity ONLY within the set already narrowed by the
active closed-enum filters. The HTTP response is not cached (no-store); the
query embedding is (AA-856, below).

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

AA-712 slice 6b: with PLACES_SOURCE=cis it ranks catalog.v_tour_component (CIS migration 228) instead,
with the exact aggregate MIN(c.embedding <=> q) per destination (S237 decision D7: 228 has no top-k
function, and through the security_barrier view the HNSW index on geo.tour_place cannot serve an
index-ordered scan). The views return live tours only and need no AA tenant, so this path has neither
the active-tour EXISTS nor set_catalog_tenant. Rewrite to a top-k object before the catalog-wide run
(design 03 §6.5).

AA-856 (Cohere Embed V4 on acc2: 20 requests per minute, S239 burst test = search 500s):
  * query embeddings are cached in the Lambda container (``llm_gateway.query_embed_cache``: bounded
    LRU + TTL, key = stage + route models + input type + normalised text). A hit makes no model call,
    writes no llm_call_log row and is served even over the daily cap (it spends nothing);
  * when the embed fails (throttle or any Bedrock error, every attempt still logged as a failed
    llm_call_log row), search answers 200 with a keyword ranking over the same catalog and filters
    (``ranking: "keyword"``) instead of a 500. After a throttle this container skips the model call
    for ``THROTTLE_COOLDOWN_SECONDS`` and ranks by keyword directly. The daily spend cap still answers
    503 (AA-805), unchanged;
  * every search prints one ``{"evt": "search_embed", ...}`` line (outcome hit / miss / fallback,
    reason, the container's cache hit and miss counters); never the query text.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Callable, Optional, Protocol

from backend.browse.filters import BrowseFilters
from backend.browse.tiles import geo_fields
from backend.shared.catalog_rls import set_catalog_tenant
from backend.shared import llm_gateway, places_source, vector_topk

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


# AA-856: after a Bedrock throttle this container ranks by keyword without trying the model for this
# long (the 20 rpm bucket refills one request every 3 s; retrying at once spends it again).
THROTTLE_COOLDOWN_SECONDS = 10.0
_throttled_until = 0.0


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


# AA-712 slice 6b (PLACES_SOURCE=cis): exact aggregate over the catalog component view. Query vector
# = $1, filter params from $2 (the country filter reads the joined catalog.v_destination ``d``).
CIS_SEARCH_SQL = """
    SELECT d.destination_id AS id, d.name, d.lat, d.lng, d.located_by, d.precision,
           COUNT(c.component_id) AS component_count,
           MIN(c.embedding <=> $1::vector) AS best_distance
    FROM catalog.v_tour_component c
    JOIN catalog.v_destination d ON d.destination_id = c.destination_id
    WHERE c.embedding IS NOT NULL {where_frag}
    GROUP BY d.destination_id, d.name, d.lat, d.lng, d.located_by, d.precision
    ORDER BY best_distance ASC, d.destination_id
    LIMIT {limit}
"""


def cis_search_sql(filters: BrowseFilters, limit: int) -> tuple[str, list[Any]]:
    """(sql, filter_params) of the cis path; the query embedding is $1."""
    frag, filter_params = filters.where_sql(start_index=2)
    return CIS_SEARCH_SQL.format(where_frag=frag, limit=int(limit)), filter_params


def exact_first_for(filters: BrowseFilters) -> bool:
    """A country filter bounds the candidates to one country's components: the exact aggregate is
    cheap there and the top-k passes would mostly come up short (AA-712 review finding 1)."""
    return filters.country is not None


def _log_embed(outcome: str, reason: Optional[str] = None) -> None:
    """One CloudWatch line per search (AA-856): cache hit rate and fallbacks. No query text."""
    evt = {"evt": "search_embed", "outcome": outcome, **llm_gateway.query_embed_cache.stats()}
    if reason:
        evt["reason"] = reason
    print(json.dumps(evt), flush=True)


async def _query_vector(q: str, pool: _Pool) -> tuple[Optional[list[float]], Optional[str]]:
    """(vector, None) from the cache or one Bedrock call, or (None, reason) when search must fall
    back to keyword ranking. Raises SearchUnavailable over the daily spend cap (AA-805)."""
    global _throttled_until
    # AA-685: model from the gateway route (cached 60 s, never raises).
    route = await llm_gateway.load_route(pool, STAGE)
    key = llm_gateway.embed_cache_key(route, QUERY_INPUT_TYPE, q)
    cached = llm_gateway.query_embed_cache.get(key)
    if cached is not None:
        _log_embed("hit")
        return cached, None
    if time.monotonic() < _throttled_until:
        return None, "throttle_cooldown"
    if (await llm_gateway.budget_status(pool))["exhausted"]:
        raise SearchUnavailable("daily AI spend cap reached")
    text = llm_gateway.normalise_query(q)
    # One llm_call_log row per real embed (AA-805: and one per failed attempt).
    signal = {"texts": 1, "query_len_chars": len(text)}
    failures: list = []
    try:
        vectors, call = llm_gateway.embed_texts(route, [text], input_type=QUERY_INPUT_TYPE,
                                                failures=failures)
    except llm_gateway.BedrockError as e:
        throttled = llm_gateway.is_throttle(str(e))
        if throttled:
            _throttled_until = time.monotonic() + THROTTLE_COOLDOWN_SECONDS
        return None, "throttle" if throttled else "embed_error"
    finally:
        for failed in failures:
            await llm_gateway.record(pool, failed, signal)
    await llm_gateway.record(pool, call, signal)
    llm_gateway.query_embed_cache.put(key, vectors[0])
    _log_embed("miss")
    return vectors[0], None


async def search(
    q: str,
    filters: BrowseFilters,
    *,
    pool: _Pool,
    embedder: Optional[Embedder] = None,
    limit: int = SEARCH_LIMIT,
) -> dict:
    # Nothing to rank: empty, whitespace or punctuation/format characters only (e.g. "?!", a
    # zero-width space). Answer without a model call, so such requests never spend the shared
    # Cohere quota or write a failed llm_call_log row (AA-856).
    if not has_search_word(q):
        return {"destinations": []}

    if embedder is not None:
        vec = embedder(q)
    else:
        vec, reason = await _query_vector(q, pool)
        if vec is None:
            return await keyword_search(q, filters, pool=pool, limit=limit, reason=reason)
    vec_literal = _vector_literal(vec)

    if places_source.is_cis():
        sql, filter_params = cis_search_sql(filters, limit)
        rows = await pool.fetch(sql, vec_literal, *filter_params)
        log.info("browse search: %d destinations via cis exact", len(rows))
        return {"destinations": [_dest(r) for r in rows], "ranking": "semantic"}

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
                dest_cols=DEST_COLS, limit=limit, exact_first=exact_first_for(filters))
        log.info("browse search: %d destinations via %s", len(rows), mode)
        return rows

    rows = await _with_conn(pool, _run)
    return {"destinations": [_dest(r) for r in rows], "ranking": "semantic"}


async def _with_conn(pool: _Pool, fn: Callable[[Any], Any]) -> Any:
    if hasattr(pool, "acquire"):
        async with pool.acquire() as conn:
            return await fn(conn)
    return await fn(pool)


# --- Keyword fallback (AA-856) ------------------------------------------------------------------
#
# No pg_trgm/unaccent on the shared RDS (only vector, pg_stat_statements), so the fallback is a
# case-insensitive substring match (ILIKE) of each query word, scored per component:
#   destination name 3, component name 2, destination country 1, component text 1
# summed over the words (more words matched = higher), best component per destination, ties by the
# number of filtered components. Same filters, same active-tour rule (legacy) / live-tour views (cis)
# as the semantic path; the scan is over the filtered components only (~4k legacy rows today).

KEYWORD_MAX_TERMS = 8
_WORD_RE = re.compile(r"[^\W_]+")
_STOPWORDS = frozenset({
    "a", "and", "the", "of", "in", "on", "at", "to", "for", "with", "by", "from", "near",
    "or", "my", "me", "i", "is", "are", "trip", "tour", "tours", "travel", "visit", "day", "days",
})

_KEYWORD_SCORE = """
    CROSS JOIN LATERAL (
        SELECT SUM(CASE WHEN d.name ILIKE p THEN 3
                        WHEN c.name ILIKE p THEN 2
                        WHEN d.country ILIKE p OR c.text_extract ILIKE p THEN 1
                        ELSE 0 END) AS score
        FROM unnest($1::text[]) AS p
    ) s
"""

KEYWORD_LEGACY_SQL = """
    SELECT d.id, d.name, d.lat, d.lng,
           COUNT(c.id) AS component_count, MAX(s.score) AS best_score
    FROM tripplanner.itinerary_components c
    JOIN shared.destinations d ON d.id = c.destination_id
    {score}
    WHERE TRUE {where_frag} {active}
    GROUP BY d.id, d.name, d.lat, d.lng
    HAVING MAX(s.score) > 0
    ORDER BY best_score DESC, component_count DESC, d.id
    LIMIT {limit}
"""

KEYWORD_CIS_SQL = """
    SELECT d.destination_id AS id, d.name, d.lat, d.lng, d.located_by, d.precision,
           COUNT(c.component_id) AS component_count, MAX(s.score) AS best_score
    FROM catalog.v_tour_component c
    JOIN catalog.v_destination d ON d.destination_id = c.destination_id
    {score}
    WHERE TRUE {where_frag}
    GROUP BY d.destination_id, d.name, d.lat, d.lng, d.located_by, d.precision
    HAVING MAX(s.score) > 0
    ORDER BY best_score DESC, component_count DESC, d.destination_id
    LIMIT {limit}
"""


def has_search_word(q: Optional[str]) -> bool:
    """True when the normalised query holds at least one letter or digit."""
    return bool(_WORD_RE.search(llm_gateway.normalise_query(q or "")))


def keyword_terms(q: str) -> list[str]:
    """ILIKE patterns ('%word%') for the query: words of 2+ characters, stopwords dropped, at most
    KEYWORD_MAX_TERMS. Words are letters/digits only, so no LIKE wildcard or escape can get in.
    A query of stopwords only keeps its words."""
    words = [w for w in _WORD_RE.findall(llm_gateway.normalise_query(q)) if len(w) >= 2]
    kept = [w for w in words if w not in _STOPWORDS] or words
    return [f"%{w}%" for w in dict.fromkeys(kept)][:KEYWORD_MAX_TERMS]


def keyword_sql(filters: BrowseFilters, limit: int, *, cis: bool) -> tuple[str, list[Any]]:
    """(sql, filter_params): the patterns array is $1, filter params follow from $2."""
    frag, filter_params = filters.where_sql(start_index=2)
    if cis:
        sql = KEYWORD_CIS_SQL.format(score=_KEYWORD_SCORE, where_frag=frag, limit=int(limit))
    else:
        sql = KEYWORD_LEGACY_SQL.format(score=_KEYWORD_SCORE, where_frag=frag,
                                        active=ACTIVE_TOUR_SQL, limit=int(limit))
    return sql, filter_params


async def keyword_search(q: str, filters: BrowseFilters, *, pool: _Pool,
                         limit: int = SEARCH_LIMIT, reason: Optional[str] = None) -> dict:
    """Search without a model call (AA-856): ranked by keyword match, no embedding."""
    terms = keyword_terms(q)
    if not terms:
        _log_embed("fallback", reason)
        return {"destinations": [], "ranking": "keyword"}
    cis = places_source.is_cis()
    sql, filter_params = keyword_sql(filters, limit, cis=cis)
    if cis:
        rows = await pool.fetch(sql, terms, *filter_params)
    else:
        async def _run(conn) -> list:
            async with conn.transaction():
                await set_catalog_tenant(conn)
                return list(await conn.fetch(sql, terms, *filter_params))
        rows = await _with_conn(pool, _run)
    _log_embed("fallback", reason)
    log.info("browse search: %d destinations via keyword fallback (%s)", len(rows), reason)
    return {"destinations": [_dest(r) for r in rows], "ranking": "keyword"}


def _dest(r: Any) -> dict:
    return {
        "id": str(r["id"]),
        "name": r["name"],
        "lat": r["lat"],
        "lng": r["lng"],
        "component_count": r["component_count"],
        **geo_fields(r),
    }
