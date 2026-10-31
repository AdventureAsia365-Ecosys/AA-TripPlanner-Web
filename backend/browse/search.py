"""Free-text semantic search for the Map/Browse Lambda (Lambda A).

Embeds the query text (Bedrock), then ranks the catalog components by
pgvector cosine similarity ONLY within the set already narrowed by the
active closed-enum filters. The HTTP response is not cached (no-store); the
query embedding is (AA-856, below).

Returns the same destination-pin shape as tiles, aggregated to
destinations but ordered by best (lowest) cosine distance so the most
relevant places surface first.

AA-805: the query is embedded with input_type "search_query" (components were embedded as
"search_document" — Cohere's asymmetric retrieval pair); only components of LIVE tours are ranked (the
catalog views hold nothing else); the daily TripPlanner spend cap stops the embed call; failed embed
attempts are logged.

AA-712: ranking goes through backend.shared.vector_topk — the nearest components come from the HNSW
index (ORDER BY embedding <=> q LIMIT k) and are grouped per destination afterwards, instead of
MIN(embedding <=> q) over every component (a full scan of every vector per search).

AA-712 slice 6b / AA-847: it ranks the CIS catalog through the vector_topk passes (``vector_topk.CIS``): the top-k
scan reads catalog.v_component_embedding (CIS 253, not a security_barrier view, so the HNSW index on
geo.tour_place serves it; it shows the rows of v_tour_component: live tours, usable places, never a suspect or
merged one), the exact fallback catalog.v_tour_component and the destinations catalog.v_destination. The views
need no AA tenant. Since AA-712 X4 this is the only path (the frozen tripplanner tables are gone).

AA-856 (Cohere Embed V4 on acc2: 20 requests per minute, S239 burst test = search 500s):
  * query embeddings are cached in the Lambda container (``llm_gateway.query_embed_cache``: bounded
    LRU + TTL, key = stage + route models + input type + normalised text). A hit makes no model call,
    writes no llm_call_log row and is served even over the daily cap (it spends nothing);
  * when the embed fails (throttle or any Bedrock error, every attempt still logged as a failed
    llm_call_log row), search answers 200 with a keyword ranking over the same catalog and filters
    (``ranking: "keyword"``) instead of a 500. After a throttle this container skips the model call
    for ``THROTTLE_COOLDOWN_SECONDS`` and ranks by keyword directly;
  * AA-642 S0 (root ADR 0009 decisions 5 and 6): a refused model call is a keyword ranking too (200),
    never a 503: over the daily spend cap (``ai_budget_exhausted: true``), and when the spend counter
    or the route config cannot be read (``ai_paused: true``, fail closed: no embed call);
  * every search prints one ``{"evt": "search_embed", ...}`` line (outcome hit / miss / fallback,
    reason, the container's cache hit and miss counters); never the query text.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Callable, Optional, Protocol

from backend.browse.filters import BrowseFilters
from backend.browse.tiles import LINE_COLS, LINE_COLS_SQL, geo_fields
from backend.shared import llm_gateway, observability, vector_topk

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
QUERY_INPUT_TYPE = "search_query"


# AA-856: after a Bedrock throttle this container ranks by keyword without trying the model for this
# long (the 20 rpm bucket refills one request every 3 s; retrying at once spends it again).
THROTTLE_COOLDOWN_SECONDS = 10.0
_throttled_until = 0.0


def _vector_literal(vec: list[float]) -> str:
    """pgvector accepts a text literal like '[0.1,0.2,...]'."""
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


# AA-847: the country filter as an InitPlan array over the catalog destinations, so it stays
# inside the index-ordered scan of catalog.v_component_embedding (vector_topk.CIS).
COUNTRY_CLAUSE = ("AND c.destination_id = ANY(ARRAY(SELECT dc.destination_id FROM catalog.v_destination dc "
                  "WHERE dc.country = ${i}))")
# AA-847 slice 6d (CIS 256): a line carries its kind / status / length / bbox (the result map fits the bboxes)
DEST_COLS = ("id", "name", "lat", "lng", "located_by", "precision") + LINE_COLS


def search_where(filters: BrowseFilters) -> tuple[str, str, list[Any]]:
    """(scan_where, keep_where, filter_params) for vector_topk.CIS; the query embedding is $1. The views show only
    components of live tours on usable places (CIS 253 filters v_component_embedding with the v_tour_component
    rule: not suspect, not a merged duplicate), so nothing is checked after the scan."""
    frag, filter_params = filters.where_sql(start_index=2, country_clause=COUNTRY_CLAUSE)
    return f"c.embedding IS NOT NULL {frag}", "", filter_params


def exact_first_for(filters: BrowseFilters) -> bool:
    """A country filter bounds the candidates to one country's components: the exact aggregate is
    cheap there and the top-k passes would mostly come up short (AA-712 review finding 1)."""
    return filters.country is not None


def _log_embed(outcome: str, reason: Optional[str] = None) -> None:
    """One CloudWatch line per search (AA-856): cache hit rate and fallbacks. No query text."""
    evt = {"evt": "search_embed", "outcome": outcome, **llm_gateway.query_embed_cache.stats()}
    if reason:
        evt["reason"] = reason
    observability.emit(**evt)  # AA-871: + request_id when bound


# AA-642 S0: keyword-fallback reasons where the model call was refused before it was made.
REASON_BUDGET_CAP = "budget_cap"
PAUSED_REASONS = (llm_gateway.REASON_SPEND_UNREADABLE, "route_unreadable")


async def _query_vector(q: str, pool: _Pool) -> tuple[Optional[list[float]], Optional[str]]:
    """(vector, None) from the cache or one Bedrock call, or (None, reason) when search must fall
    back to keyword ranking: over the daily spend cap (AA-805, reason "budget_cap"), spend counter
    or route config unreadable (AA-642 S0, PAUSED_REASONS), throttle or embed error (AA-856)."""
    global _throttled_until
    # AA-685: model from the gateway route (cached 60 s). AA-642 S0: unreadable -> AiPaused.
    try:
        route = await llm_gateway.load_route(pool, STAGE)
    except llm_gateway.AiPaused as e:
        return None, e.reason
    key = llm_gateway.embed_cache_key(route, QUERY_INPUT_TYPE, q)
    cached = llm_gateway.query_embed_cache.get(key)
    if cached is not None:
        _log_embed("hit")
        return cached, None
    if time.monotonic() < _throttled_until:
        return None, "throttle_cooldown"
    budget = await llm_gateway.budget_status(pool, STAGE)  # AA-1015: + the search stage cap, when set
    if budget["exhausted"]:
        if budget.get("reason") == llm_gateway.REASON_SPEND_UNREADABLE:
            return None, llm_gateway.REASON_SPEND_UNREADABLE
        return None, REASON_BUDGET_CAP
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
            out = await keyword_search(q, filters, pool=pool, limit=limit, reason=reason)
            if reason in PAUSED_REASONS:
                out["ai_paused"] = True
            elif reason == REASON_BUDGET_CAP:
                out["ai_budget_exhausted"] = True
            return out
    vec_literal = _vector_literal(vec)

    scan_where, keep_where, filter_params = search_where(filters)
    params: list[Any] = [vec_literal, *filter_params]

    # The transaction scopes hnsw.ef_search / hnsw.iterative_scan (AA-712). The views need no tenant.
    async def _run(conn) -> list:
        async with conn.transaction():
            rows, mode = await vector_topk.ranked_destinations(
                conn, scan_where=scan_where, keep_where=keep_where, params=params,
                dest_cols=DEST_COLS, limit=limit, exact_first=exact_first_for(filters),
                source=vector_topk.CIS)
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
# number of filtered components. Same filters and live-tour views as the semantic path; the scan is over the
# filtered components only.

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

KEYWORD_SQL = """
    SELECT d.destination_id AS id, d.name, d.lat, d.lng, d.located_by, d.precision, """ + LINE_COLS_SQL + """,
           COUNT(c.component_id) AS component_count, MAX(s.score) AS best_score
    FROM catalog.v_tour_component c
    JOIN catalog.v_destination d ON d.destination_id = c.destination_id
    {score}
    WHERE TRUE {where_frag}
    GROUP BY d.destination_id, d.name, d.lat, d.lng, d.located_by, d.precision, """ + LINE_COLS_SQL + """
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


def keyword_sql(filters: BrowseFilters, limit: int) -> tuple[str, list[Any]]:
    """(sql, filter_params): the patterns array is $1, filter params follow from $2."""
    frag, filter_params = filters.where_sql(start_index=2)
    return KEYWORD_SQL.format(score=_KEYWORD_SCORE, where_frag=frag, limit=int(limit)), filter_params


async def keyword_search(q: str, filters: BrowseFilters, *, pool: _Pool,
                         limit: int = SEARCH_LIMIT, reason: Optional[str] = None) -> dict:
    """Search without a model call (AA-856): ranked by keyword match, no embedding."""
    terms = keyword_terms(q)
    if not terms:
        _log_embed("fallback", reason)
        return {"destinations": [], "ranking": "keyword"}
    sql, filter_params = keyword_sql(filters, limit)
    rows = await pool.fetch(sql, terms, *filter_params)
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
