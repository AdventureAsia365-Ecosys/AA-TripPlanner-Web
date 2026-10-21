"""AA-712 slice 6b integration — the browse readers' SQL on both PLACES_SOURCE paths, on a real Postgres.

Runs only with ``pytest --pg-dsn postgresql://postgres@127.0.0.1:<port>/postgres`` against a throwaway LOCAL
Postgres (non-local hosts are refused); each test creates and drops its own ``tp_aa712_6b_*`` database.

The CIS catalog views of migration 228 are stood in for by TABLES with the same names and the columns the
readers use (names from AA-Ecosys/docs/contracts/catalog.graph.v1.json and catalog.tour.v1.1.json); the
views' own logic (liveness, suspect destinations) is CIS's and tested there. The same two Laos tours and one
Vietnam tour are loaded into the frozen TripPlanner tables, with the legacy key quirk: tour A is keyed by its
published tour_id, tour B by its catalog source_id. Without pgvector the ``vector`` type is a text domain with
a SQL cosine-distance ``<=>`` (enough for the query shapes; no index).
"""
from __future__ import annotations

import json
import uuid
from urllib.parse import urlparse, urlunparse

import pytest

asyncpg = pytest.importorskip("asyncpg")

from backend import config  # noqa: E402
from backend.browse import handler as h  # noqa: E402
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
CREATE SCHEMA shared; CREATE SCHEMA tripplanner; CREATE SCHEMA gold_aa_internal; CREATE SCHEMA catalog;
CREATE TABLE shared.destinations (id uuid PRIMARY KEY, name text, country text, lat float8, lng float8,
    cover_image_url text);
CREATE TABLE gold_aa_internal.published_tours (tour_id uuid PRIMARY KEY, aa_name text, master_status text,
    deleted_at timestamptz);
CREATE TABLE tripplanner.itinerary_components (id uuid PRIMARY KEY, source_tour_id text, source_day_index int,
    destination_id uuid, name text, activity text, intensity_level text, season_months int[],
    duration_hint text, text_extract text, embedding vector);
CREATE TABLE tripplanner.tour_stop (source_tour_id text, day_index int, destination_id uuid);
CREATE TABLE tripplanner.tour_leg (source_tour_id text, day_from int, day_to int, days int,
    start_destination_id uuid, end_destination_id uuid, destination_ids uuid[], countries text[]);
CREATE TABLE tripplanner.tour_junction (destination_a uuid, destination_b uuid, km float8);

CREATE TABLE catalog.v_tour (source_system text DEFAULT 'cis', source_id text, tour_id uuid, title text,
    country text, duration_days int);
CREATE TABLE catalog.v_tour_day (tour_id uuid, day_number int, title text);
CREATE TABLE catalog.v_tour_place (tour_id uuid, day_number int, position int, destination_id uuid,
    destination_name text, lat float8, lng float8, country text);
CREATE TABLE catalog.v_destination (source_system text DEFAULT 'cis', source_id text, destination_id uuid,
    name text, country text, lat float8, lng float8, cover_photo_path text, cover_image_url text,
    photo_count bigint DEFAULT 0, created_at timestamptz, updated_at timestamptz, geo_status text,
    located_by text, precision text);
CREATE TABLE catalog.v_tour_component (source_system text DEFAULT 'cis', source_id text, tour_id uuid,
    built_from_tour_id uuid, is_stale boolean DEFAULT false, component_id uuid, day_index int,
    destination_id uuid, name text, country text, activity text, intensity_level text, season_months int[],
    duration_hint text, text_extract text, place_card_id uuid, embedding vector);
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
"""

D = {k: uuid.UUID(int=i + 1) for i, k in enumerate(("VTE", "VV", "LPB", "PKS", "HAN"))}
PLACES = {"VTE": ("Vientiane", "Laos", 17.97, 102.60, "geoapify", "point"),
          "VV": ("Vang Vieng", "Laos", 18.92, 102.45, "geoapify", "point"),
          "LPB": ("Luang Prabang", "Laos", 19.88, 102.13, "llm", "area"),
          "PKS": ("Pakse", "Laos", 15.12, 105.78, "geoapify", "point"),
          "HAN": ("Hanoi", "Vietnam", 21.03, 105.85, "geoapify", "point")}
# source_id, tour_id, legacy key, title, country, stops by day
TOURS = [
    ("src-a", uuid.UUID(int=101), None, "Mekong Valley", "Laos", ["VTE", "VV", "LPB", "LPB"]),
    ("src-b", uuid.UUID(int=102), "src-b", "North to South", "Laos", ["LPB", "LPB", "PKS"]),
    ("src-c", uuid.UUID(int=103), None, "Hanoi Stay", "Vietnam", ["HAN", "HAN"]),
]
ACT = ("cultural_heritage", "culinary")
JUNCTIONS = [("LPB", "VV", 110.0), ("VV", "LPB", 110.0), ("VTE", "VV", 130.0), ("VV", "VTE", 130.0)]


def _vec(i: int) -> str:
    return "[" + ",".join(str(x) for x in (1.0 + i % 3, 0.5 * (i % 5), 1.0, 0.1 * i)) + "]"


async def _load(conn) -> None:
    for k, (name, country, lat, lng, by, prec) in PLACES.items():
        await conn.execute("INSERT INTO shared.destinations VALUES ($1,$2,$3,$4,$5,NULL)", D[k], name, country,
                           lat, lng)
        await conn.execute("INSERT INTO catalog.v_destination (source_id, destination_id, name, country, lat, lng, "
                           "geo_status, located_by, precision) VALUES ($1,$2,$3,$4,$5,$6,'ok',$7,$8)",
                           str(D[k]), D[k], name, country, lat, lng, by, prec)
    n = 0
    for src, tid, key, title, country, stops in TOURS:
        key = key or str(tid)                         # legacy key quirk: tour_id, or source_id for tour B
        await conn.execute("INSERT INTO catalog.v_tour (source_id, tour_id, title, country, duration_days) "
                           "VALUES ($1,$2,$3,$4,$5)", src, tid, title, country, len(stops))
        if key == str(tid):
            await conn.execute("INSERT INTO gold_aa_internal.published_tours VALUES ($1,$2,'active',NULL)",
                               tid, title)
        for day, stop in enumerate(stops, start=1):
            await conn.execute("INSERT INTO tripplanner.tour_stop VALUES ($1,$2,$3)", key, day, D[stop])
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
                    "INSERT INTO tripplanner.itinerary_components VALUES ($1,$2,$3,$4,$5,$6,'leisurely',"
                    "'{1,11}',$7,$8,$9)", old_id, key, day, D[stop], name, act, "full_day", name, _vec(n))
                await conn.execute(
                    "INSERT INTO catalog.v_tour_component (source_id, tour_id, built_from_tour_id, component_id, "
                    "day_index, destination_id, name, country, activity, intensity_level, season_months, "
                    "duration_hint, text_extract, embedding) VALUES ($1,$2,$2,$3,$4,$5,$6,$7,$8,'leisurely',"
                    "'{1,11}',$9,$10,$11)", src, tid, new_id, day,
                    D[stop], name, country, act, "full_day", name, _vec(n))
                await conn.execute(
                    "INSERT INTO catalog.v_component_embedding VALUES ($1,$2,$3,$4,'leisurely','{1,11}',$5)",
                    new_id, src, D[stop], act, _vec(n))
                await conn.execute("INSERT INTO tripplanner.component_id_map (old_id, new_id, matched_by) "
                                   "VALUES ($1,$2,'name')", old_id, new_id)
        for a in range(1, len(stops) + 1):
            for b in range(a + 1, len(stops) + 1):
                ids = list(dict.fromkeys(D[s] for s in stops[a - 1:b]))
                await conn.execute("INSERT INTO tripplanner.tour_leg VALUES ($1,$2,$3,$4,$5,$6,$7,$8)", key, a, b,
                                   b - a + 1, D[stops[a - 1]], D[stops[b - 1]], ids, [country])
                await conn.execute("INSERT INTO catalog.v_tour_leg (source_id, tour_id, day_from, day_to, days, "
                                   "start_destination_id, end_destination_id, destination_ids, countries) VALUES "
                                   "($1,$2,$3,$4,$5,$6,$7,$8,$9)", src, tid, a, b, b - a + 1, D[stops[a - 1]],
                                   D[stops[b - 1]], ids, [country])
    for k, (name, country, lat, lng, by, prec) in PLACES.items():
        await conn.execute("INSERT INTO catalog.v_graph_node VALUES ($1,$2,$3,$4,$5,$6,$7)", D[k], name, country,
                           lat, lng, by, prec)
    for a, b, km in JUNCTIONS:
        await conn.execute("INSERT INTO tripplanner.tour_junction VALUES ($1,$2,$3)", D[a], D[b], km)
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


async def _both(monkeypatch, pool, path, query=None):
    out = {}
    for mode in ("legacy", "cis"):
        monkeypatch.setattr(config, "PLACES_SOURCE", mode)
        resp = await h.route("GET", path, query or {}, pool=pool)
        assert resp["statusCode"] in (200, 404), resp
        assert resp["headers"]["x-places-source"] == mode
        out[mode] = (resp["statusCode"], json.loads(resp["body"]))
    return out


def _pins(body):
    return sorted((d["id"], d["component_count"]) for d in body["destinations"])


async def test_tiles_countries_and_by_country_agree(pool, monkeypatch):
    r = await _both(monkeypatch, pool, "/browse/tiles/10_100_10")
    assert _pins(r["legacy"][1]) == _pins(r["cis"][1]) and len(r["cis"][1]["destinations"]) == 4  # Laos; Hanoi is at 21N
    lpb = next(d for d in r["cis"][1]["destinations"] if d["name"] == "Luang Prabang")
    assert (lpb["located_by"], lpb["precision"]) == ("llm", "area")
    r = await _both(monkeypatch, pool, "/browse/tiles/19_102", {"activity": "culinary", "country": "Laos",
                                                               "season": "11"})
    assert _pins(r["legacy"][1]) == _pins(r["cis"][1]) == [(str(D["LPB"]), 1)]
    r = await _both(monkeypatch, pool, "/browse/countries")
    assert r["legacy"][1] == r["cis"][1] and [c["country"] for c in r["cis"][1]["countries"]] == ["Laos", "Vietnam"]
    r = await _both(monkeypatch, pool, "/browse/by-country", {"country": "Laos"})
    assert _pins(r["legacy"][1]) == _pins(r["cis"][1]) and len(r["cis"][1]["destinations"]) == 4


async def test_destination_detail_same_components_new_ids(pool, monkeypatch):
    r = await _both(monkeypatch, pool, f"/browse/destinations/{D['LPB']}")
    names = {m: sorted(c["name"] for c in r[m][1]["components"]) for m in r}
    assert names["legacy"] == names["cis"] and len(names["cis"]) == 5
    assert r["cis"][1]["precision"] == "area" and "precision" not in r["legacy"][1]
    ids = {m: {c["id"] for c in r[m][1]["components"]} for m in r}
    assert not ids["legacy"] & ids["cis"]          # stable uuid5 ids, not the frozen table's ids


async def test_search_exact_on_cis_and_the_legacy_source_id_quirk(pool, monkeypatch):
    vec = [float(x) for x in _vec(4).strip("[]").split(",")]
    out = {}
    for mode in ("legacy", "cis"):
        monkeypatch.setattr(config, "PLACES_SOURCE", mode)
        for flt in ({}, {"country": "Laos"}):
            body = await search_mod.search("q", BrowseFilters.from_query(flt), pool=pool, embedder=lambda q: vec)
            out[(mode, bool(flt))] = [(d["id"], d["component_count"]) for d in body["destinations"]]
    # legacy search ranks only components whose source_tour_id is a live published tour_id, so tour B
    # (keyed by source_id) is missing there; the cis path ranks every live tour's components
    assert str(D["PKS"]) not in {i for i, _ in out[("legacy", False)]}
    assert {i for i, _ in out[("cis", False)]} == {str(v) for v in D.values()}
    assert {i for i, _ in out[("cis", True)]} == {str(D[k]) for k in ("VTE", "VV", "LPB", "PKS")}
    # where both see the same components, the order agrees (same MIN(distance) per destination)
    legacy_ids = [i for i, _ in out[("legacy", False)]]
    assert [i for i, _ in out[("cis", False)] if i in legacy_ids and i != str(D["LPB"])] == \
        [i for i in legacy_ids if i != str(D["LPB"])]


async def test_routes_same_proposals_keyed_by_source_id(pool, monkeypatch):
    for days in (2, 3, 4, 6):
        r = await _both(monkeypatch, pool, "/browse/routes", {"country": "Laos", "days": str(days)})
        src_of = {str(t[1]): t[0] for t in TOURS}

        def shape(body, cis):
            return [(p["total_days"], p["transfers"], p["whole_tour"],
                     tuple((s["source_id"] if cis else src_of.get(s["tour_id"], s["tour_id"]),
                            s["day_from"], s["day_to"]) for s in p["segments"]),
                     tuple((d["day"], d.get("destination_id"), d["component_id"] is not None) for d in p["days"]))
                    for p in body["proposals"]]
        assert shape(r["legacy"][1], False) == shape(r["cis"][1], True), days
        for p in r["cis"][1]["proposals"]:
            for s in p["segments"]:
                assert s["tour_id"] == str(dict((t[0], t[1]) for t in TOURS)[s["source_id"]])
                assert s["tour_name"] is not None   # titles from catalog.v_tour, no tenant needed
    r = await _both(monkeypatch, pool, "/browse/routes", {"country": "Laos", "days": "6"})
    assert any(p["transfers"] == 1 for p in r["cis"][1]["proposals"])   # a chain through a junction


async def test_tour_route_cis_finds_both_tours_on_the_graph(pool, monkeypatch):
    a, b = TOURS[0], TOURS[1]
    ra = await _both(monkeypatch, pool, "/browse/tour-route", {"tour_id": str(a[1]), "source_id": a[0]})
    assert ra["legacy"][1]["proposal"]["match"] == ra["cis"][1]["proposal"]["match"] == "tour_graph"
    assert len(ra["cis"][1]["proposal"]["days"]) == 4
    rb = await _both(monkeypatch, pool, "/browse/tour-route", {"tour_id": str(b[1]), "source_id": b[0]})
    # legacy keyed tour B by its source_id, so the tour_id lookup misses the graph (places fallback)
    assert rb["legacy"][1]["proposal"]["match"] == "places"
    assert rb["cis"][1]["proposal"]["match"] == "tour_graph"
    assert all(d["component_id"] for d in rb["cis"][1]["proposal"]["days"])


# ── AA-847 slice 6c: assembly on both paths, ids from before the flip through the id-map ───────────────────────

async def test_a_draft_from_before_the_flip_folds_to_the_same_components_on_cis(pool, monkeypatch):
    from backend.assembly import coverage as coverage_mod
    from backend.assembly import events as events_mod

    old = [r["id"] for r in await pool.fetch(
        "SELECT id::text AS id FROM tripplanner.itinerary_components c WHERE source_tour_id = $1 "
        "ORDER BY source_day_index, name", str(TOURS[0][1]))]
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
    got = {}
    for mode in ("legacy", "cis"):
        monkeypatch.setattr(config, "PLACES_SOURCE", mode)
        comps = await events_mod._current_components(pool, trip)  # noqa: SLF001
        got[mode] = sorted((c["name"], c["source_day_index"]) for c in comps)
        cov = await coverage_mod.coverage(pool, trip)
        assert cov["coverable"], (mode, cov)
    # legacy cannot see the removal by a new id (5 pins); cis folds old and new ids together (4 pins)
    assert len(got["legacy"]) == 5 and len(got["cis"]) == 4
    assert set(got["cis"]) < set(got["legacy"])


async def test_suggestions_on_cis_from_a_pre_flip_pin(pool, monkeypatch):
    """The legacy suggestion path reads tripplanner.tour_graph_node (not in this stand-in); test_route_suggest
    covers it offline."""
    from backend.assembly import suggestions as suggest_mod

    old = await pool.fetchval("SELECT id::text FROM tripplanner.itinerary_components WHERE destination_id = $1 "
                              "ORDER BY name LIMIT 1", D["VTE"])
    await pool.execute("INSERT INTO tripplanner.trip_events (trip_id, event_type, payload) VALUES ('t-sug', "
                       "'add_component', $1::jsonb)", json.dumps({"component_id": old}))
    monkeypatch.setattr(config, "PLACES_SOURCE", "cis")
    res = await suggest_mod.suggest(pool, "t-sug")
    names = [s["name"] for s in res["suggestions"]]
    assert res["mode"] == "route" and names and "Vientiane" not in names   # next stops of tours through VTE
