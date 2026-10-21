"""Rank destinations by their best component's cosine distance, served by the HNSW index (AA-712).

The old search/suggestion SQL computed ``MIN(c.embedding <=> q) ... GROUP BY destination`` over every
matching component, which reads every 1536-d vector per request (a full scan; the HNSW index
``itinerary_components_embedding_idx`` from tp-001 was never used). At catalog scale (30-45k
components) that is 170-280 MB of vectors per search.

Here the nearest components come from a top-k over ``tripplanner.itinerary_components`` alone
(``ORDER BY c.embedding <=> $1::vector LIMIT k``, no join, so the planner can serve it from the HNSW
index), conditions that need another table (the active-tour check) are applied to those k rows, and
the survivors are grouped per destination. A destination's first appearance in the distance-ordered
component list is its best component, so the first N distinct destinations ARE the N best
destinations by ``MIN(distance)`` — the same answer as the old query whenever the top-k leaves at
least N destinations (exactly so on an exact scan; up to the index's recall on an HNSW scan).

When it does not (many components per destination, selective filters, rows dropped after the scan),
the k widens once, then the exact aggregate runs. A filter set that leaves fewer than N destinations
is usually small, and every filter of the exact query can use a btree/GIN index or the destination
list, so the slow path is only taken where it is not slow.

``component_count`` keeps its old meaning (every matching component of the destination, not only
those in the top-k): it is counted per returned destination through the destination_id index.

``exact_first=True`` skips the top-k passes and runs the exact aggregate directly. Callers set it when
the filters already bound the candidate set to one country's components (search with a country
filter, the suggestion fallback inside the trip's countries): there the exact aggregate is cheap,
and the top-k passes would usually come up short anyway and only add round trips (on pgvector 0.8
each short pass can walk up to ``hnsw.max_scan_tuples`` index tuples before giving up).

Callers pass two WHERE fragments over the alias ``c`` (query vector = ``$1``):
  * ``scan_where`` — conditions on ``itinerary_components`` columns only (they stay inside the
    index-ordered scan; use ``c.destination_id = ANY(ARRAY(SELECT ...))`` for destination
    attributes: an InitPlan array, not a join);
  * ``keep_where`` — ``AND ...`` conditions that read other tables (EXISTS ...) and may use only
    ``c.id``, ``c.destination_id`` and ``c.source_tour_id``; applied after the scan.

Run ``ranked_destinations`` inside a transaction: ``hnsw.ef_search`` is set with
``set_config(..., true)`` (transaction-local), never leaking to other queries on a pooled connection.

AA-847 (AA-712 slice 6c, ADR 0007 Consequences): the same ranking over the CIS catalog (``source=CIS``). The scan
reads ``catalog.v_component_embedding`` (CIS 253: resolved components of live tours, NOT a security_barrier view,
so ``ORDER BY embedding <=> q LIMIT k`` is served by the HNSW index on ``geo.tour_place``); the exact fallback and
the component count read ``catalog.v_tour_component``; destinations come from ``catalog.v_destination``. Each is
read through a subquery that renames its columns to the alias ``c`` the callers use (``c.id``,
``c.source_tour_id``, ``c.destination_id``, ``c.activity`` …), so ``scan_where`` / ``keep_where`` are the same
fragments on both paths except for the destination lookups (``Source.destinations`` / ``Source.dest_id``).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Protocol, Sequence

# k per pass. Before pgvector 0.8 an HNSW scan returns at most hnsw.ef_search rows, and
# scan_where filters inside the index scan count against it, so ef_search is set above k (2x,
# capped at pgvector's maximum of 1000) and k cannot usefully exceed 1000.
TOPK_PASSES: tuple[int, ...] = (200, 1000)
# pgvector >= 0.8 also gets hnsw.iterative_scan, so a pass returns its k rows even when scan_where
# filters reject many index candidates. No wider pass: on the local 45k-row bench a k=4000 pass
# cost more (p95 ~360 ms) than the exact aggregate it was meant to avoid.
EF_SEARCH_MAX = 1000
EF_SEARCH_FACTOR = 2

# pgvector version per process (one database per Lambda container); None = not checked yet.
_iterative: Optional[bool] = None


class _Conn(Protocol):
    async def fetch(self, query: str, *args: Any) -> Any: ...
    async def fetchval(self, query: str, *args: Any) -> Any: ...
    async def execute(self, query: str, *args: Any) -> Any: ...


@dataclass(frozen=True)
class Source:
    """Where the components and destinations are read (relations rendered under the alias ``c`` / ``od`` / ``d``)."""
    scan: str          # the index-ordered top-k scan
    exact: str         # the exact aggregate and the component count
    destinations: str
    dest_id: str       # the destination key column of ``destinations``


LEGACY = Source(
    scan="tripplanner.itinerary_components",
    exact="tripplanner.itinerary_components",
    destinations="shared.destinations",
    dest_id="id",
)
_CIS_COLS = ("component_id AS id, source_id AS source_tour_id, day_index AS source_day_index, destination_id, "
             "activity, intensity_level, season_months, embedding")
CIS = Source(
    scan="(SELECT component_id AS id, source_id AS source_tour_id, destination_id, activity, intensity_level, "
         "season_months, embedding FROM catalog.v_component_embedding)",
    exact=f"(SELECT {_CIS_COLS} FROM catalog.v_tour_component)",
    destinations="catalog.v_destination",
    dest_id="destination_id",
)


def _dest_select(alias: str, cols: Sequence[str], source: Source) -> tuple[str, str]:
    """(select list, group-by list) of ``dest_cols``; ``id`` is the destination key of the source."""
    sel, grp = [], []
    for c in cols:
        expr = f"{alias}.{source.dest_id}" if c == "id" else f"{alias}.{c}"
        sel.append(f"{expr} AS {c}" if c == "id" and source.dest_id != "id" else expr)
        grp.append(expr)
    return ", ".join(sel), ", ".join(grp)


def ef_search_for(k: int) -> int:
    return min(EF_SEARCH_MAX, max(k * EF_SEARCH_FACTOR, 40))


def _version_tuple(v: Optional[str]) -> tuple[int, ...]:
    out = []
    for part in (v or "0").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits or 0))
    return tuple(out)


async def iterative_scan_supported(conn: _Conn) -> bool:
    """pgvector >= 0.8 (hnsw.iterative_scan). Checked once per process."""
    global _iterative
    if _iterative is None:
        v = await conn.fetchval("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        _iterative = _version_tuple(v) >= (0, 8)
    return _iterative


def topk_sql(scan_where: str, keep_where: str, dest_cols: Sequence[str], k_param: int,
             limit: int, source: Source = LEGACY) -> str:
    """Top-k components by distance (index-servable), filtered, grouped to destinations.
    ``k_param`` is the placeholder number bound to k."""
    cols, _ = _dest_select("od", dest_cols, source)
    key = f"od.{source.dest_id}"
    return f"""
        WITH scan AS MATERIALIZED (
            SELECT c.id, c.destination_id, c.source_tour_id,
                   c.embedding <=> $1::vector AS distance
            FROM {source.scan} c
            WHERE {scan_where}
            ORDER BY c.embedding <=> $1::vector
            LIMIT ${k_param}
        ), best AS (
            SELECT c.destination_id, MIN(c.distance) AS best_distance
            FROM scan c
            WHERE TRUE {keep_where}
            GROUP BY c.destination_id
        )
        SELECT {cols}, b.best_distance,
               (SELECT COUNT(*) FROM {source.exact} c
                WHERE c.destination_id = {key} AND {scan_where} {keep_where}) AS component_count
        FROM best b
        JOIN {source.destinations} od ON {key} = b.destination_id
        ORDER BY b.best_distance ASC, {key}
        LIMIT {int(limit)}
    """


def exact_sql(scan_where: str, keep_where: str, dest_cols: Sequence[str], limit: int,
              source: Source = LEGACY) -> str:
    """The pre-AA-712 aggregate over every matching component. Fallback only."""
    cols, group = _dest_select("d", dest_cols, source)
    key = f"d.{source.dest_id}"
    return f"""
        SELECT {cols}, COUNT(c.id) AS component_count,
               MIN(c.embedding <=> $1::vector) AS best_distance
        FROM {source.exact} c
        JOIN {source.destinations} d ON {key} = c.destination_id
        WHERE {scan_where} {keep_where}
        GROUP BY {group}
        ORDER BY best_distance ASC, {key}
        LIMIT {int(limit)}
    """


async def ranked_destinations(
    conn: _Conn,
    *,
    scan_where: str,
    keep_where: str,
    params: Sequence[Any],
    dest_cols: Sequence[str],
    limit: int,
    passes: Optional[Sequence[int]] = None,
    exact_first: bool = False,
    source: Source = LEGACY,
) -> tuple[list, str]:
    """Return (rows, mode) — rows best-first with ``dest_cols`` + component_count + best_distance;
    mode is ``topk:<k>`` or ``exact``. Run inside a transaction. ``exact_first`` skips the top-k
    passes (a candidate set already bounded to a country — see the module docstring)."""
    if exact_first:
        rows = list(await conn.fetch(exact_sql(scan_where, keep_where, dest_cols, limit, source), *params))
        return rows, "exact"
    sql = topk_sql(scan_where, keep_where, dest_cols, len(params) + 1, limit, source)
    if await iterative_scan_supported(conn):
        # relaxed_order is enough: the k rows are re-ordered by MIN(distance) per destination
        await conn.execute("SELECT set_config('hnsw.iterative_scan', 'relaxed_order', true)")
    passes = passes or TOPK_PASSES
    for k in passes:
        await conn.execute("SELECT set_config('hnsw.ef_search', $1, true)", str(ef_search_for(k)))
        rows = list(await conn.fetch(sql, *params, int(k)))
        if len(rows) >= limit:
            return rows, f"topk:{k}"
    rows = list(await conn.fetch(exact_sql(scan_where, keep_where, dest_cols, limit, source), *params))
    return rows, "exact"
