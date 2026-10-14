"""AA-712 — destination ranking served by the HNSW index (offline: SQL shape + pass logic)."""
from __future__ import annotations

import re

import pytest

from backend.browse import search as search_mod
from backend.browse.filters import BrowseFilters
from backend.shared import vector_topk as vt

SCAN = "c.embedding IS NOT NULL AND c.activity = $2"
KEEP = search_mod.ACTIVE_TOUR_SQL


def _flat(sql: str) -> str:
    return " ".join(sql.split())


def test_topk_sql_scans_components_alone_ordered_by_distance_with_a_limit():
    sql = _flat(vt.topk_sql(SCAN, KEEP, ("id", "name"), k_param=3, limit=60))
    inner = re.search(r"WITH scan AS MATERIALIZED \((.*?)\), best AS", sql).group(1).strip()
    # the index-servable shape: one table, its own filters, ORDER BY <expr> LIMIT k, no aggregate
    assert "FROM tripplanner.itinerary_components c WHERE c.embedding IS NOT NULL AND c.activity = $2" in inner
    assert inner.endswith("ORDER BY c.embedding <=> $1::vector LIMIT $3")
    assert "JOIN" not in inner and "EXISTS" not in inner and "MIN(" not in inner
    # the active-tour check and the grouping run on the k rows only
    best = re.search(r"best AS \((.*?)\) SELECT", sql).group(1).strip()
    assert "FROM scan c WHERE TRUE AND EXISTS" in best and "pt.master_status = 'active'" in best
    assert "MIN(c.distance) AS best_distance" in best and "GROUP BY c.destination_id" in best
    assert sql.endswith("ORDER BY b.best_distance ASC, od.id LIMIT 60")
    # component_count still counts every matching component of the destination
    assert "WHERE c.destination_id = od.id AND c.embedding IS NOT NULL AND c.activity = $2 AND EXISTS" in sql


def test_search_where_keeps_active_tour_filter_and_has_no_join_in_the_scan():
    scan, keep, params = search_mod.search_where(BrowseFilters(activity="trekking", country="Laos"))
    assert "pt.master_status = 'active'" in keep and "pt.deleted_at IS NULL" in keep
    assert _flat(scan) == ("c.embedding IS NOT NULL AND c.activity = $2 AND c.destination_id = "
                           "ANY(ARRAY(SELECT dc.id FROM shared.destinations dc WHERE dc.country = $3))")
    assert params == ["trekking", "Laos"]


def test_where_sql_for_tiles_is_unchanged():
    frag, params = BrowseFilters(country="Laos", season=4).where_sql(start_index=1)
    assert frag == "AND d.country = $1 AND $2 = ANY(c.season_months)" and params == ["Laos", 4]


def test_ef_search_above_k_and_capped():
    assert vt.ef_search_for(200) == 400
    assert vt.ef_search_for(1000) == 1000
    assert vt.ef_search_for(5) == 40


@pytest.fixture(autouse=True)
def _fresh_version_cache(monkeypatch):
    monkeypatch.setattr(vt, "_iterative", None)


class _Conn:
    def __init__(self, sizes, extversion="0.7.4"):
        self.sizes, self.calls, self.extversion = list(sizes), [], extversion

    async def fetchval(self, sql, *args):
        assert "pg_extension" in sql
        return self.extversion

    async def execute(self, sql, *args):
        self.calls.append(("set", args[0] if args else sql.split("'")[1] + "=" + sql.split("'")[3]))

    async def fetch(self, sql, *args):
        kind = "topk" if "WITH scan AS" in sql else "exact"
        self.calls.append((kind, args[-1] if kind == "topk" else None))
        return [{"id": i} for i in range(self.sizes.pop(0))]


async def test_first_pass_enough_stops_there():
    conn = _Conn([60])
    rows, mode = await vt.ranked_destinations(conn, scan_where=SCAN, keep_where=KEEP, params=["[1]", "trekking"],
                                              dest_cols=("id",), limit=60)
    assert (len(rows), mode) == (60, "topk:200")
    assert conn.calls == [("set", "400"), ("topk", 200)]


async def test_short_pass_widens_then_falls_back_to_exact():
    conn = _Conn([10, 59, 59])
    rows, mode = await vt.ranked_destinations(conn, scan_where=SCAN, keep_where=KEEP, params=["[1]", "trekking"],
                                              dest_cols=("id",), limit=60)
    assert mode == "exact" and len(rows) == 59
    assert conn.calls == [("set", "400"), ("topk", 200), ("set", "1000"), ("topk", 1000),
                          ("exact", None)]


async def test_widened_pass_enough():
    conn = _Conn([10, 60])
    _, mode = await vt.ranked_destinations(conn, scan_where=SCAN, keep_where=KEEP, params=["[1]", "trekking"],
                                           dest_cols=("id",), limit=60)
    assert mode == "topk:1000"


def test_version_parse():
    assert vt._version_tuple("0.8.0") >= (0, 8)
    assert vt._version_tuple("0.7.4") < (0, 8)
    assert vt._version_tuple(None) < (0, 8)


async def test_pgvector_08_uses_iterative_scan():
    conn = _Conn([10, 60], extversion="0.8.0")
    _, mode = await vt.ranked_destinations(conn, scan_where=SCAN, keep_where=KEEP, params=["[1]", "trekking"],
                                           dest_cols=("id",), limit=60)
    assert mode == "topk:1000"
    assert conn.calls == [("set", "hnsw.iterative_scan=relaxed_order"), ("set", "400"), ("topk", 200),
                          ("set", "1000"), ("topk", 1000)]


async def test_version_is_checked_once_per_process():
    conn = _Conn([60, 60, 60])
    for _ in range(2):
        await vt.ranked_destinations(conn, scan_where=SCAN, keep_where=KEEP, params=["[1]"],
                                     dest_cols=("id",), limit=60)
    assert vt._iterative is False
    conn.extversion = "0.8.0"  # not re-read
    await vt.ranked_destinations(conn, scan_where=SCAN, keep_where=KEEP, params=["[1]"],
                                 dest_cols=("id",), limit=1)
    assert vt._iterative is False


async def test_exact_first_runs_one_query_and_no_top_k_pass():
    conn = _Conn([3])
    rows, mode = await vt.ranked_destinations(conn, scan_where=SCAN, keep_where=KEEP, params=["[1]", "trekking"],
                                              dest_cols=("id",), limit=60, exact_first=True)
    assert (len(rows), mode) == (3, "exact")
    assert conn.calls == [("exact", None)]  # no ef_search / iterative_scan settings either


def test_country_filter_goes_exact_first_other_filters_do_not():
    assert search_mod.exact_first_for(BrowseFilters(country="Laos")) is True
    assert search_mod.exact_first_for(BrowseFilters(country="Laos", activity="trekking")) is True
    assert search_mod.exact_first_for(BrowseFilters(activity="trekking", season=4)) is False
    assert search_mod.exact_first_for(BrowseFilters()) is False


class _Pool(_Conn):
    async def fetch(self, sql, *args):
        rows = await super().fetch(sql, *args)
        return [{"id": r["id"], "name": "P", "lat": 1.0, "lng": 2.0, "component_count": 1} for r in rows]

    def transaction(self):
        return _Txn()


class _Txn:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


async def test_country_filtered_search_runs_exactly_one_ranking_query(monkeypatch):
    async def no_tenant(_conn):
        return None
    monkeypatch.setattr(search_mod, "set_catalog_tenant", no_tenant)
    pool = _Pool([2])
    out = await search_mod.search("x", BrowseFilters(country="Bhutan"), pool=pool, embedder=lambda _q: [0.1, 0.2])
    assert len(out["destinations"]) == 2
    assert [c[0] for c in pool.calls] == ["exact"]  # no top-k pass, no ef_search setting


async def test_unfiltered_search_starts_with_the_index_pass(monkeypatch):
    async def no_tenant(_conn):
        return None
    monkeypatch.setattr(search_mod, "set_catalog_tenant", no_tenant)
    pool = _Pool([60])
    await search_mod.search("x", BrowseFilters(), pool=pool, embedder=lambda _q: [0.1, 0.2])
    assert [c[0] for c in pool.calls] == ["set", "topk"]
