"""AA-856: query-embedding cache + keyword fallback for /browse/search. No DB, no Bedrock."""
from __future__ import annotations

import json

import pytest

from backend.browse import handler as h
from backend.browse import search as search_mod
from backend.browse.filters import BrowseFilters
from backend.shared import llm_gateway


class Pool:
    """Answers fetch by the first matching (needle, rows) rule; records every statement + args."""

    def __init__(self, rules=(), spent=0.0):
        self.rules = list(rules)
        self.spent = spent
        self.calls: list[tuple[str, tuple]] = []

    async def fetch(self, query, *args):
        self.calls.append((query, args))
        for needle, rows in self.rules:
            if needle in query:
                return rows
        return []

    async def fetchrow(self, query, *args):
        self.calls.append((query, args))
        return {"spent": self.spent}

    async def fetchval(self, query, *args):
        self.calls.append((query, args))
        return None

    async def execute(self, query, *args):
        self.calls.append((query, args))
        return "OK"

    def transaction(self):
        return _Txn()


class _Txn:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


HOI_AN = {"id": "d1", "name": "Hoi An", "lat": 15.9, "lng": 108.3, "component_count": 2}


@pytest.fixture
def gateway(monkeypatch):
    """Fake route + embed + record; counts embed calls, keeps the logged calls."""
    state = {"embeds": [], "recorded": [], "fail": None}

    async def load_route(db, stage):
        return llm_gateway.builtin_route(stage)

    def embed_texts(route, texts, *, input_type, failures=None):
        state["embeds"].append(texts[0])
        if state["fail"]:
            failures.append(llm_gateway._failed(route, route.models[0], "acc2", 0, state["fail"]))
            raise llm_gateway.BedrockError(f"All models failed for stage 'tp_search_embed': "
                                           f"cohere-embed-v4@acc2: {state['fail']}")
        return [[0.25] * 8], llm_gateway.Call(route.stage, "embed", "cohere-embed-v4", "acc2",
                                              3, 0, 0.0000004, False)

    async def record(db, call, quality_signal, **kw):
        state["recorded"].append((call.stage, call.failed))

    monkeypatch.setattr(search_mod.llm_gateway, "load_route", load_route)
    monkeypatch.setattr(search_mod.llm_gateway, "embed_texts", embed_texts)
    monkeypatch.setattr(search_mod.llm_gateway, "record", record)
    return state


def _evts(capsys) -> list[dict]:
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()
            if line.startswith('{"evt": "search_embed"')]


# --- normalisation + cache unit ---------------------------------------------------------------

def test_normalise_query_casefolds_and_collapses_whitespace():
    assert llm_gateway.normalise_query("  Beach\tTOWN \n ") == "beach town"
    assert llm_gateway.normalise_query("ＢＥＡＣＨ") == "beach"  # NFKC full-width


def test_cache_key_names_the_model_and_input_type():
    route = llm_gateway.builtin_route("tp_search_embed")
    a = llm_gateway.embed_cache_key(route, "search_query", "Beach Town")
    assert a == llm_gateway.embed_cache_key(route, "search_query", " beach  town ")
    assert a != llm_gateway.embed_cache_key(route, "search_document", "beach town")
    other = llm_gateway.Route("tp_search_embed", "embed",
                              (llm_gateway.Model("other-embed", "cohere", "embed", {"acc2": "x"}),))
    assert a != llm_gateway.embed_cache_key(other, "search_query", "beach town")


def test_cache_lru_bound_evicts_least_recently_used():
    c = llm_gateway.EmbeddingCache(max_entries=2, ttl_seconds=100)
    c.put(("a",), [1.0])
    c.put(("b",), [2.0])
    assert c.get(("a",)) == [1.0]          # a is now most recent
    c.put(("c",), [3.0])                   # evicts b
    assert c.get(("b",)) is None
    assert c.get(("a",)) == [1.0] and c.get(("c",)) == [3.0]
    assert len(c) == 2
    assert c.stats() == {"cache_hits": 3, "cache_misses": 1, "cache_size": 2}


def test_cache_entry_expires_after_ttl():
    now = [0.0]
    c = llm_gateway.EmbeddingCache(max_entries=10, ttl_seconds=60, clock=lambda: now[0])
    c.put(("a",), [0.5, 0.5])
    now[0] = 59.0
    assert c.get(("a",)) == [0.5, 0.5]
    now[0] = 60.0
    assert c.get(("a",)) is None and len(c) == 0


def test_is_throttle():
    assert llm_gateway.is_throttle("ThrottlingException: Too many requests, please wait")
    assert llm_gateway.is_throttle("too many requests")
    assert not llm_gateway.is_throttle("AccessDeniedException")


# --- search: cache ----------------------------------------------------------------------------

async def test_repeated_query_embeds_once_and_logs_hit(gateway, capsys):
    pool = Pool([("best_distance", [HOI_AN])])
    first = await search_mod.search("Beach town", BrowseFilters(), pool=pool)
    again = await search_mod.search("  beach   TOWN ", BrowseFilters(), pool=pool)
    assert first["ranking"] == again["ranking"] == "semantic"
    assert gateway["embeds"] == ["beach town"]          # one Bedrock call, normalised text
    assert gateway["recorded"] == [("tp_search_embed", False)]  # the hit is not logged/billed
    evts = _evts(capsys)
    assert [e["outcome"] for e in evts] == ["miss", "hit"]
    assert evts[-1]["cache_hits"] == 1 and evts[-1]["cache_size"] == 1
    assert "beach" not in json.dumps(evts)              # never the query text


async def test_cache_hit_is_served_over_the_daily_cap(gateway, monkeypatch):
    from backend import config
    monkeypatch.setattr(config, "TP_DAILY_SPEND_CAP_USD", 1.0)
    pool = Pool([("best_distance", [HOI_AN])], spent=0.0)
    await search_mod.search("beach", BrowseFilters(), pool=pool)
    pool.spent = 5.0
    out = await search_mod.search("beach", BrowseFilters(), pool=pool)
    assert out["ranking"] == "semantic" and len(gateway["embeds"]) == 1
    with pytest.raises(search_mod.SearchUnavailable):     # a miss over the cap is still 503
        await search_mod.search("mountain", BrowseFilters(), pool=pool)


# --- search: keyword fallback -----------------------------------------------------------------

async def test_throttled_embed_falls_back_to_keyword_and_logs_it(gateway, capsys):
    gateway["fail"] = "ThrottlingException: Too many requests, please wait before trying again."
    pool = Pool([("best_score", [HOI_AN | {"best_score": 3}])])
    resp = await h.route("GET", "/browse/search", {"q": "Hoi An lanterns", "activity": "culinary"},
                         pool=pool)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["ranking"] == "keyword"
    assert [d["name"] for d in body["destinations"]] == ["Hoi An"]
    assert gateway["recorded"] == [("tp_search_embed", True)]  # failed attempt still logged
    sql, args = next((q, a) for q, a in pool.calls if "best_score" in q)
    assert args[0] == ["%hoi%", "%an%", "%lanterns%"] and args[1] == "culinary"
    assert "c.activity = $2" in sql
    assert "FROM catalog.v_tour_component c" in sql         # live tours only (the view's rule)
    assert "<=>" not in sql
    evt = _evts(capsys)[-1]
    assert evt["outcome"] == "fallback" and evt["reason"] == "throttle"


async def test_after_a_throttle_the_container_skips_the_model_for_the_cooldown(gateway, capsys):
    gateway["fail"] = "ThrottlingException: Too many requests"
    pool = Pool([("best_score", [HOI_AN])])
    await search_mod.search("beach", BrowseFilters(), pool=pool)
    gateway["fail"] = None
    out = await search_mod.search("temple", BrowseFilters(), pool=pool)
    assert out["ranking"] == "keyword"
    assert gateway["embeds"] == ["beach"]                  # no second Bedrock call
    assert _evts(capsys)[-1]["reason"] == "throttle_cooldown"
    search_mod._throttled_until = 0.0                      # cooldown over -> model again
    assert (await search_mod.search("temple", BrowseFilters(), pool=pool))["ranking"] == "semantic"


async def test_other_bedrock_error_falls_back_without_cooldown(gateway, capsys):
    gateway["fail"] = "AccessDeniedException: not authorized"
    pool = Pool()
    out = await search_mod.search("beach", BrowseFilters(), pool=pool)
    assert out == {"destinations": [], "ranking": "keyword"}
    assert _evts(capsys)[-1]["reason"] == "embed_error"
    assert search_mod._throttled_until == 0.0


async def test_keyword_fallback_reads_the_catalog_views_only(gateway):
    gateway["fail"] = "ThrottlingException"
    row = HOI_AN | {"located_by": "geoapify", "precision": "point", "best_score": 2}
    pool = Pool([("best_score", [row])])
    out = await search_mod.search("hoi an", BrowseFilters(country="Vietnam"), pool=pool)
    assert out["destinations"][0]["located_by"] == "geoapify"
    sql, args = next((q, a) for q, a in pool.calls if "best_score" in q)
    assert "catalog.v_tour_component" in sql and "catalog.v_destination" in sql
    assert "tripplanner." not in sql and "published_tours" not in sql and "app.tenant_id" not in sql
    assert "d.country = $2" in sql and args[1] == "Vietnam"


def test_keyword_terms():
    assert search_mod.keyword_terms("Trek to the Annapurna base camp") == [
        "%trek%", "%annapurna%", "%base%", "%camp%"]
    assert search_mod.keyword_terms("the tour") == ["%the%", "%tour%"]   # stopwords only: keep them
    assert search_mod.keyword_terms("a") == []
    assert search_mod.keyword_terms("100%_off \\ x") == ["%100%", "%off%"]  # no LIKE wildcards in
    assert len(search_mod.keyword_terms(" ".join(f"w{i}" for i in range(20)))) == 8


async def test_keyword_fallback_with_no_usable_term_returns_empty_keyword_result(gateway):
    gateway["fail"] = "ThrottlingException"
    pool = Pool()
    out = await search_mod.search("a", BrowseFilters(), pool=pool)   # embedded, then no 2+ char word
    assert gateway["embeds"] == ["a"]
    assert out == {"destinations": [], "ranking": "keyword"}
    assert not any("best_score" in q for q, _ in pool.calls)


@pytest.mark.parametrize("q", ["", "   ", "\t\n", "\u3000", "?!", "%_\\", "\u200b", "\ufeff"])
async def test_query_with_no_word_makes_no_model_call(gateway, q):
    pool = Pool()
    out = await search_mod.search(q, BrowseFilters(), pool=pool)
    assert out == {"destinations": []}
    assert gateway["embeds"] == [] and gateway["recorded"] == [] and pool.calls == []


async def test_route_search_whitespace_query_makes_no_model_call(gateway):
    pool = Pool()
    resp = await h.route("GET", "/browse/search", {"q": " \u200b "}, pool=pool)
    assert resp["statusCode"] == 200 and json.loads(resp["body"]) == {"destinations": []}
    assert gateway["embeds"] == [] and gateway["recorded"] == []


def test_keyword_sql_limit_is_an_int():
    sql, params = search_mod.keyword_sql(BrowseFilters(season=5), 60)
    assert sql.strip().endswith("LIMIT 60") and params == [5] and "$2 = ANY(c.season_months)" in sql
