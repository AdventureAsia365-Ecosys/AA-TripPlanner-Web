"""AA-712 slice 6b / X4 integration — the browse and assembly readers' SQL over the catalog views, on a real Postgres.

Runs only with ``pytest --pg-dsn postgresql://postgres@127.0.0.1:<port>/postgres`` against a throwaway LOCAL
Postgres (non-local hosts are refused); each test creates and drops its own ``tp_aa712_6b_*`` database.

The CIS catalog views of migration 228 are stood in for by TABLES with the same names and the columns the
readers use (names from AA-Ecosys/docs/contracts/catalog.graph.v1.json and catalog.tour.v1.1.json); the
views' own logic (liveness, suspect destinations) is CIS's and tested there. Two Laos tours and one Vietnam tour;
``tripplanner.component_id_map`` maps an old (pre-flip) id to each catalog component, as tp-017 did for the frozen
place tables that tp-018 dropped (AA-712 X4; until then this file compared both PLACES_SOURCE paths). Without
pgvector the ``vector`` type is a text domain with a SQL cosine-distance ``<=>`` (enough for the query shapes; no
index).
"""
from __future__ import annotations

import json
import math
import random
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse, urlunparse

import pytest

asyncpg = pytest.importorskip("asyncpg")

from backend.assembly import suggestions as suggest_sql  # noqa: E402
from backend.browse import handler as h  # noqa: E402
from backend.browse import routes as routes_mod  # noqa: E402
from backend.browse import search as search_mod  # noqa: E402
from backend.browse.filters import BrowseFilters  # noqa: E402
from backend.shared import vector_topk as vt  # noqa: E402

VECTOR_STANDIN = """
CREATE DOMAIN public.vector AS text;
CREATE FUNCTION public.tp_cos(a text, b text) RETURNS float8 LANGUAGE sql IMMUTABLE AS $$
    WITH x AS (SELECT string_to_array(btrim(a, '[]'), ',')::float8[] AS u,
                      string_to_array(btrim(b, '[]'), ',')::float8[] AS v)
    SELECT 1 - (SELECT sum(u[i] * v[i]) FROM generate_subscripts(u, 1) i)
             / (sqrt((SELECT sum(e * e) FROM unnest(u) e)) * sqrt((SELECT sum(e * e) FROM unnest(v) e)))
      FROM x $$;
CREATE OPERATOR <=> (LEFTARG = text, RIGHTARG = text, FUNCTION = public.tp_cos);
"""

SCHEMA = """
CREATE SCHEMA tripplanner; CREATE SCHEMA catalog;
CREATE TABLE catalog.v_tour (source_system text DEFAULT 'cis', source_id text, tour_id uuid, title text,
    country text, duration_days int);
CREATE TABLE catalog.v_tour_day (tour_id uuid, day_number int, title text);
CREATE TABLE catalog.v_tour_place (tour_id uuid, day_number int, position int, destination_id uuid,
    destination_name text, lat float8, lng float8, country text);
CREATE TABLE catalog.v_destination (source_system text DEFAULT 'cis', source_id text, destination_id uuid,
    name text, country text, lat float8, lng float8, cover_photo_path text, cover_image_url text,
    photo_count bigint DEFAULT 0, created_at timestamptz, updated_at timestamptz, geo_status text,
    located_by text, precision text,
    -- CIS 256 (slice 6d): line shape and bbox; a point's bbox is its own lat/lng
    shape_kind text, shape_status text, shape_source text, length_km float8, min_lat float8, min_lng float8,
    max_lat float8, max_lng float8, shape_lo jsonb);
CREATE TABLE catalog.v_tour_component (source_system text DEFAULT 'cis', source_id text, tour_id uuid,
    built_from_tour_id uuid, is_stale boolean DEFAULT false, component_id uuid, day_index int,
    destination_id uuid, name text, country text, activity text, intensity_level text, season_months int[],
    duration_hint text, text_extract text, place_card_id uuid, embedding vector,
    -- CIS 256 (slice 6d): the day's stretch along a line
    precision text, segment jsonb, segment_basis text, segment_from_destination_id uuid,
    segment_to_destination_id uuid, segment_km float8);
CREATE TABLE catalog.v_tour_stop (source_system text DEFAULT 'cis', source_id text, tour_id uuid, day_index int,
    destination_id uuid, country text, source text, build_id uuid);
CREATE TABLE catalog.v_tour_leg (source_system text DEFAULT 'cis', source_id text, tour_id uuid, day_from int,
    day_to int, days int, start_destination_id uuid, end_destination_id uuid, destination_ids uuid[],
    countries text[], activities text[], build_id uuid);
CREATE TABLE catalog.v_tour_junction (destination_a uuid, destination_b uuid, km float8, cross_border boolean,
    build_id uuid);
-- AA-847 (CIS 253 / tp-017): the HNSW top-k view, the graph nodes, the id-map and the trip event log.
CREATE TABLE catalog.v_component_embedding (component_id uuid, source_id text, destination_id uuid, activity text,
    intensity_level text, season_months int[], embedding vector);
CREATE TABLE catalog.v_graph_node (destination_id uuid, name text, country text, lat float8, lng float8,
    located_by text, precision text);
CREATE TABLE tripplanner.component_id_map (old_id uuid PRIMARY KEY, new_id uuid NOT NULL, matched_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE tripplanner.trip_events (id bigserial PRIMARY KEY, trip_id text, event_type text, payload jsonb);
-- AA-990 (CIS 285): component ids a CIS place rebuild dropped → the component of the same place.
CREATE TABLE catalog.v_component_redirect (component_id uuid, new_component_id uuid);
"""

D = {k: uuid.UUID(int=i + 1) for i, k in enumerate(("VTE", "VV", "LPB", "PKS", "HAN"))}
PLACES = {"VTE": ("Vientiane", "Laos", 17.97, 102.60, "geoapify", "point"),
          "VV": ("Vang Vieng", "Laos", 18.92, 102.45, "geoapify", "point"),
          "LPB": ("Luang Prabang", "Laos", 19.88, 102.13, "llm", "area"),
          "PKS": ("Pakse", "Laos", 15.12, 105.78, "geoapify", "point"),
          "HAN": ("Hanoi", "Vietnam", 21.03, 105.85, "geoapify", "point")}
# source_id, tour_id, title, country, stops by day
TOURS = [
    ("src-a", uuid.UUID(int=101), "Mekong Valley", "Laos", ["VTE", "VV", "LPB", "LPB"]),
    ("src-b", uuid.UUID(int=102), "North to South", "Laos", ["LPB", "LPB", "PKS"]),
    ("src-c", uuid.UUID(int=103), "Hanoi Stay", "Vietnam", ["HAN", "HAN"]),
]
ACT = ("cultural_heritage", "culinary")
JUNCTIONS = [("LPB", "VV", 110.0), ("VV", "LPB", 110.0), ("VTE", "VV", 130.0), ("VV", "VTE", 130.0)]


def _vec(i: int) -> str:
    return "[" + ",".join(str(x) for x in (1.0 + i % 3, 0.5 * (i % 5), 1.0, 0.1 * i)) + "]"


async def _load(conn) -> None:
    for k, (name, country, lat, lng, by, prec) in PLACES.items():
        await conn.execute("INSERT INTO catalog.v_destination (source_id, destination_id, name, country, lat, lng, "
                           "geo_status, located_by, precision, min_lat, min_lng, max_lat, max_lng) "
                           "VALUES ($1,$2,$3,$4,$5,$6,'ok',$7,$8,$5,$6,$5,$6)",
                           str(D[k]), D[k], name, country, lat, lng, by, prec)
    n = 0
    for src, tid, title, country, stops in TOURS:
        await conn.execute("INSERT INTO catalog.v_tour (source_id, tour_id, title, country, duration_days) "
                           "VALUES ($1,$2,$3,$4,$5)", src, tid, title, country, len(stops))
        for day, stop in enumerate(stops, start=1):
            await conn.execute("INSERT INTO catalog.v_tour_stop (source_id, tour_id, day_index, destination_id, "
                               "country, source) VALUES ($1,$2,$3,$4,$5,'overnight')", src, tid, day, D[stop],
                               country)
            await conn.execute("INSERT INTO catalog.v_tour_day VALUES ($1,$2,$3)", tid, day, f"Day {day}")
            await conn.execute("INSERT INTO catalog.v_tour_place VALUES ($1,$2,1,$3,$4,$5,$6,$7)", tid, day,
                               D[stop], PLACES[stop][0], PLACES[stop][2], PLACES[stop][3], country)
            for act in ACT[: 2 if day == 1 else 1]:
                n += 1
                name = f"{PLACES[stop][0]} {act}"
                old_id, new_id = uuid.uuid4(), uuid.uuid5(uuid.NAMESPACE_URL, f"{src}|{day}|{name}")
                await conn.execute(
                    "INSERT INTO catalog.v_tour_component (source_id, tour_id, built_from_tour_id, component_id, "
                    "day_index, destination_id, name, country, activity, intensity_level, season_months, "
                    "duration_hint, text_extract, embedding) VALUES ($1,$2,$2,$3,$4,$5,$6,$7,$8,'leisurely',"
                    "'{1,11}',$9,$10,$11)", src, tid, new_id, day,
                    D[stop], name, country, act, "full_day", name, _vec(n))
                await conn.execute(
                    "INSERT INTO catalog.v_component_embedding VALUES ($1,$2,$3,$4,'leisurely','{1,11}',$5)",
                    new_id, src, D[stop], act, _vec(n))
                # the id a pre-flip draft holds for this component (tp-017)
                await conn.execute("INSERT INTO tripplanner.component_id_map (old_id, new_id, matched_by) "
                                   "VALUES ($1,$2,'name')", old_id, new_id)
        for a in range(1, len(stops) + 1):
            for b in range(a + 1, len(stops) + 1):
                ids = list(dict.fromkeys(D[s] for s in stops[a - 1:b]))
                await conn.execute("INSERT INTO catalog.v_tour_leg (source_id, tour_id, day_from, day_to, days, "
                                   "start_destination_id, end_destination_id, destination_ids, countries) VALUES "
                                   "($1,$2,$3,$4,$5,$6,$7,$8,$9)", src, tid, a, b, b - a + 1, D[stops[a - 1]],
                                   D[stops[b - 1]], ids, [country])
    for k, (name, country, lat, lng, by, prec) in PLACES.items():
        await conn.execute("INSERT INTO catalog.v_graph_node VALUES ($1,$2,$3,$4,$5,$6,$7)", D[k], name, country,
                           lat, lng, by, prec)
    for a, b, km in JUNCTIONS:
        await conn.execute("INSERT INTO catalog.v_tour_junction VALUES ($1,$2,$3,false,NULL)", D[a], D[b], km)


@pytest.fixture(scope="module")
def pg_dsn(request):
    dsn = request.config.getoption("--pg-dsn")
    if not dsn:
        pytest.skip("integration: pass --pg-dsn for a throwaway local Postgres")
    host = urlparse(dsn).hostname or "localhost"
    if host not in ("localhost", "127.0.0.1", "::1"):
        pytest.skip(f"integration tests only run against a local Postgres, not {host}")
    return dsn


class _Pool:
    def __init__(self, conn):
        self.c = conn

    def __getattr__(self, name):
        return getattr(self.c, name)

    def acquire(self):
        conn = self.c

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *a):
                return False
        return _Ctx()


@pytest.fixture
async def pool(pg_dsn, monkeypatch):
    monkeypatch.setattr(vt, "_iterative", None)
    name = f"tp_aa712_6b_{uuid.uuid4().hex[:8]}"
    admin = await asyncpg.connect(pg_dsn)
    await admin.execute(f'CREATE DATABASE "{name}"')
    conn = await asyncpg.connect(urlunparse(urlparse(pg_dsn)._replace(path=f"/{name}")))
    try:
        try:
            await conn.execute("CREATE EXTENSION vector")
        except asyncpg.PostgresError:
            await conn.execute(VECTOR_STANDIN)
        await conn.execute(SCHEMA)
        await _load(conn)
        yield _Pool(conn)
    finally:
        await conn.close()
        await admin.execute(f'DROP DATABASE "{name}"')
        await admin.close()


async def _get(pool, path, query=None):
    resp = await h.route("GET", path, query or {}, pool=pool)
    assert resp["statusCode"] in (200, 404), resp
    assert "x-places-source" not in resp["headers"]          # the switch is gone (AA-712 X4)
    return resp["statusCode"], json.loads(resp["body"])


def _pins(body):
    return sorted((d["id"], d["component_count"]) for d in body["destinations"])


async def test_tiles_countries_and_by_country(pool):
    _, body = await _get(pool, "/browse/tiles/10_100_10")
    # Laos only (Hanoi is at 21N); LPB: 2 tours x 2 days + the extra day-1 activity of tour B
    assert _pins(body) == sorted([(str(D["VTE"]), 2), (str(D["VV"]), 1), (str(D["LPB"]), 5), (str(D["PKS"]), 1)])
    lpb = next(d for d in body["destinations"] if d["name"] == "Luang Prabang")
    assert (lpb["located_by"], lpb["precision"]) == ("llm", "area")
    _, body = await _get(pool, "/browse/tiles/19_102", {"activity": "culinary", "country": "Laos", "season": "11"})
    assert _pins(body) == [(str(D["LPB"]), 1)]
    _, body = await _get(pool, "/browse/countries")
    assert body == {"countries": [{"country": "Laos", "component_count": 9},
                                  {"country": "Vietnam", "component_count": 3}]}
    _, body = await _get(pool, "/browse/by-country", {"country": "Laos"})
    assert _pins(body) == sorted([(str(D["VTE"]), 2), (str(D["VV"]), 1), (str(D["LPB"]), 5), (str(D["PKS"]), 1)])


async def test_destination_detail_lists_the_catalog_components(pool):
    _, body = await _get(pool, f"/browse/destinations/{D['LPB']}")
    assert sorted(c["name"] for c in body["components"]) == [
        "Luang Prabang culinary", "Luang Prabang cultural_heritage", "Luang Prabang cultural_heritage",
        "Luang Prabang cultural_heritage", "Luang Prabang cultural_heritage"]
    assert body["precision"] == "area"
    catalog_ids = {str(r["component_id"]) for r in await pool.fetch(
        "SELECT component_id FROM catalog.v_tour_component WHERE destination_id = $1", D["LPB"])}
    assert {c["id"] for c in body["components"]} == catalog_ids       # stable uuid5 ids


async def test_search_ranks_every_live_tours_components(pool):
    vec = [float(x) for x in _vec(4).strip("[]").split(",")]
    out = {}
    for flt in ({}, {"country": "Laos"}):
        body = await search_mod.search("q", BrowseFilters.from_query(flt), pool=pool, embedder=lambda q: vec)
        out[bool(flt)] = [(d["id"], d["component_count"]) for d in body["destinations"]]
    assert {i for i, _ in out[False]} == {str(v) for v in D.values()}
    assert {i for i, _ in out[True]} == {str(D[k]) for k in ("VTE", "VV", "LPB", "PKS")}
    # the country filter keeps the order of the unfiltered ranking (same MIN(distance) per destination)
    assert [i for i, _ in out[False] if i != str(D["HAN"])] == [i for i, _ in out[True]]


async def test_routes_are_keyed_by_source_id(pool):
    for days in (2, 3, 4, 6):
        _, body = await _get(pool, "/browse/routes", {"country": "Laos", "days": str(days)})
        assert body["proposals"], days
        for p in body["proposals"]:
            for s in p["segments"]:
                assert s["tour_id"] == str(dict((t[0], t[1]) for t in TOURS)[s["source_id"]])
                assert s["tour_name"] is not None   # titles from catalog.v_tour, no tenant needed
            assert all(d["component_id"] for d in p["days"])
    _, body = await _get(pool, "/browse/routes", {"country": "Laos", "days": "6"})
    assert any(p["transfers"] == 1 for p in body["proposals"])   # a chain through a junction


async def test_tour_route_finds_both_tours_on_the_graph(pool):
    a, b = TOURS[0], TOURS[1]
    _, ra = await _get(pool, "/browse/tour-route", {"tour_id": str(a[1]), "source_id": a[0]})
    assert ra["proposal"]["match"] == "tour_graph" and len(ra["proposal"]["days"]) == 4
    _, rb = await _get(pool, "/browse/tour-route", {"tour_id": str(b[1])})
    assert rb["proposal"]["match"] == "tour_graph"
    assert all(d["component_id"] for d in rb["proposal"]["days"])


# ── AA-847 slice 6c: assembly over the views, ids from before the flip through the id-map ────────────────────────

async def _old_ids(pool, src: str) -> list[str]:
    """The pre-flip ids of a tour's components (tp-017 map), in day / name order."""
    return [r["id"] for r in await pool.fetch(
        "SELECT m.old_id::text AS id FROM tripplanner.component_id_map m "
        "JOIN catalog.v_tour_component c ON c.component_id = m.new_id WHERE c.source_id = $1 "
        "ORDER BY c.day_index, c.name", src)]


async def test_a_draft_from_before_the_flip_folds_to_the_catalog_components(pool):
    from backend.assembly import coverage as coverage_mod
    from backend.assembly import events as events_mod

    old = await _old_ids(pool, TOURS[0][0])
    assert len(old) == 5
    trip = "trip-aa847"
    for cid in old:
        await pool.execute("INSERT INTO tripplanner.trip_events (trip_id, event_type, payload) VALUES ($1, "
                           "'add_component', $2::jsonb)", trip, json.dumps({"component_id": cid}))
    # after the flip the browser removes one pin by its NEW id
    new_first = await pool.fetchval("SELECT new_id::text FROM tripplanner.component_id_map WHERE old_id = $1::uuid",
                                    old[0])
    await pool.execute("INSERT INTO tripplanner.trip_events (trip_id, event_type, payload) VALUES ($1, "
                       "'remove_component', $2::jsonb)", trip, json.dumps({"component_id": new_first}))
    comps = await events_mod._current_components(pool, trip)  # noqa: SLF001
    # old and new ids of one component fold together: 5 added, 1 removed
    assert len(comps) == 4 and new_first not in {str(c["id"]) for c in comps}
    assert all(c["source_tour_id"] == TOURS[0][0] for c in comps)
    cov = await coverage_mod.coverage(pool, trip)
    assert cov["coverable"], cov
    assert [leg["tour_name"] for leg in cov["legs"]] == ["Mekong Valley"]


async def test_suggestions_from_a_pre_flip_pin(pool):
    from backend.assembly import suggestions as suggest_mod

    old = await pool.fetchval(
        "SELECT m.old_id::text FROM tripplanner.component_id_map m JOIN catalog.v_tour_component c "
        "ON c.component_id = m.new_id WHERE c.destination_id = $1 ORDER BY c.name LIMIT 1", D["VTE"])
    await pool.execute("INSERT INTO tripplanner.trip_events (trip_id, event_type, payload) VALUES ('t-sug', "
                       "'add_component', $1::jsonb)", json.dumps({"component_id": old}))
    res = await suggest_mod.suggest(pool, "t-sug")
    names = [s["name"] for s in res["suggestions"]]
    assert res["mode"] == "route" and names and "Vientiane" not in names   # next stops of tours through VTE


async def test_a_pin_two_rebuilds_dropped_follows_the_id_map_and_the_cis_redirects(pool):
    """AA-990 (D1): pre-flip id → (tp-017 map) catalog id X; a rebuild dropped X → Y (CIS redirect); the backfill
    inserted Y → served id (map). The fold serves the component; no stop is listed as unavailable."""
    from backend.assembly import events as events_mod

    served = await pool.fetchval("SELECT component_id::text FROM catalog.v_tour_component WHERE source_id = $1 "
                                 "ORDER BY day_index, name LIMIT 1", TOURS[2][0])
    old, x, y = (str(uuid.uuid4()) for _ in range(3))
    await pool.execute("INSERT INTO tripplanner.component_id_map (old_id, new_id, matched_by) VALUES "
                       "($1::uuid, $2::uuid, 'name'), ($3::uuid, $4::uuid, 'name')", old, x, y, served)
    await pool.execute("INSERT INTO catalog.v_component_redirect VALUES ($1::uuid, $2::uuid)", x, y)
    await pool.execute("INSERT INTO tripplanner.trip_events (trip_id, event_type, payload) VALUES ('t-990', "
                       "'add_component', $1::jsonb)", json.dumps({"component_id": old}))
    comps, missing = await events_mod._components_and_missing(pool, "t-990")  # noqa: SLF001
    assert [str(c["id"]) for c in comps] == [served] and missing == []


# ── AA-847 routes gate: the equality-join CHAIN_SQL returns what the range-join form returned ────────────────

# The cis chain query as merged in slice 6b (TP #117), kept verbatim as the reference for the rewrite.
CHAIN_SQL_CIS_V1 = """
    WITH span AS MATERIALIZED (
        SELECT source_id, min(day_index) AS first_day FROM catalog.v_tour_stop GROUP BY source_id
    ), legs AS MATERIALIZED (
        SELECT l.source_id, l.day_from, l.day_to, l.days, l.start_destination_id, l.end_destination_id,
               l.destination_ids
        FROM catalog.v_tour_leg l WHERE $1 = ANY(l.countries) AND l.days < $2::int
    ), a AS (
        SELECT l.* FROM legs l JOIN span sp ON sp.source_id = l.source_id AND l.day_from = sp.first_day
    ), j AS MATERIALIZED (
        SELECT destination_a, destination_b, km FROM catalog.v_tour_junction
    )
    SELECT a.source_id AS a_tour, a.day_from AS a_from, a.day_to AS a_to, a.days AS a_days,
           b.source_id AS b_tour, b.day_from AS b_from, b.day_to AS b_to, b.days AS b_days,
           coalesce(j.km, 0) AS transfer_km,
           cardinality(a.destination_ids) + cardinality(b.destination_ids) AS places
    FROM a
    JOIN legs b ON b.source_id <> a.source_id AND a.days + b.days BETWEEN $2::int - $3::int AND $2::int
    LEFT JOIN j ON j.destination_a = a.end_destination_id AND j.destination_b = b.start_destination_id
    WHERE b.start_destination_id = a.end_destination_id OR j.destination_a IS NOT NULL
    ORDER BY abs(a.days + b.days - $2::int), coalesce(j.km, 0), places DESC
    LIMIT 40
"""


def chain_graph(seed: int, countries: dict[str, int], places_per_country: int = 30, max_days: int = 16):
    """A seeded catalog tour graph with Dev's shape: tours that stay 1-3 nights per place and revisit places
    (legs that start and end at the same place), every day range of a tour as a leg, a few cross-border
    tours, and junctions between distinct places within 150 km in both directions (never a self-pair:
    the graph builder never writes one). Returns (stops, legs, junctions) rows for the stand-in tables."""
    rng = random.Random(seed)
    places: dict[str, list[tuple[uuid.UUID, float, float]]] = {}
    n = 0
    for ci, country in enumerate(countries):
        lat0, lng0 = 14.0 + 3 * ci, 101.0 + 2 * ci
        places[country] = []
        for _ in range(places_per_country):
            n += 1
            places[country].append((uuid.UUID(int=900_000 + n), lat0 + rng.uniform(0, 4), lng0 + rng.uniform(0, 3)))
    every = [p for ps in places.values() for p in ps]
    km = {}
    for i, (a, alat, alng) in enumerate(every):
        for b, blat, blng in every[i + 1:]:
            d = 111.0 * math.hypot(alat - blat, (alng - blng) * math.cos(math.radians((alat + blat) / 2)))
            if d <= 150:
                km[(a, b)] = km[(b, a)] = round(d, 1)
    near = {p[0]: [b for (a, b) in km if a == p[0]] for p in every}
    country_of = {p[0]: c for c, ps in places.items() for p in ps}
    stops, legs = [], []
    t = 0
    for country, n_tours in countries.items():
        for _ in range(n_tours):
            t += 1
            src = f"gen-{t:04d}"
            here = rng.choice(places[country])[0]
            days: list[uuid.UUID] = []
            target = rng.randint(3, max_days)
            while len(days) < target:
                days.extend([here] * min(rng.randint(1, 3), target - len(days)))
                if rng.random() < 0.15 and days:          # loop back to an earlier place
                    here = rng.choice(days)
                else:
                    here = rng.choice(near[here] or [here])
            for d, dest in enumerate(days, start=1):
                stops.append((src, d, dest))
            for a in range(1, len(days) + 1):
                for b in range(a + 1, len(days) + 1):
                    ids = list(dict.fromkeys(days[a - 1:b]))
                    legs.append((src, a, b, b - a + 1, days[a - 1], days[b - 1], ids,
                                 sorted({country_of[x] for x in ids})))
    junctions = [(a, b, v) for (a, b), v in km.items()]
    return stops, legs, junctions


async def _load_chain_graph(conn, stops, legs, junctions) -> None:
    await conn.execute("TRUNCATE catalog.v_tour_stop, catalog.v_tour_leg, catalog.v_tour_junction")
    await conn.copy_records_to_table("v_tour_stop", schema_name="catalog", records=stops,
                                     columns=["source_id", "day_index", "destination_id"])
    await conn.copy_records_to_table("v_tour_leg", schema_name="catalog", records=legs,
                                     columns=["source_id", "day_from", "day_to", "days", "start_destination_id",
                                              "end_destination_id", "destination_ids", "countries"])
    await conn.copy_records_to_table("v_tour_junction", schema_name="catalog", records=junctions,
                                     columns=["destination_a", "destination_b", "km"])
    await conn.execute("ANALYZE catalog.v_tour_stop; ANALYZE catalog.v_tour_leg; ANALYZE catalog.v_tour_junction")


def _no_limit(sql: str) -> str:
    assert sql.count("LIMIT 40") == 1
    return sql.replace("LIMIT 40", "")


async def chain_equivalence(conn, country: str, days: int, slack: int = routes_mod.SLACK) -> dict:
    """Old vs new chain SQL for one argument set: full candidate sets compared with EXCEPT ALL both ways
    (row multiplicity included), and the LIMIT 40 answers compared by their sort keys (ties may pick
    different rows of equal rank, so the rows themselves are not compared)."""
    old, new = _no_limit(CHAIN_SQL_CIS_V1), _no_limit(routes_mod.CHAIN_SQL)
    q = "SELECT count(*) FROM (({}) EXCEPT ALL ({})) x"
    args = (country, days, slack)
    out = {"old_rows": await conn.fetchval(f"SELECT count(*) FROM ({old}) x", *args),
           "new_rows": await conn.fetchval(f"SELECT count(*) FROM ({new}) x", *args),
           "old_minus_new": await conn.fetchval(q.format(old, new), *args),
           "new_minus_old": await conn.fetchval(q.format(new, old), *args)}

    def keys(rows):
        return [(abs(r["a_days"] + r["b_days"] - days), float(r["transfer_km"]), -r["places"]) for r in rows]
    out["top40_old"] = keys(await conn.fetch(CHAIN_SQL_CIS_V1, *args))
    out["top40_new"] = keys(await conn.fetch(routes_mod.CHAIN_SQL, *args))
    out["transfers"] = await conn.fetchval(f"SELECT count(*) FILTER (WHERE transfer_km > 0) FROM ({new}) x", *args)
    return out


async def test_chain_sql_rewrite_returns_the_same_candidates(pool):
    stops, legs, junctions = chain_graph(847, {"Laos": 24, "Vietnam": 8})
    await _load_chain_graph(pool, stops, legs, junctions)
    checked = 0
    for country in ("Laos", "Vietnam"):
        for days in (4, 7, 10, 14, 21):
            r = await chain_equivalence(pool, country, days)
            assert r["old_minus_new"] == 0 and r["new_minus_old"] == 0, (country, days, r)
            assert r["old_rows"] == r["new_rows"], (country, days, r)
            assert r["top40_old"] == r["top40_new"], (country, days)
            checked += r["new_rows"]
            if country == "Laos" and days >= 10:
                assert r["new_rows"] > 500 and 0 < r["transfers"] < r["new_rows"], r  # both kinds of chain
    assert checked > 5000


async def test_chain_sql_counts_a_self_pair_junction_once(pool):
    """The CIS junction table has no CHECK against destination_a = destination_b. The range-join form
    returned one row per such junction INSTEAD of the 0 km row (its km, usually 0); the rewrite keeps the
    single 0 km row. Same answer as long as no self-pair exists (the graph builder writes none)."""
    stops, legs, junctions = chain_graph(848, {"Laos": 10})
    await _load_chain_graph(pool, stops, legs, junctions)
    before = await chain_equivalence(pool, "Laos", 10)
    assert before["old_minus_new"] == before["new_minus_old"] == 0
    ends = await pool.fetch("SELECT DISTINCT end_destination_id AS d FROM catalog.v_tour_leg")
    await pool.executemany("INSERT INTO catalog.v_tour_junction (destination_a, destination_b, km) "
                           "VALUES ($1, $1, 0.0)", [(r["d"],) for r in ends])
    after = await chain_equivalence(pool, "Laos", 10)
    assert after["new_rows"] == before["new_rows"]                 # the rewrite is unaffected
    assert after["old_rows"] == before["old_rows"]                 # one self-pair per place: old replaces the 0 km row
    await pool.executemany("INSERT INTO catalog.v_tour_junction (destination_a, destination_b, km) "
                           "VALUES ($1, $1, 0.0)", [(r["d"],) for r in ends])
    doubled = await chain_equivalence(pool, "Laos", 10)
    assert doubled["new_rows"] == before["new_rows"] and doubled["old_rows"] > before["old_rows"]


async def test_routes_force_custom_plans_inside_their_own_transaction(pool, monkeypatch):
    """SET LOCAL scopes plan_cache_mode to the two candidate reads: the connection is back on auto after."""
    seen = []
    real_fetch = pool.c.fetch

    async def fetch(query, *args):
        seen.append((query.strip()[:30], await pool.c.fetchval("SHOW plan_cache_mode")))
        return await real_fetch(query, *args)
    monkeypatch.setattr(pool, "fetch", fetch, raising=False)   # _Pool delegates the rest to the connection
    body = await routes_mod.proposals(pool, "Laos", 6)
    assert body["proposals"]
    assert seen[0][1] == seen[1][1] == "force_custom_plan"         # single + chain
    assert all(mode == "auto" for _, mode in seen[2:])              # days, components, titles
    assert await pool.c.fetchval("SHOW plan_cache_mode") == "auto"


# ── AA-847 gate after CIS 258: ROUTE_CANDIDATES_SQL without a per-row CTE rescan returns the same rows ───────

# The cis route-candidates query as merged in slice 6c (TP #118), kept verbatim as the reference for the rewrite.
ROUTE_CANDIDATES_SQL_CIS_V1 = f"""
    WITH near AS (
        SELECT n.destination_id, {suggest_sql._KM_SQL} AS transfer_km
        FROM catalog.v_graph_node n
        WHERE {suggest_sql._KM_SQL} <= $4
    ), stops AS MATERIALIZED (
        SELECT source_id, day_index, destination_id FROM catalog.v_tour_stop
    ), here AS (
        SELECT s.source_id, s.day_index, s.destination_id, nr.transfer_km
        FROM stops s JOIN near nr ON nr.destination_id = s.destination_id
    ), nxt AS (
        SELECT h.source_id, h.day_index AS from_day, h.transfer_km,
               (SELECT min(s2.day_index) FROM stops s2
                WHERE s2.source_id = h.source_id AND s2.day_index > h.day_index
                  AND s2.destination_id <> h.destination_id) AS to_day
        FROM here h
    )
    SELECT d.destination_id AS id, d.name, d.lat, d.lng, d.country,
           COUNT(DISTINCT c.component_id) AS component_count,
           MIN(c.embedding <=> $1::vector) AS best_distance,
           MIN(nx.transfer_km) AS transfer_km,
           COUNT(DISTINCT nx.source_id) AS tour_count
    FROM nxt nx
    JOIN catalog.v_tour_component c
      ON c.source_id = nx.source_id
     AND c.day_index > nx.from_day AND c.day_index <= nx.to_day
    JOIN catalog.v_destination d ON d.destination_id = c.destination_id
    WHERE nx.to_day IS NOT NULL AND c.embedding IS NOT NULL AND d.destination_id <> ALL($5::uuid[])
      AND ($7::text IS NULL OR c.activity = $7::text)
      AND ($8::text IS NULL OR c.intensity_level = $8::text)
    GROUP BY d.destination_id, d.name, d.lat, d.lng, d.country
    ORDER BY best_distance ASC
    LIMIT $6
"""

# Round 1 of the AA-847 gate (TP #121 ee5b0ba): the next stop as an equality join, v_tour_component and
# v_destination still joined in the main query. Kept verbatim as the second reference (Dev Laos 213 ms: the views
# were read once per nxt row, see test_route_candidates_views_read_once).
ROUTE_CANDIDATES_SQL_CIS_V2 = f"""
    WITH near AS (
        SELECT n.destination_id, {suggest_sql._KM_SQL} AS transfer_km
        FROM catalog.v_graph_node n
        WHERE {suggest_sql._KM_SQL} <= $4
    ), stops AS MATERIALIZED (
        SELECT source_id, day_index, destination_id FROM catalog.v_tour_stop
    ), here AS (
        SELECT s.source_id, s.day_index, s.destination_id, nr.transfer_km
        FROM stops s JOIN near nr ON nr.destination_id = s.destination_id
    ), nxt AS MATERIALIZED (
        -- the tour's next stop somewhere else (a stay of several nights is skipped over)
        SELECT h.source_id, h.day_index AS from_day, h.transfer_km, min(s2.day_index) AS to_day
        FROM here h
        JOIN stops s2 ON s2.source_id = h.source_id AND s2.day_index > h.day_index
                     AND s2.destination_id <> h.destination_id
        GROUP BY h.source_id, h.day_index, h.destination_id, h.transfer_km
    )
    SELECT d.destination_id AS id, d.name, d.lat, d.lng, d.country,
           COUNT(DISTINCT c.component_id) AS component_count,
           MIN(c.embedding <=> $1::vector) AS best_distance,
           MIN(nx.transfer_km) AS transfer_km,
           COUNT(DISTINCT nx.source_id) AS tour_count
    FROM nxt nx
    JOIN catalog.v_tour_component c
      ON c.source_id = nx.source_id
     AND c.day_index > nx.from_day AND c.day_index <= nx.to_day
    JOIN catalog.v_destination d ON d.destination_id = c.destination_id
    WHERE c.embedding IS NOT NULL AND d.destination_id <> ALL($5::uuid[])
      AND ($7::text IS NULL OR c.activity = $7::text)
      AND ($8::text IS NULL OR c.intensity_level = $8::text)
    GROUP BY d.destination_id, d.name, d.lat, d.lng, d.country
    ORDER BY best_distance ASC, d.destination_id
    LIMIT $6
"""

SUGGEST_ACTS = ("cultural_heritage", "culinary", "trekking", "wildlife")
SUGGEST_INTENSITY = ("leisurely", "moderate", "challenging")


def suggest_graph(seed: int, countries: dict[str, int], places_per_country: int = 40, max_days: int = 16,
                  quirks: bool = True) -> dict:
    """A seeded catalog with Dev's shape for the route-candidates read: tours that stay 1-3 nights per place,
    move to a place within 150 km or loop back, 1-4 components per day (some without an embedding), graph
    nodes for every stop place. With ``quirks`` the stops also carry rows the subquery and the GROUP BY must
    treat alike: duplicate stop rows, a second stop on the same day, a stop without a destination, and
    components on days without a stop. Returns rows for the stand-in tables."""
    rng = random.Random(seed)
    places: list[tuple[uuid.UUID, str, str, float, float]] = []
    by_country: dict[str, list[int]] = {}
    for ci, country in enumerate(countries):
        lat0, lng0 = 14.0 + 3 * ci, 101.0 + 2 * ci
        by_country[country] = []
        for _ in range(places_per_country):
            by_country[country].append(len(places))
            places.append((uuid.UUID(int=700_000 + len(places)), f"Place {len(places)}", country,
                           round(lat0 + rng.uniform(0, 4), 4), round(lng0 + rng.uniform(0, 3), 4)))

    def km(i: int, j: int) -> float:
        a, b = places[i], places[j]
        return 111.0 * math.hypot(a[3] - b[3], (a[4] - b[4]) * math.cos(math.radians((a[3] + b[3]) / 2)))
    near = {i: [j for j in range(len(places)) if j != i and km(i, j) <= 150] for i in range(len(places))}
    stops, comps = [], []
    t = 0
    for country, n_tours in countries.items():
        for _ in range(n_tours):
            t += 1
            src = f"sug-{t:04d}"
            here = rng.choice(by_country[country])
            days: list[int] = []
            target = rng.randint(3, max_days)
            while len(days) < target:
                days.extend([here] * min(rng.randint(1, 3), target - len(days)))
                here = rng.choice(days) if rng.random() < 0.15 else rng.choice(near[here] or [here])
            for d, p in enumerate(days, start=1):
                stops.append((src, d, places[p][0]))
                if quirks and rng.random() < 0.04:
                    stops.append((src, d, places[p][0]))                         # duplicate stop row
                if quirks and rng.random() < 0.04 and near[p]:
                    stops.append((src, d, places[rng.choice(near[p])][0]))       # a second stop that day
                if quirks and rng.random() < 0.01:
                    stops.append((src, d, None))                                 # no destination
                for _ in range(rng.randint(1, 4)):
                    other = quirks and rng.random() < 0.03                       # a component elsewhere
                    dest = places[rng.choice(near[p] or [p])][0] if other else places[p][0]
                    v = None if rng.random() < 0.1 else \
                        "[" + ",".join(f"{rng.uniform(-1, 1):.3f}" for _ in range(4)) + "]"
                    comps.append((src, uuid.UUID(int=10_000_000 + len(comps)), d, dest, rng.choice(SUGGEST_ACTS),
                                  rng.choice(SUGGEST_INTENSITY), v))
            if quirks and rng.random() < 0.1:                                    # a component past the last stop
                comps.append((src, uuid.UUID(int=10_000_000 + len(comps)), len(days) + 1, places[days[-1]][0],
                              "culinary", "leisurely", "[1,0,0,0]"))
    stop_places = {s[2] for s in stops if s[2] is not None}
    nodes = [p for p in places if p[0] in stop_places]
    return {"places": places, "nodes": nodes, "stops": stops, "components": comps}


async def _load_suggest_graph(conn, g: dict) -> None:
    await conn.execute("TRUNCATE catalog.v_tour_stop, catalog.v_tour_component, catalog.v_destination, "
                       "catalog.v_graph_node")
    await conn.copy_records_to_table("v_destination", schema_name="catalog",
                                     records=[(str(p[0]), p[0], p[1], p[2], p[3], p[4]) for p in g["places"]],
                                     columns=["source_id", "destination_id", "name", "country", "lat", "lng"])
    await conn.copy_records_to_table("v_graph_node", schema_name="catalog",
                                     records=[(p[0], p[1], p[2], p[3], p[4]) for p in g["nodes"]],
                                     columns=["destination_id", "name", "country", "lat", "lng"])
    await conn.copy_records_to_table("v_tour_stop", schema_name="catalog", records=g["stops"],
                                     columns=["source_id", "day_index", "destination_id"])
    await conn.execute("CREATE TEMP TABLE tmp_comp (source_id text, component_id uuid, day_index int, "
                       "destination_id uuid, activity text, intensity_level text, embedding text)")
    await conn.copy_records_to_table("tmp_comp", records=g["components"])
    await conn.execute("INSERT INTO catalog.v_tour_component (source_id, component_id, day_index, destination_id, "
                       "activity, intensity_level, embedding) SELECT source_id, component_id, day_index, "
                       "destination_id, activity, intensity_level, embedding::vector FROM tmp_comp; "
                       "DROP TABLE tmp_comp")
    await conn.execute("ANALYZE catalog.v_tour_stop; ANALYZE catalog.v_tour_component; ANALYZE catalog.v_destination; "
                       "ANALYZE catalog.v_graph_node")


def suggest_args(g: dict, seed: int, n: int = 6) -> list[tuple]:
    """Argument sets for ROUTE_CANDIDATES_SQL: a last stop at a graph node, its place (and a few more)
    excluded, with and without the activity / intensity filters. $6 (the LIMIT) is set per call."""
    rng = random.Random(seed)
    out = []
    for i in range(n):
        p = rng.choice(g["nodes"])
        exclude = [p[0]] + [q[0] for q in rng.sample(g["nodes"], min(3, len(g["nodes"])))]
        act = SUGGEST_ACTS[i % len(SUGGEST_ACTS)] if i % 3 == 1 else None
        inten = SUGGEST_INTENSITY[i % len(SUGGEST_INTENSITY)] if i % 3 == 2 else None
        taste = "[" + ",".join(f"{rng.uniform(-1, 1):.3f}" for _ in range(4)) + "]"
        out.append((taste, p[3], p[4], suggest_sql.JUNCTION_KM, exclude, act, inten))
    return out


async def route_candidates_equivalence(conn, args: tuple, limit: int = 24, old: str | None = None,
                                       new: str | None = None) -> dict:
    """Old vs new ROUTE_CANDIDATES_SQL for one argument set: the full answers (LIMIT 1,000,000) compared
    with EXCEPT ALL both ways, and the LIMIT ``limit`` answers by their best_distance keys plus the rows strictly
    ahead of the last key (the old form leaves rows tied on best_distance to the plan). ``old`` defaults to the
    slice 6c text, ``new`` to the current one."""
    taste, lat, lng, km, exclude, act, inten = args
    full = (taste, lat, lng, km, exclude, 1_000_000, act, inten)
    old, new = old or ROUTE_CANDIDATES_SQL_CIS_V1, new or suggest_sql.ROUTE_CANDIDATES_SQL
    q = "SELECT count(*) FROM (({}) EXCEPT ALL ({})) x"
    out = {"old_rows": await conn.fetchval(f"SELECT count(*) FROM ({old}) x", *full),
           "new_rows": await conn.fetchval(f"SELECT count(*) FROM ({new}) x", *full),
           "old_minus_new": await conn.fetchval(q.format(old, new), *full),
           "new_minus_old": await conn.fetchval(q.format(new, old), *full)}
    top = (taste, lat, lng, km, exclude, limit, act, inten)
    o, n = await conn.fetch(old, *top), await conn.fetch(new, *top)
    out["top_old"], out["top_new"] = [r["best_distance"] for r in o], [r["best_distance"] for r in n]
    cut = out["top_new"][-1] if n else None
    out["ahead_equal"] = ({tuple(r) for r in o if r["best_distance"] < cut} ==
                          {tuple(r) for r in n if r["best_distance"] < cut}) if n else not o
    out["new_sorted"] = [(r["best_distance"], str(r["id"])) for r in n] == \
        sorted((r["best_distance"], str(r["id"])) for r in n)
    return out


async def test_route_candidates_rewrite_returns_the_same_rows(pool):
    g = suggest_graph(847, {"Laos": 30, "Vietnam": 10, "Thailand": 8})
    await _load_suggest_graph(pool, g)
    checked = 0
    for args in suggest_args(g, 847, n=12):
        r = await route_candidates_equivalence(pool, args)
        assert r["old_minus_new"] == 0 and r["new_minus_old"] == 0, (args[1:], r)
        assert r["old_rows"] == r["new_rows"] and r["top_old"] == r["top_new"] and r["ahead_equal"], (args[1:], r)
        assert r["new_sorted"]                                    # best_distance, then destination id
        checked += r["new_rows"]
    assert checked > 200


async def test_route_candidates_quirks_alone_change_nothing(pool):
    """Stops the GROUP BY folds (duplicate rows) or the subquery skips (no destination, a same-day second stop)
    give the same rows as the subquery form, on a graph made of little else."""
    g = suggest_graph(848, {"Laos": 12}, places_per_country=10, max_days=8)
    extra = []
    for src, day, dest in g["stops"]:
        extra += [(src, day, dest)] * 2 + [(src, day, None)]
    g["stops"] += extra
    await _load_suggest_graph(pool, g)
    for args in suggest_args(g, 848, n=6):
        r = await route_candidates_equivalence(pool, args)
        assert r["old_minus_new"] == r["new_minus_old"] == 0 and r["old_rows"] == r["new_rows"] > 0, r


async def test_route_candidates_plan_never_rescans_a_cte_per_row(pool):
    """The rewrite has no SubPlan: no CTE Scan runs more than once (the old form's next-stop subquery ran once
    per here row and once per candidate pair)."""
    g = suggest_graph(849, {"Laos": 30, "Vietnam": 10})
    await _load_suggest_graph(pool, g)
    taste, lat, lng, km, exclude, act, inten = suggest_args(g, 849, n=1)[0]
    args = (taste, lat, lng, km, exclude, 24, act, inten)

    async def loops(sql):
        plan = json.loads(await pool.fetchval("EXPLAIN (ANALYZE, FORMAT JSON) " + sql, *args))[0]["Plan"]
        nodes, todo = [], [plan]
        while todo:
            n = todo.pop()
            nodes.append(n)
            todo += n.get("Plans", [])
        return max(n["Actual Loops"] for n in nodes if n["Node Type"] == "CTE Scan"), \
            any(n.get("Subplan Name", "").startswith("SubPlan") for n in nodes)
    old_loops, old_sub = await loops(ROUTE_CANDIDATES_SQL_CIS_V1)
    new_loops, new_sub = await loops(suggest_sql.ROUTE_CANDIDATES_SQL)
    assert old_sub and old_loops > 1
    assert not new_sub and new_loops == 1


# ── AA-847 gate, round 2: the same reads over stand-in security_barrier VIEWS with CIS's nesting ────────────────
# The tables above stand in for the catalog views, so a plan that reads a view once per outer row cannot show on
# them. Here the base tables (CIS column names, the keys and indexes the plans use, FORCE RLS with the view owner's
# policies) carry the view definitions of CIS 253 / 256 / 258 as they are on Dev: catalog._live_tour (not a barrier,
# flattened) under v_tour_component and _live_stop; v_tour_stop, v_graph_node, v_tour_component and v_destination
# are security_barrier views owned by a non-superuser role, and the reads run as a second role (tripplanner's
# place), so RLS applies as on Dev. {owner} is the view owner role.
CATALOG_VIEWS_SCHEMA = """
CREATE SCHEMA shared; CREATE SCHEMA gold_aa_internal; CREATE SCHEMA silver_aa_internal; CREATE SCHEMA geo;
CREATE SCHEMA catalog;
CREATE TABLE shared.destinations (id uuid PRIMARY KEY, name text NOT NULL, country text NOT NULL,
    lat float8 NOT NULL, lng float8 NOT NULL, cover_image_url text, created_at timestamptz NOT NULL DEFAULT now(),
    located_by text, located_at timestamptz, geo_status text NOT NULL DEFAULT 'ok', duplicate_of uuid,
    geo_precision text);
CREATE TABLE shared.place_photo (id uuid PRIMARY KEY, destination_id uuid, tour_id uuid, place_label text,
    width int, height int, match_source text, updated_at timestamptz DEFAULT now(), status text, s3_key_large text,
    file_name text);
CREATE VIEW shared.v_destination_photos AS
SELECT p.destination_id, p.id AS photo_id, '/content/photos/' || p.id::text AS path, p.place_label,
       p.tour_id, p.width, p.height, p.match_source, p.updated_at,
       row_number() OVER (PARTITION BY p.destination_id
                          ORDER BY (p.match_source = 'manual') DESC, p.file_name) AS position
  FROM shared.place_photo p
 WHERE p.status = 'matched' AND p.destination_id IS NOT NULL AND p.s3_key_large IS NOT NULL;
CREATE TABLE gold_aa_internal.published_tours (id uuid PRIMARY KEY, tour_id uuid NOT NULL UNIQUE,
    tenant_id uuid NOT NULL, aa_name text NOT NULL, aa_subtitle text, aa_summary text, aa_description text,
    aa_highlights jsonb, aa_itineraries text, published_at timestamptz DEFAULT now(), master_status text,
    deleted_at timestamptz);
CREATE TABLE silver_aa_internal.raw_tours (tour_id uuid PRIMARY KEY, tenant_id uuid NOT NULL,
    source_group_id uuid, source_version int, sku text, country text, duration text, inclusions text,
    exclusions text, activities text, best_time_to_go text, group_size text, lifecycle_stage text,
    source_status text NOT NULL, deleted_at timestamptz,
    source_key text GENERATED ALWAYS AS ((COALESCE(source_group_id, tour_id))::text) STORED);
CREATE INDEX raw_tours_source_key_idx ON silver_aa_internal.raw_tours (source_key);
CREATE UNIQUE INDEX uq_raw_tours_active_per_group ON silver_aa_internal.raw_tours (source_group_id)
    WHERE source_status = 'active';
CREATE TABLE geo.graph_build (build_id uuid PRIMARY KEY, is_current boolean NOT NULL,
    built_at timestamptz NOT NULL DEFAULT now());
CREATE UNIQUE INDEX graph_build_one_current_uq ON geo.graph_build (is_current) WHERE is_current;
CREATE TABLE geo.graph_node (build_id uuid NOT NULL, destination_id uuid NOT NULL, activities text[] NOT NULL,
    intensity_min text, intensity_max text, season_months int[] NOT NULL, PRIMARY KEY (build_id, destination_id));
CREATE TABLE geo.tour_stop (build_id uuid NOT NULL, source_id text NOT NULL, day_index int NOT NULL,
    destination_id uuid NOT NULL, country text, source text NOT NULL, PRIMARY KEY (build_id, source_id, day_index));
CREATE TABLE geo.tour_place (component_id uuid PRIMARY KEY, source_id text NOT NULL, tour_id uuid NOT NULL,
    day_index int NOT NULL, name text NOT NULL, country text NOT NULL, destination_id uuid, place_card_id uuid,
    activity text NOT NULL, intensity_level text NOT NULL, season_months int[] NOT NULL DEFAULT '{}',
    duration_hint text NOT NULL DEFAULT 'full_day', text_extract text NOT NULL DEFAULT '', embedding vector);
CREATE INDEX tour_place_destination_idx ON geo.tour_place (destination_id);
CREATE INDEX tour_place_source_idx ON geo.tour_place (source_id, day_index);
CREATE TABLE geo.tour_segment (build_id uuid NOT NULL, source_id text NOT NULL, day_index int NOT NULL,
    destination_id uuid NOT NULL, segment jsonb, basis text NOT NULL, from_destination_id uuid,
    to_destination_id uuid, segment_km numeric(8, 1), PRIMARY KEY (build_id, source_id, day_index, destination_id));
CREATE TABLE geo.place_shape (destination_id uuid PRIMARY KEY, kind text NOT NULL DEFAULT 'line',
    status text NOT NULL DEFAULT 'pending', source text, geom_lo jsonb, min_lat float8 NOT NULL,
    min_lng float8 NOT NULL, max_lat float8 NOT NULL, max_lng float8 NOT NULL, length_km numeric(8, 1));

ALTER TABLE gold_aa_internal.published_tours ENABLE ROW LEVEL SECURITY;
ALTER TABLE gold_aa_internal.published_tours FORCE ROW LEVEL SECURITY;
ALTER TABLE silver_aa_internal.raw_tours ENABLE ROW LEVEL SECURITY;
ALTER TABLE silver_aa_internal.raw_tours FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_aa_master_read ON gold_aa_internal.published_tours FOR SELECT TO {owner}
    USING (tenant_id = '00000000-0000-0000-0000-000000000001'::uuid);
CREATE POLICY tenant_isolation ON gold_aa_internal.published_tours
    USING (tenant_id = (NULLIF(current_setting('app.tenant_id', true), ''))::uuid);
CREATE POLICY owner_aa_master_read ON silver_aa_internal.raw_tours FOR SELECT TO {owner}
    USING (tenant_id = '00000000-0000-0000-0000-000000000001'::uuid);
CREATE POLICY tenant_isolation ON silver_aa_internal.raw_tours
    USING (tenant_id = (NULLIF(current_setting('app.tenant_id', true), ''))::uuid);
GRANT USAGE ON SCHEMA shared, gold_aa_internal, silver_aa_internal, geo, catalog TO {owner};
GRANT SELECT ON ALL TABLES IN SCHEMA shared, gold_aa_internal, silver_aa_internal, geo TO {owner};
GRANT CREATE ON SCHEMA catalog TO {owner};
SET ROLE {owner};

-- CIS 258: the live-tour rule, joined on the stored key
CREATE VIEW catalog._live_tour AS
SELECT rt.tour_id,
       rt.source_key AS source_id,
       rt.source_version, rt.sku, rt.country, rt.duration, rt.inclusions, rt.exclusions,
       rt.activities, rt.best_time_to_go, rt.group_size, rt.lifecycle_stage,
       pt.aa_name, pt.aa_subtitle, pt.aa_summary, pt.aa_description, pt.aa_highlights,
       pt.aa_itineraries, pt.published_at
  FROM gold_aa_internal.published_tours pt
  JOIN silver_aa_internal.raw_tours rt ON rt.tour_id = pt.tour_id
 WHERE pt.tenant_id = '00000000-0000-0000-0000-000000000001'::uuid
   AND pt.master_status = 'active'
   AND pt.deleted_at IS NULL
   AND rt.source_status = 'active'
   AND rt.deleted_at IS NULL;

-- CIS 258: components (the embedding column present), the day stretch as a left join on the current build
CREATE VIEW catalog.v_tour_component WITH (security_barrier = true) AS
SELECT 'cis'::text               AS source_system,
       t.source_id,
       t.tour_id,
       gp.tour_id                AS built_from_tour_id,
       (gp.tour_id <> t.tour_id) AS is_stale,
       gp.component_id,
       gp.day_index,
       gp.destination_id,
       gp.name,
       gp.country,
       gp.activity,
       gp.intensity_level,
       gp.season_months,
       gp.duration_hint,
       gp.text_extract,
       gp.place_card_id,
       gp.embedding,
       d.geo_precision           AS precision,
       seg.segment,
       seg.basis                 AS segment_basis,
       seg.from_destination_id   AS segment_from_destination_id,
       seg.to_destination_id     AS segment_to_destination_id,
       seg.segment_km::double precision AS segment_km
  FROM geo.tour_place gp
  JOIN catalog._live_tour t ON t.source_id = gp.source_id
  JOIN shared.destinations d ON d.id = gp.destination_id
                            AND d.geo_status <> 'suspect' AND d.duplicate_of IS NULL
  LEFT JOIN geo.tour_segment seg
         ON seg.build_id = (SELECT b.build_id FROM geo.graph_build b WHERE b.is_current)
        AND seg.source_id = gp.source_id
        AND seg.day_index = gp.day_index
        AND seg.destination_id = gp.destination_id;

-- CIS 256: live stops, never a line
CREATE VIEW catalog._live_stop AS
SELECT t.source_id,
       t.tour_id,
       s.day_index,
       s.destination_id,
       s.country,
       s.source,
       s.build_id
  FROM geo.graph_build b
  JOIN geo.tour_stop s ON s.build_id = b.build_id
  JOIN catalog._live_tour t ON t.source_id = s.source_id
  JOIN shared.destinations d ON d.id = s.destination_id
                            AND d.geo_status <> 'suspect' AND d.duplicate_of IS NULL
                            AND d.geo_precision IS DISTINCT FROM 'line'
 WHERE b.is_current;

-- CIS 253
CREATE VIEW catalog.v_tour_stop WITH (security_barrier = true) AS
SELECT 'cis'::text AS source_system,
       s.source_id,
       s.tour_id,
       s.day_index,
       s.destination_id,
       s.country,
       s.source,
       s.build_id
  FROM catalog._live_stop s;

CREATE VIEW catalog.v_graph_node WITH (security_barrier = true) AS
SELECT n.destination_id,
       d.name,
       d.country,
       d.lat,
       d.lng,
       d.located_by,
       d.geo_precision AS precision,
       live.source_ids,
       live.tour_ids,
       live.stop_count,
       n.activities,
       n.intensity_min,
       n.intensity_max,
       n.season_months,
       n.build_id,
       b.built_at
  FROM geo.graph_build b
  JOIN geo.graph_node n ON n.build_id = b.build_id
  JOIN shared.destinations d ON d.id = n.destination_id
                            AND d.geo_status <> 'suspect' AND d.duplicate_of IS NULL
  JOIN (SELECT x.destination_id,
               array_agg(x.source_id ORDER BY x.source_id) AS source_ids,
               array_agg(x.tour_id ORDER BY x.source_id)   AS tour_ids,
               sum(x.n)::integer                           AS stop_count
          FROM (SELECT s.destination_id, s.source_id, s.tour_id, count(*) AS n
                  FROM catalog._live_stop s
                 GROUP BY s.destination_id, s.source_id, s.tour_id) x
         GROUP BY x.destination_id) live ON live.destination_id = n.destination_id
 WHERE b.is_current;

-- CIS 256
CREATE VIEW catalog.v_destination WITH (security_barrier = true) AS
SELECT 'cis'::text      AS source_system,
       d.id::text       AS source_id,
       d.id             AS destination_id,
       d.name,
       d.country,
       d.lat,
       d.lng,
       photos.cover_path AS cover_photo_path,
       d.cover_image_url,
       COALESCE(photos.n, 0) AS photo_count,
       d.created_at,
       GREATEST(d.created_at, d.located_at, photos.last_update) AS updated_at,
       d.geo_status,
       d.located_by,
       d.geo_precision  AS precision,
       s.kind           AS shape_kind,
       s.status         AS shape_status,
       s.source         AS shape_source,
       s.length_km::double precision AS length_km,
       COALESCE(s.min_lat, d.lat) AS min_lat,
       COALESCE(s.min_lng, d.lng) AS min_lng,
       COALESCE(s.max_lat, d.lat) AS max_lat,
       COALESCE(s.max_lng, d.lng) AS max_lng,
       s.geom_lo        AS shape_lo
  FROM shared.destinations d
  LEFT JOIN geo.place_shape s ON s.destination_id = d.id AND d.geo_precision = 'line'
  LEFT JOIN LATERAL (
      SELECT count(*) AS n, max(p.updated_at) AS last_update,
             (array_agg(p.path) FILTER (WHERE p.position = 1))[1] AS cover_path
        FROM shared.v_destination_photos p
       WHERE p.destination_id = d.id
  ) photos ON true;
RESET ROLE;
"""
AA_TENANT = uuid.UUID("00000000-0000-0000-0000-000000000001")


def catalog_rows(g: dict, seed: int, filler_tours: int = 800, filler_places: int = 1300) -> dict:
    """Base-table rows for CATALOG_VIEWS_SCHEMA from a suggest_graph() catalog, with what the views must hide:
    tours that are not live (another tenant, master not active, deleted, source inactive, no published row),
    suspect / merged / line destinations, an old build's stops, stale components (built from an older tour_id);
    and ``filler_tours`` live tours / ``filler_places`` destinations without graph rows (Dev: ~890 published tours,
    ~1,470 destinations; the graph ~60 tours, ~140 nodes).
    geo.tour_stop has one stop per (build, tour, day), so suggest_graph()'s duplicate / same-day / NULL stop quirks
    are dropped here. The live key is raw_tours.source_key: a group id for half the tours, the tour id otherwise."""
    rng = random.Random(seed)
    other_tenant, gone = uuid.UUID(int=999), datetime(2026, 1, 1, tzinfo=timezone.utc)
    srcs = sorted({s[0] for s in g["stops"]} | {c[0] for c in g["components"]})
    tours, published, key_of = [], [], {}
    for i, src in enumerate(srcs):
        tid, grp = uuid.UUID(int=20_000_000 + i), (uuid.UUID(int=30_000_000 + i) if i % 2 else None)
        key_of[src] = str(grp or tid)
        r = rng.random()
        status, deleted = ("inactive", None) if r < 0.04 else ("active", gone if r < 0.07 else None)
        tours.append((tid, AA_TENANT, grp, "Laos", status, deleted))
        if grp is not None and rng.random() < 0.1:                       # an older version of the same group
            tours.append((uuid.UUID(int=40_000_000 + i), AA_TENANT, grp, "Laos", "superseded", None))
        r = rng.random()
        if r < 0.03:
            continue                                                     # never published
        published.append((uuid.UUID(int=50_000_000 + i), tid,
                          other_tenant if r < 0.06 else AA_TENANT,
                          "draft" if r < 0.09 else "active", gone if r < 0.11 else None, f"Tour {i}"))
    for j in range(filler_tours):
        tid = uuid.UUID(int=60_000_000 + j)
        tours.append((tid, AA_TENANT if j % 9 else other_tenant, None, "Laos", "active", None))
        published.append((uuid.UUID(int=70_000_000 + j), tid, AA_TENANT if j % 9 else other_tenant, "active", None,
                          f"Filler {j}"))
    dests, photos, shapes = [], [], []
    for k, (pid, name, country, lat, lng) in enumerate(g["places"]):
        r = rng.random()
        status, dup, prec = ("suspect", None, "point") if r < 0.03 else \
            ("ok", g["places"][0][0] if 0.03 <= r < 0.05 and k else None, "line" if 0.05 <= r < 0.07 else "point")
        dests.append((pid, name, country, lat, lng, status, dup, prec))
        if prec == "line":
            shapes.append((pid, lat - 0.1, lng - 0.1, lat + 0.1, lng + 0.1, 12.5))
        for n in range(rng.randint(0, 2)):
            photos.append((uuid.UUID(int=80_000_000 + 10 * k + n), pid, "matched", f"k{k}{n}", f"f{n}.jpg"))
    for j in range(filler_places):
        dests.append((uuid.UUID(int=85_000_000 + j), f"Elsewhere {j}", "Vietnam", round(rng.uniform(8, 23), 4),
                      round(rng.uniform(102, 109), 4), "ok", None, "point"))
    current, old = uuid.UUID(int=1), uuid.UUID(int=2)
    stops, seen = [], set()
    for src, day, dest in g["stops"]:
        if dest is None or (src, day) in seen:
            continue
        seen.add((src, day))
        stops.append((current, key_of[src], day, dest, "Laos", "overnight"))
        if rng.random() < 0.05:
            stops.append((old, key_of[src], day, g["places"][rng.randrange(len(g["places"]))][0], "Laos", "overnight"))
    tid_of: dict[str, uuid.UUID] = {}
    for t in tours:
        tid_of.setdefault(str(t[2] or t[0]), t[0])
    comps = []
    for src, cid, day, dest, act, inten, vec in g["components"]:
        built = tid_of[key_of[src]] if rng.random() > 0.05 else uuid.UUID(int=90_000_000 + len(comps))  # stale
        comps.append((cid, key_of[src], built, day, f"C {cid.int % 100000}", "Laos", dest, act, inten, vec))
    segs = [(current, s[1], s[2], s[3], "feature") for s in stops if s[0] == current and rng.random() < 0.3]
    nodes = [(current, p[0]) for p in g["nodes"]] + [(old, p[0]) for p in g["nodes"][:5]]
    return {"tours": tours, "published": published, "dests": dests, "photos": photos, "shapes": shapes,
            "builds": [(current, True), (old, False)], "nodes": nodes, "stops": stops, "comps": comps,
            "segs": segs}


async def load_catalog_views(conn, rows: dict) -> None:
    async def copy(table, records, columns):
        schema, name = table.split(".")
        await conn.copy_records_to_table(name, schema_name=schema, records=records, columns=columns)
    await copy("shared.destinations", rows["dests"],
               ["id", "name", "country", "lat", "lng", "geo_status", "duplicate_of", "geo_precision"])
    await copy("shared.place_photo", rows["photos"], ["id", "destination_id", "status", "s3_key_large", "file_name"])
    await copy("geo.place_shape", rows["shapes"], ["destination_id", "min_lat", "min_lng", "max_lat", "max_lng",
                                                   "length_km"])
    await copy("silver_aa_internal.raw_tours", rows["tours"],
               ["tour_id", "tenant_id", "source_group_id", "country", "source_status", "deleted_at"])
    await copy("gold_aa_internal.published_tours", rows["published"],
               ["id", "tour_id", "tenant_id", "master_status", "deleted_at", "aa_name"])
    await copy("geo.graph_build", rows["builds"], ["build_id", "is_current"])
    await conn.executemany("INSERT INTO geo.graph_node (build_id, destination_id, activities, season_months) "
                           "VALUES ($1, $2, '{}', '{}')", rows["nodes"])
    await copy("geo.tour_stop", rows["stops"], ["build_id", "source_id", "day_index", "destination_id", "country",
                                               "source"])
    await copy("geo.tour_segment", rows["segs"], ["build_id", "source_id", "day_index", "destination_id", "basis"])
    await conn.execute("CREATE TEMP TABLE tmp_place (component_id uuid, source_id text, tour_id uuid, day_index int, "
                       "name text, country text, destination_id uuid, activity text, intensity_level text, "
                       "embedding text)")
    await conn.copy_records_to_table("tmp_place", records=rows["comps"])
    await conn.execute("INSERT INTO geo.tour_place (component_id, source_id, tour_id, day_index, name, country, "
                       "destination_id, activity, intensity_level, embedding) SELECT component_id, source_id, "
                       "tour_id, day_index, name, country, destination_id, activity, intensity_level, "
                       "embedding::vector FROM tmp_place; DROP TABLE tmp_place")
    await conn.execute("ANALYZE")


@pytest.fixture
async def views_pool(pg_dsn):
    """A fresh database with CATALOG_VIEWS_SCHEMA, the views owned by a throwaway non-superuser role and read as a
    second one (the connection runs SET ROLE to it). Both roles and the database are dropped after the test."""
    tag = uuid.uuid4().hex[:8]
    name, owner, reader = f"tp_aa847_rc2_{tag}", f"tp_aa847_owner_{tag}", f"tp_aa847_reader_{tag}"
    admin = await asyncpg.connect(pg_dsn)
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.execute(f"CREATE ROLE {owner} NOLOGIN; CREATE ROLE {reader} NOLOGIN")
    conn = await asyncpg.connect(urlunparse(urlparse(pg_dsn)._replace(path=f"/{name}")))
    try:
        try:
            await conn.execute("CREATE EXTENSION vector")
        except asyncpg.PostgresError:
            await conn.execute(VECTOR_STANDIN)
        await conn.execute(CATALOG_VIEWS_SCHEMA.replace("{owner}", owner))
        await conn.execute(f"GRANT USAGE ON SCHEMA catalog TO {reader}; "
                           f"GRANT SELECT ON ALL TABLES IN SCHEMA catalog TO {reader}")
        wrapped = _Pool(conn)
        wrapped.reader = reader                                    # the caller loads as superuser, then SET ROLE
        yield wrapped
    finally:
        await conn.close()
        await admin.execute(f'DROP DATABASE "{name}"')
        await admin.execute(f"DROP ROLE {reader}; DROP ROLE {owner}")
        await admin.close()


def _plan_nodes(node: dict, in_cte: bool = False):
    """(node, inside a CTE subplan) for every node of an EXPLAIN (FORMAT JSON) plan."""
    in_cte = in_cte or str(node.get("Subplan Name", "")).startswith("CTE ")
    yield node, in_cte
    for child in node.get("Plans", []):
        yield from _plan_nodes(child, in_cte)


async def _explain(conn, sql: str, args: tuple) -> list[tuple[dict, bool]]:
    return list(_plan_nodes(json.loads(await conn.fetchval("EXPLAIN (ANALYZE, FORMAT JSON) " + sql, *args))[0]["Plan"]))


async def _views_graph(conn, seed: int, tours: int = 30, places: int = 60) -> dict:
    g = suggest_graph(seed, {"Laos": tours}, places_per_country=places)
    await load_catalog_views(conn, catalog_rows(g, seed, filler_tours=200, filler_places=300))
    await conn.execute(f"SET ROLE {conn.reader}")
    return g


async def test_route_candidates_round2_same_rows_over_the_views(views_pool):
    """The round 2 text (each catalog view in its own MATERIALIZED CTE) against both frozen references, over the
    security_barrier stand-in views with liveness, unusable destinations, an old build and stale components."""
    g = await _views_graph(views_pool, 850)
    assert await views_pool.fetchval("SELECT count(*) FROM catalog.v_tour_component") < sum(
        1 for c in g["components"])                                # the views do hide rows
    checked = 0
    for args in suggest_args(g, 850, n=8):
        for ref in (ROUTE_CANDIDATES_SQL_CIS_V1, ROUTE_CANDIDATES_SQL_CIS_V2):
            r = await route_candidates_equivalence(views_pool, args, old=ref)
            assert r["old_minus_new"] == 0 and r["new_minus_old"] == 0, (args[1:], r)
            assert r["old_rows"] == r["new_rows"] and r["ahead_equal"], (args[1:], r)
            assert r["new_sorted"]
            if ref is ROUTE_CANDIDATES_SQL_CIS_V2:                  # same tie-break, so the same LIMIT rows
                assert r["top_old"] == r["top_new"]
        checked += r["new_rows"]
    assert checked > 100


async def test_route_candidates_views_read_once(views_pool):
    """Dev Laos after round 1: nxt (estimated at a row) was the outer side of a nested loop and v_tour_component,
    a security_barrier subquery, its inner side, re-run once per nxt row (published_tours scanned 154 times). With
    hash joins off the local planner takes that order: round 1 reads view tables outside any CTE, more than once.
    Round 2 reads every table inside a CTE that runs once (natural plan and the forced one), the main query only
    joins CTEs, and no Seq Scan of published_tours / raw_tours runs more than once."""
    g = await _views_graph(views_pool, 851)
    taste, lat, lng, km, exclude, act, inten = suggest_args(g, 851, n=1)[0]
    args = (taste, lat, lng, km, exclude, 24, act, inten)
    await views_pool.execute("SET enable_hashjoin = off")
    try:
        old = await _explain(views_pool, ROUTE_CANDIDATES_SQL_CIS_V2, args)
        forced = await _explain(views_pool, suggest_sql.ROUTE_CANDIDATES_SQL, args)
    finally:
        await views_pool.execute("RESET enable_hashjoin")
    natural = await _explain(views_pool, suggest_sql.ROUTE_CANDIDATES_SQL, args)
    assert max(n["Actual Loops"] for n, in_cte in old if not in_cte and "Relation Name" in n) > 1
    for plan in (natural, forced):
        assert not [n for n, in_cte in plan if not in_cte and "Relation Name" in n]
        ctes = {n["Subplan Name"]: n["Actual Loops"] for n, _ in plan
                if str(n.get("Subplan Name", "")).startswith("CTE ")}
        assert set(ctes) == {"CTE near", "CTE stops", "CTE nxt", "CTE comp", "CTE dest"} and set(ctes.values()) == {1}
        assert all(n["Actual Loops"] == 1 for n, _ in plan if n["Node Type"] == "Seq Scan"
                   and n.get("Relation Name") in ("published_tours", "raw_tours"))
