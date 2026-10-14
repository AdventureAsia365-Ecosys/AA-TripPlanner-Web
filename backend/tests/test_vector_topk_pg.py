"""AA-712 integration — the top-k search returns what the old full-scan aggregate returned.

Runs only with ``pytest --pg-dsn postgresql://postgres@127.0.0.1:55432/postgres`` against a
throwaway LOCAL Postgres that has the pgvector extension available. It creates its own database
(``tp_aa712_<random>``), builds the three tables the queries touch with the real index (HNSW cosine,
tp-001), loads clustered fixture vectors, and drops the database at the end. Non-local hosts are
refused so it can never touch RDS.
"""
from __future__ import annotations

import random
from datetime import datetime, timezone
import uuid
from urllib.parse import urlparse, urlunparse

import pytest

asyncpg = pytest.importorskip("asyncpg")

from backend.assembly import suggestions as sg  # noqa: E402
from backend.browse import search as search_mod  # noqa: E402
from backend.browse.filters import BrowseFilters  # noqa: E402
from backend.shared import vector_topk as vt  # noqa: E402

DIM = 256
N_DEST = 240
COUNTRIES = ("Vietnam", "Laos", "Cambodia", "Thailand")
ACTIVITIES = ("trekking", "culinary", "cultural_heritage", "nature_wildlife")

# Verbatim pre-AA-712 search SQL (browse/search.py build_search_query), the equivalence baseline.
LEGACY_SEARCH_SQL = """
    SELECT d.id, d.name, d.lat, d.lng,
           COUNT(c.id) AS component_count,
           MIN(c.embedding <=> $1::vector) AS best_distance
    FROM shared.destinations d
    JOIN tripplanner.itinerary_components c ON c.destination_id = d.id
    WHERE c.embedding IS NOT NULL
      AND EXISTS (SELECT 1 FROM gold_aa_internal.published_tours pt
                  WHERE pt.tour_id::text = c.source_tour_id
                    AND pt.master_status = 'active'
                    AND pt.deleted_at IS NULL)
      {where_frag}
    GROUP BY d.id, d.name, d.lat, d.lng
    ORDER BY best_distance ASC
    LIMIT {limit}
"""

# Verbatim pre-AA-712 suggestion fallback SQL (assembly/suggestions.py), with the trip countries.
LEGACY_SUGGEST_SQL = """
    SELECT d.id, d.name, d.lat, d.lng, d.country,
           COUNT(c.id) AS component_count,
           MIN(c.embedding <=> $1::vector) AS best_distance
    FROM shared.destinations d
    JOIN tripplanner.itinerary_components c ON c.destination_id = d.id
    WHERE c.embedding IS NOT NULL
      AND d.id <> ALL($2::uuid[])
      AND d.country = ANY($3::text[])
    GROUP BY d.id, d.name, d.lat, d.lng, d.country
    ORDER BY best_distance ASC
    LIMIT {limit}
"""

SCHEMA = f"""
CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA shared; CREATE SCHEMA tripplanner; CREATE SCHEMA gold_aa_internal;
CREATE TABLE shared.destinations (
    id uuid PRIMARY KEY, name text NOT NULL, country text NOT NULL,
    lat double precision, lng double precision);
CREATE TABLE gold_aa_internal.published_tours (
    tour_id uuid PRIMARY KEY, master_status text NOT NULL, deleted_at timestamptz);
CREATE TABLE tripplanner.itinerary_components (
    id uuid PRIMARY KEY, source_tour_id text NOT NULL, source_day_index int NOT NULL,
    destination_id uuid NOT NULL REFERENCES shared.destinations(id),
    activity text NOT NULL, intensity_level text NOT NULL, season_months int[] NOT NULL,
    embedding vector({DIM}));
CREATE INDEX itinerary_components_embedding_idx
    ON tripplanner.itinerary_components USING hnsw (embedding vector_cosine_ops);
CREATE INDEX itinerary_components_destination_idx ON tripplanner.itinerary_components (destination_id);
CREATE INDEX itinerary_components_activity_idx ON tripplanner.itinerary_components (activity);
CREATE INDEX itinerary_components_season_idx ON tripplanner.itinerary_components USING GIN (season_months);
"""


def _vec(rng: random.Random, center: list[float], noise: float) -> list[float]:
    return [x + rng.gauss(0, noise) for x in center]


def _lit(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.5f}" for x in v) + "]"


@pytest.fixture(autouse=True)
def _fresh_version_cache(monkeypatch):
    monkeypatch.setattr(vt, "_iterative", None)  # unit-test fakes may have cached a version


@pytest.fixture(scope="module")
def pg_dsn(request):
    dsn = request.config.getoption("--pg-dsn")
    if not dsn:
        pytest.skip("integration: pass --pg-dsn for a throwaway local Postgres with pgvector")
    host = urlparse(dsn).hostname or "localhost"
    if host not in ("localhost", "127.0.0.1", "::1"):
        pytest.skip(f"integration tests only run against a local Postgres, not {host}")
    return dsn


@pytest.fixture
async def db(pg_dsn):
    """A fresh database with fixtures; dropped afterwards."""
    name = f"tp_aa712_{uuid.uuid4().hex[:8]}"
    admin = await asyncpg.connect(pg_dsn)
    await admin.execute(f'CREATE DATABASE "{name}"')
    parts = urlparse(pg_dsn)
    conn = await asyncpg.connect(urlunparse(parts._replace(path=f"/{name}")))
    try:
        await conn.execute(SCHEMA)
        ctx = await _load(conn)
        yield conn, ctx
    finally:
        await conn.close()
        await admin.execute(f'DROP DATABASE "{name}"')
        await admin.close()


async def _load(conn) -> dict:
    """240 destinations, each a cluster of components around its own centre; 8 topic directions
    shared across destinations so a query matches several places. 3 components of every 40th
    destination belong to an inactive or deleted tour. One 'hub' destination holds 600 components
    so a 200-row top-k is dominated by it (forces the widening path)."""
    rng = random.Random(712)
    topics = [[rng.gauss(0, 1) for _ in range(DIM)] for _ in range(8)]
    dests, comps, tours, centres = [], [], {}, []
    for i in range(N_DEST):
        did = uuid.UUID(int=i + 1)
        country = COUNTRIES[i % len(COUNTRIES)]
        dests.append((did, f"Place {i}", country, 10 + i * 0.05, 100 + i * 0.05))
        topic = topics[i % len(topics)]
        centre = _vec(rng, topic, 0.6)
        centres.append(centre)
        n = 600 if i == 7 else rng.randint(4, 30)
        for j in range(n):
            status, deleted = "active", None
            tour = uuid.UUID(int=10_000 + i * 4 + (j % 4))
            if i % 40 == 3 and j < 3:
                tour = uuid.UUID(int=900_000 + i * 10 + j)
                status = "draft" if j < 2 else "active"
                deleted = None if j < 2 else datetime(2026, 1, 1, tzinfo=timezone.utc)
            tours[tour] = (status, deleted)
            comps.append((uuid.uuid4(), str(tour), j % 10, did, ACTIVITIES[j % len(ACTIVITIES)],
                          "moderate", [1 + (j % 12), 1 + ((j + 5) % 12)], _lit(_vec(rng, centre, 0.35))))
    await conn.executemany("INSERT INTO shared.destinations VALUES ($1,$2,$3,$4,$5)", dests)
    await conn.executemany(
        "INSERT INTO gold_aa_internal.published_tours VALUES ($1,$2,$3)",
        [(t, s, d) for t, (s, d) in tours.items()])
    await conn.executemany(
        "INSERT INTO tripplanner.itinerary_components VALUES ($1,$2,$3,$4,$5,$6,$7,$8::vector)", comps)
    await conn.execute("ANALYZE")
    # a query lands near one place of a topic, like a real search phrase near its best match
    queries = [_lit(_vec(rng, centres[i], 0.35)) for i in (0, 9, 18, 27)]
    return {"topics": topics, "queries": queries, "rng": rng}


async def _legacy_search(conn, q: str, filters: BrowseFilters, limit: int) -> list:
    where_frag, fp = filters.where_sql(start_index=2)
    return await conn.fetch(LEGACY_SEARCH_SQL.format(where_frag=where_frag, limit=limit), q, *fp)


FILTER_CASES = [
    BrowseFilters(),
    BrowseFilters(activity="trekking"),
    BrowseFilters(country="Laos"),
    BrowseFilters(season=4),
    BrowseFilters(activity="culinary", country="Vietnam", season=7),
]


async def _new_search(conn, q: str, f: BrowseFilters, limit: int) -> list:
    vec = [float(x) for x in q.strip("[]").split(",")]
    return (await search_mod.search("x", f, pool=conn, embedder=lambda _q: vec,
                                    limit=limit))["destinations"]


async def _exact_plans(conn) -> None:
    """No plain index scans: the top-k is an exact seq scan + sort (HNSW has no bitmap scan)."""
    await conn.execute("SET enable_indexscan = off")


async def test_search_matches_the_full_scan_aggregate(db):
    """Exact scan: identical destinations, order and counts for every filter set and limit."""
    conn, ctx = db
    await _exact_plans(conn)
    for q in ctx["queries"]:
        for f in FILTER_CASES:
            for limit in (5, 20, 60):
                old = await _legacy_search(conn, q, f, limit)
                new = await _new_search(conn, q, f, limit)
                assert [str(r["id"]) for r in old] == [d["id"] for d in new], (f, limit)
                assert [r["component_count"] for r in old] == [d["component_count"] for d in new]


async def test_search_through_the_hnsw_index_keeps_the_top_destinations(db):
    """HNSW is approximate: through the index the top destinations still match the exact answer
    (these fixtures are random 256-d clusters, harder for HNSW than real embeddings)."""
    conn, ctx = db
    await conn.execute("SET enable_seqscan = off")
    await conn.execute("SET enable_bitmapscan = off")
    for q in ctx["queries"]:
        for f in FILTER_CASES:
            old = [str(r["id"]) for r in await _legacy_search(conn, q, f, 60)]
            new = [d["id"] for d in await _new_search(conn, q, f, 60)]
            assert new[:10] == old[:10], f
            assert len(set(new) & set(old)) >= 0.9 * len(old), f


async def test_inactive_and_deleted_tours_are_never_ranked(db):
    conn, _ = db
    await _exact_plans(conn)
    # a query exactly on an inactive component: the old and new searches both skip it
    row = await conn.fetchrow("""
        SELECT c.embedding::text AS e, c.destination_id FROM tripplanner.itinerary_components c
        JOIN gold_aa_internal.published_tours pt ON pt.tour_id::text = c.source_tour_id
        WHERE pt.master_status <> 'active' LIMIT 1""")
    vec = [float(x) for x in row["e"].strip("[]").split(",")]
    new = (await search_mod.search("x", BrowseFilters(), pool=conn, embedder=lambda _q: vec,
                                   limit=60))["destinations"]
    old = await _legacy_search(conn, row["e"], BrowseFilters(), 60)
    assert [d["id"] for d in new] == [str(r["id"]) for r in old]
    # its destination still has active components, so it may appear — but with the active count
    cnt = await conn.fetchval("""
        SELECT COUNT(*) FROM tripplanner.itinerary_components c
        WHERE c.destination_id = $1 AND EXISTS (SELECT 1 FROM gold_aa_internal.published_tours pt
          WHERE pt.tour_id::text = c.source_tour_id AND pt.master_status = 'active'
            AND pt.deleted_at IS NULL)""", row["destination_id"])
    hit = [d for d in new if d["id"] == str(row["destination_id"])]
    assert not hit or hit[0]["component_count"] == cnt


async def test_hub_destination_forces_widening_and_stays_equal(db):
    conn, ctx = db
    hub = await conn.fetchval("SELECT embedding::text FROM tripplanner.itinerary_components "
                              "WHERE destination_id = $1 LIMIT 1", uuid.UUID(int=8))
    await _exact_plans(conn)
    scan, keep, fp = search_mod.search_where(BrowseFilters())
    async with conn.transaction():
        rows, mode = await vt.ranked_destinations(conn, scan_where=scan, keep_where=keep,
                                                  params=[hub, *fp], dest_cols=search_mod.DEST_COLS,
                                                  limit=60)
    assert mode != "topk:200"  # 600 hub components fill the first 200-row pass
    old = await _legacy_search(conn, hub, BrowseFilters(), 60)
    assert [r["id"] for r in rows] == [r["id"] for r in old]


@pytest.mark.parametrize("f", [BrowseFilters(), BrowseFilters(activity="trekking", season=4),
                               BrowseFilters(country="Laos")])
async def test_top_k_scan_reads_components_alone(db, f):
    """The scan has no join, so it can be an index-ordered HNSW scan (unfiltered: the planner takes
    it here once seq scans are off; at catalog scale it is the cheap plan) or, for a selective filter,
    a btree/bitmap read of the few matching rows + sort — both exact-or-ANN, never a full aggregate."""
    conn, ctx = db
    scan, keep, fp = search_mod.search_where(f)
    sql = vt.topk_sql(scan, keep, search_mod.DEST_COLS, len(fp) + 2, 60)
    await conn.execute("SET enable_seqscan = off")
    plan = "\n".join(r[0] for r in await conn.fetch("EXPLAIN " + sql, ctx["queries"][0], *fp, 200))
    lines = plan.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.strip() == "CTE scan")
    indent = len(lines[start]) - len(lines[start].lstrip())
    body = []
    for ln in lines[start + 1:]:
        if len(ln) - len(ln.lstrip()) <= indent:
            break
        body.append(ln)
    scan_plan = "\n".join(body)
    assert "Join" not in scan_plan and "published_tours" not in scan_plan, plan
    if f == BrowseFilters():
        assert "Index Scan using itinerary_components_embedding_idx" in scan_plan, plan
        assert "Sort" not in scan_plan, plan  # ordered by the index


async def test_suggestion_fallback_matches_the_full_scan_aggregate(db, monkeypatch):
    conn, ctx = db
    await _exact_plans(conn)
    pinned = await conn.fetch("SELECT id, destination_id FROM tripplanner.itinerary_components "
                              "WHERE destination_id IN ($1, $2) LIMIT 4",
                              uuid.UUID(int=2), uuid.UUID(int=6))  # both Laos (i % 4 == 1)

    async def comps(_conn, _trip):
        return [{"id": str(r["id"]), "destination_id": str(r["destination_id"])} for r in pinned]

    monkeypatch.setattr(sg.events_mod, "_current_components", comps)
    out = await sg.suggest(conn, "trip-1", limit=6)  # no coordinates -> no route step
    assert out["mode"] == "taste"
    embs = await conn.fetch("SELECT embedding::text AS e FROM tripplanner.itinerary_components "
                            "WHERE id = ANY($1::uuid[])", [r["id"] for r in pinned])
    taste = sg._avg_vector_literal([sg._parse_vector(r["e"]) for r in embs])  # same taste vector
    old = await conn.fetch(LEGACY_SUGGEST_SQL.format(limit=6 * sg.CANDIDATE_MULTIPLIER), taste,
                           [str(r["destination_id"]) for r in pinned], ["Laos"])
    assert [s["id"] for s in out["suggestions"]] == [str(r["id"]) for r in old][:6]
