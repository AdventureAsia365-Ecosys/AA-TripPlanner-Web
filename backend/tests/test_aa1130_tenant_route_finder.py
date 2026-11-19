"""AA-1130 (S283 audit T1, root ADR 0019 S278) — the route finder of an agency's planner proposes only the tours the
agency sells, under the agency's own titles. Offline: a recording fake pool.

GET /browse/countries, /browse/routes and /browse/tour-route take `tenant=<slug>`: a served agency narrows them to its
rows of catalog.v_tenant_tour_v1 (tenant_tours.served_scope); the AA-hosted demo tenant and no tenant keep the whole
catalogue; an unknown slug is a 404 and a read that cannot answer a 503, never the unscoped AA list. Invented names and
places only.
"""
from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager

import pytest

from backend.browse import countries as countries_mod
from backend.browse import handler as h
from backend.browse import routes as rt
from backend.browse import tenant_site, tenant_tours

AGENCY = "4b1d7e57-0000-4000-8000-0000000000a1"
DEMO = "00000000-0000-0000-0000-000000000002"
SOLD = "5f1e0c2a-7d3b-4c9e-8a61-2b4d6f8e0a13"     # a tour the agency sells (source_id)
OTHER = "9a3c1e55-2b4d-4f6a-8c7e-1d2f3a4b5c6d"    # a catalogue tour it does not sell
TOUR_SOLD = "770a38dd-4e7b-4b55-9ded-0e9ced757301"
PLACE_A = "1c2d3e4f-5a6b-4c7d-8e9f-0a1b2c3d4e5f"
PLACE_B = "2d3e4f5a-6b7c-4d8e-9f0a-1b2c3d4e5f6a"
CATALOGUE_TITLE = "Catalogue title of the ridge tour"
AGENCY_TITLE = "Quillmere's ridge walk"


def _site_row(tenant_id: str = AGENCY, slug: str = "quillmere-voyages") -> dict:
    return {"tenant_id": uuid.UUID(tenant_id), "slug": slug, "name": "Quillmere Voyages", "kind": "agency",
            "in_service": True, "served": True, "tp_launcher": True, "tp_branding": False, "theme": "{}",
            "locale": "en"}


class _Txn:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class Pool:
    """Answers by SQL: the tenant lookup (fetchrow), the agency's scope (SCOPE_SQL), the catalogue reads by needle.
    Records (sql, args) of every statement."""

    def __init__(self, *, site=None, scope=(), rules=(), scope_error=None, lookup_error=None):
        self.site, self.scope, self.rules = site, list(scope), list(rules)
        self.scope_error, self.lookup_error = scope_error, lookup_error
        self.calls: list[tuple[str, tuple]] = []

    def _rows(self, sql, args):
        self.calls.append((sql, args))
        if sql == tenant_tours.SCOPE_SQL:
            if self.scope_error:
                raise self.scope_error
            return self.scope
        for needle, rows in self.rules:
            if needle in sql:
                return rows
        return []

    async def fetch(self, sql, *args):
        return self._rows(sql, args)

    async def fetchrow(self, sql, *args):
        if sql == tenant_site.TENANT_SQL:
            self.calls.append((sql, args))
            if self.lookup_error:
                raise self.lookup_error
            return self.site
        rows = self._rows(sql, args)
        return rows[0] if rows else None

    async def fetchval(self, sql, *args):
        rows = self._rows(sql, args)
        return next(iter(rows[0].values())) if rows else None

    async def execute(self, sql, *args):
        self.calls.append((sql, args))
        return "OK"

    def transaction(self):
        return _Txn()

    @asynccontextmanager
    async def acquire(self):
        yield self

    def sqls(self) -> list[str]:
        return [s for s, _ in self.calls]


class _PgError(Exception):
    def __init__(self, sqlstate):
        super().__init__("value that must not be logged")
        self.sqlstate = sqlstate


SCOPE_ROWS = [{"source_id": SOLD, "title": AGENCY_TITLE, "country": "Laos", "duration_days": 8}]


def _graph_rules(single_tour: str = SOLD) -> list:
    """One single-tour candidate (days 1-7 of an 8-day tour, first to last stop), its stops and components."""
    days = [(d, PLACE_A if d < 4 else PLACE_B, "Invented Harbour" if d < 4 else "Invented Ridge") for d in range(1, 8)]
    stops = [{"tour_id": single_tour, "day_index": d, "destination_id": dest, "name": n, "lat": 0.0, "lng": 0.0,
              "country": "Laos"} for d, dest, n in days]
    comps = [{"tour_id": single_tour, "day_index": d, "destination_id": dest, "component_id": f"comp-{d}"}
             for d, dest, _ in days]
    return [
        ("DISTINCT ON (l.source_id)", [{"tour_id": single_tour, "day_from": 1, "day_to": 7, "days": 7, "places": 2,
                                        "whole": True}]),
        ("AS a_tour", []),
        ("FROM catalog.v_tour_stop s JOIN catalog.v_destination", stops),
        ("FROM catalog.v_tour_component", comps),
        ("SELECT source_id, tour_id::text AS tour_id, title", [{"source_id": single_tour, "tour_id": TOUR_SOLD,
                                                                "title": CATALOGUE_TITLE, "duration_days": 8}]),
        ("SELECT source_id FROM catalog.v_tour WHERE tour_id", [{"source_id": single_tour}]),
        ("SELECT title FROM catalog.v_tour", [{"title": CATALOGUE_TITLE}]),
        ("COALESCE(t.duration_days, 0)", [{"n": 8}]),
    ]


# --- served_scope ---------------------------------------------------------------------------------------------------


async def test_an_agency_scope_is_its_sold_tours_with_its_own_title():
    pool = Pool(scope=SCOPE_ROWS)
    scope = await tenant_tours.served_scope(tenant_site.shape(_site_row()), pool=pool)
    assert scope == {SOLD: {"title": AGENCY_TITLE, "country": "Laos", "duration_days": 8}}
    ((sql, args),) = pool.calls
    assert args == (AGENCY,) and "WHERE t.tenant_id = $1::uuid" in sql and tenant_tours.VIEW in sql
    assert "SELECT *" not in sql


async def test_the_aa_hosted_tenant_is_not_narrowed():
    pool = Pool(scope=SCOPE_ROWS)
    assert await tenant_tours.served_scope(tenant_site.shape(_site_row(DEMO, "aa-tripplanner")), pool=pool) is None
    assert pool.calls == []


async def test_the_view_absent_is_an_empty_scope_and_an_error_raises():
    site = tenant_site.shape(_site_row())
    assert await tenant_tours.served_scope(site, pool=Pool(scope_error=_PgError("42P01"))) == {}
    with pytest.raises(tenant_tours.TenantToursUnavailable):
        await tenant_tours.served_scope(site, pool=Pool(scope_error=_PgError("57014")))


# --- /browse/routes -------------------------------------------------------------------------------------------------


async def test_an_agency_route_finder_reads_only_its_tours_and_shows_its_title():
    pool = Pool(site=_site_row(), scope=SCOPE_ROWS, rules=_graph_rules())
    resp = await h.route("GET", "/browse/routes", {"country": "Laos", "days": "7", "tenant": "quillmere-voyages"},
                         pool=pool)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    (p,) = body["proposals"]
    assert [s["tour_name"] for s in p["segments"]] == [AGENCY_TITLE]
    assert CATALOGUE_TITLE not in resp["body"]
    assert p["whole_tour"] is True and p["total_days"] == 7 and p["tour_days"] == 8
    single = next(a for s, a in pool.calls if s == rt.SINGLE_SCOPED_SQL)
    chain = next(a for s, a in pool.calls if s == rt.CHAIN_SCOPED_SQL)
    assert single == ("Laos", 7, rt.SLACK, [SOLD]) and chain == ("Laos", 7, rt.SLACK, [SOLD])
    assert rt.SINGLE_SQL not in pool.sqls() and rt.CHAIN_SQL not in pool.sqls()


def test_the_scoped_reads_filter_every_leg_by_the_agency_source_ids():
    for sql in (rt.SINGLE_SCOPED_SQL, rt.CHAIN_SCOPED_SQL):
        assert "AND l.source_id = ANY($4::text[])" in sql and "/*scope*/" not in sql
    # the chain's second leg comes from the same filtered `legs`, so a chain never borrows a tour the agency lacks
    assert rt.CHAIN_SCOPED_SQL.count("ANY($4::text[])") == 1 and "JOIN legs b" in rt.CHAIN_SCOPED_SQL
    for sql in (rt.SINGLE_SQL, rt.CHAIN_SQL):
        assert "$4" not in sql and "/*scope*/" not in sql


async def test_an_agency_that_sells_nothing_gets_no_route_and_no_catalogue_read():
    pool = Pool(site=_site_row(), scope=[], rules=_graph_rules(OTHER))
    resp = await h.route("GET", "/browse/routes", {"country": "Laos", "days": "7", "tenant": "quillmere-voyages"},
                         pool=pool)
    assert resp["statusCode"] == 200 and json.loads(resp["body"])["proposals"] == []
    assert not any("catalog.v_tour_leg" in s for s in pool.sqls())


async def test_aa_planner_and_the_demo_tenant_keep_every_tour_with_the_catalogue_title():
    for query in ({"country": "Laos", "days": "7"},
                  {"country": "Laos", "days": "7", "tenant": "aa-tripplanner"}):
        pool = Pool(site=_site_row(DEMO, "aa-tripplanner"), scope=SCOPE_ROWS, rules=_graph_rules(OTHER))
        body = json.loads((await h.route("GET", "/browse/routes", query, pool=pool))["body"])
        assert [s["tour_name"] for s in body["proposals"][0]["segments"]] == [CATALOGUE_TITLE]
        assert rt.SINGLE_SQL in pool.sqls() and tenant_tours.SCOPE_SQL not in pool.sqls()


async def test_an_unknown_tenant_is_404_and_a_failed_lookup_503_before_any_catalogue_read():
    pool = Pool(site=None, rules=_graph_rules(OTHER))
    resp = await h.route("GET", "/browse/routes", {"country": "Laos", "days": "7", "tenant": "nobody-here"},
                         pool=pool)
    assert resp["statusCode"] == 404 and resp["headers"]["cache-control"] == "no-store"
    pool = Pool(lookup_error=_PgError("57014"), rules=_graph_rules(OTHER))
    resp = await h.route("GET", "/browse/routes", {"country": "Laos", "days": "7", "tenant": "quillmere-voyages"},
                         pool=pool)
    assert resp["statusCode"] == 503 and resp["headers"]["cache-control"] == "no-store"
    pool = Pool(site=_site_row(), scope_error=_PgError("57014"), rules=_graph_rules(OTHER))
    resp = await h.route("GET", "/browse/countries", {"tenant": "quillmere-voyages"}, pool=pool)
    assert resp["statusCode"] == 503
    assert not any("catalog.v_tour_leg" in s or s == countries_mod.COUNTRIES_SQL for s in pool.sqls())


async def test_a_bad_query_is_refused_before_the_tenant_is_looked_up():
    pool = Pool(site=_site_row(), scope=SCOPE_ROWS)
    resp = await h.route("GET", "/browse/routes", {"country": "Laos", "days": "x", "tenant": "quillmere-voyages"},
                         pool=pool)
    assert resp["statusCode"] == 400 and pool.calls == []


# --- /browse/countries ----------------------------------------------------------------------------------------------

COUNTRY_ROWS = [{"country": "Laos", "component_count": 40, "tour_count": 12},
                {"country": "Nepal", "component_count": 30, "tour_count": 9},
                {"country": "Invented Border Land", "component_count": 3, "tour_count": 0}]


async def test_countries_carry_their_tour_count_for_aa_and_the_agency_sees_only_its_own():
    pool = Pool(rules=[("GROUP BY d.country", COUNTRY_ROWS)])
    body = json.loads((await h.route("GET", "/browse/countries", {}, pool=pool))["body"])
    assert [(c["country"], c["tour_count"]) for c in body["countries"]] == [
        ("Laos", 12), ("Nepal", 9), ("Invented Border Land", 0)]

    pool = Pool(site=_site_row(), scope=SCOPE_ROWS, rules=[("GROUP BY d.country", COUNTRY_ROWS)])
    resp = await h.route("GET", "/browse/countries", {"tenant": "quillmere-voyages"}, pool=pool)
    assert resp["statusCode"] == 200 and "max-age" in resp["headers"]["cache-control"]
    assert json.loads(resp["body"]) == {"countries": [{"country": "Laos", "component_count": 40, "tour_count": 1}]}


async def test_an_agency_with_no_tour_has_no_country_and_reads_no_catalogue():
    pool = Pool(site=_site_row(), scope=[], rules=[("GROUP BY d.country", COUNTRY_ROWS)])
    body = json.loads((await h.route("GET", "/browse/countries", {"tenant": "quillmere-voyages"}, pool=pool))["body"])
    assert body == {"countries": []} and countries_mod.COUNTRIES_SQL not in pool.sqls()


def test_the_country_tour_count_uses_the_live_catalogue_tours():
    sql = countries_mod.COUNTRIES_SQL
    assert "FROM catalog.v_tour t" in sql and "lifecycle_stage IS DISTINCT FROM 'retired'" in sql
    assert "LEFT JOIN tours tr ON tr.country_key = lower(d.country)" in sql


# --- /browse/tour-route ---------------------------------------------------------------------------------------------


async def test_an_agency_tour_route_carries_its_title():
    pool = Pool(site=_site_row(), scope=SCOPE_ROWS, rules=_graph_rules())
    resp = await h.route("GET", "/browse/tour-route",
                         {"tour_id": TOUR_SOLD, "source_id": SOLD, "tenant": "quillmere-voyages"}, pool=pool)
    assert resp["statusCode"] == 200
    assert [s["tour_name"] for s in json.loads(resp["body"])["proposal"]["segments"]] == [AGENCY_TITLE]
    assert CATALOGUE_TITLE not in resp["body"]


async def test_an_agency_tour_route_of_a_tour_it_does_not_sell_is_404_without_its_text():
    rules = [("SELECT source_id FROM catalog.v_tour WHERE tour_id", [{"source_id": OTHER}]),
             ("SELECT tour_id::text FROM catalog.v_tour WHERE source_id", [{"tour_id": TOUR_SOLD}])]
    pool = Pool(site=_site_row(), scope=SCOPE_ROWS, rules=rules)
    resp = await h.route("GET", "/browse/tour-route", {"source_id": OTHER, "tenant": "quillmere-voyages"}, pool=pool)
    assert resp["statusCode"] == 404 and json.loads(resp["body"])["tour"] is None


def test_the_fallback_of_an_agency_tour_keeps_no_catalogue_day_title():
    fallback = {"title": CATALOGUE_TITLE, "country": "Laos", "duration_days": 8,
                "days": [{"day": 1, "title": "Catalogue day title"}]}
    proposal, out = rt.scope_tour_route(None, fallback, AGENCY_TITLE)
    assert proposal is None and out == {"title": AGENCY_TITLE, "country": "Laos", "duration_days": 8, "days": []}
    assert fallback["days"]  # the input is not changed in place


# --- the "whole tour" length (all planners) ---------------------------------------------------------------------------


async def test_a_one_tour_proposal_names_the_tour_length_and_a_chain_does_not():
    singles = [{"tour_id": SOLD, "day_from": 1, "day_to": 7, "days": 7, "places": 2, "whole": True}]
    chains = [{"a_tour": SOLD, "a_from": 1, "a_to": 3, "a_days": 3, "b_tour": OTHER, "b_from": 2, "b_to": 5,
               "b_days": 4, "transfer_km": 12.0, "places": 4}]
    rules = [("DISTINCT ON (l.source_id)", singles), ("AS a_tour", chains),
             ("SELECT source_id, tour_id::text AS tour_id, title",
              [{"source_id": SOLD, "tour_id": TOUR_SOLD, "title": CATALOGUE_TITLE, "duration_days": 8},
               {"source_id": OTHER, "tour_id": str(uuid.UUID(int=7)), "title": "Another title", "duration_days": 5}])]
    body = await rt.proposals(Pool(rules=rules), "Laos", 7)
    by_kind = {len(p["segments"]): p for p in body["proposals"]}
    assert by_kind[1]["tour_days"] == 8 and by_kind[2]["tour_days"] is None
