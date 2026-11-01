"""AA-1026 D5: browse search embeds the query only in the space of geo.tour_place.embedding (CIS 329,
catalog.v_embedding_space_v1). No DB, no Bedrock."""
from __future__ import annotations

import asyncio

import pytest

from backend.browse import search as search_mod
from backend.browse.filters import BrowseFilters
from backend.shared import embedding_space as es
from backend.shared import llm_gateway

COHERE = "cohere-embed-v4/1536/v1"


class MissingView(Exception):
    sqlstate = "42P01"


class Pool:
    def __init__(self, space_rows=None, error=None):
        self.space_rows = space_rows if space_rows is not None else []
        self.error = error
        self.space_reads = 0

    async def fetch(self, query, *args):
        if es.VIEW in query:
            self.space_reads += 1
            if self.error is not None:
                raise self.error
            assert args == es.SEARCH_COLUMN
            return self.space_rows
        return []

    async def fetchrow(self, query, *args):
        return {"spent": 0.0}

    async def fetchval(self, query, *args):
        return None

    async def execute(self, query, *args):
        return "OK"

    def transaction(self):
        return _Txn()


class _Txn:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _row(space_key=COHERE, keys=("cohere-embed-v4", "cohere-embed-v4-acc3"), dim=1536):
    return {"space_key": space_key, "model_keys": list(keys), "dimension": dim, "query_input_type": "search_query"}


@pytest.fixture(autouse=True)
def _fresh():
    es.invalidate()
    llm_gateway.query_embed_cache.clear()
    yield
    es.invalidate()


def _route():
    return llm_gateway.builtin_route("tp_search_embed")


def test_missing_view_means_today_unchanged_and_is_cached():
    pool = Pool(error=MissingView("relation does not exist"))
    assert asyncio.run(es.load_search_space(pool)) is None
    assert asyncio.run(es.load_search_space(pool)) is None
    assert pool.space_reads == 1
    assert es.restrict(_route(), None) == _route()


def test_other_read_errors_pause_vector_search():
    with pytest.raises(llm_gateway.AiPaused) as e:
        asyncio.run(es.load_search_space(Pool(error=RuntimeError("connection reset"))))
    assert e.value.reason == es.REASON_UNREADABLE


def test_restrict_keeps_the_space_models_only():
    route = _route()
    space = asyncio.run(es.load_search_space(Pool([_row(keys=(route.models[0].key,))])))
    assert space.space_key == COHERE
    limited = es.restrict(route, space)
    assert [m.key for m in limited.models] == [route.models[0].key]
    assert es.ledger_signal(space) == {"embedding_space": COHERE}
    assert es.ledger_signal(None) == {}


def test_restrict_refuses_another_space():
    route = _route()
    titan = es.SearchSpace("titan-embed-text-v2/1024/v1", ("titan-embed-text-v2",), 1024, None)
    assert es.restrict(route, titan) is None
    same_dim_other_model = es.SearchSpace("other/1536/v1", ("other-embed",), 1536, None)
    assert es.restrict(route, same_dim_other_model) is None


def test_search_falls_back_to_keywords_when_the_column_moved_to_another_space(monkeypatch):
    embeds = []

    def embed_texts(route, texts, *, input_type, failures=None):
        embeds.append(texts)
        raise AssertionError("must not embed in another space")

    async def keyword_search(q, filters, *, pool, limit, reason):
        return {"destinations": [], "reason": reason}

    monkeypatch.setattr(search_mod.llm_gateway, "embed_texts", embed_texts)
    monkeypatch.setattr(search_mod, "keyword_search", keyword_search)
    pool = Pool([_row("titan-embed-text-v2/1024/v1", ("titan-embed-text-v2",), 1024)])

    async def load_route(db, stage):
        return _route()
    monkeypatch.setattr(search_mod.llm_gateway, "load_route", load_route)
    out = asyncio.run(search_mod.search("quiet beach", BrowseFilters(), pool=pool))
    assert out["reason"] == es.REASON_MISMATCH and embeds == [] and "ai_paused" not in out


def test_search_tags_the_ledger_row_with_the_space(monkeypatch):
    recorded = []

    async def load_route(db, stage):
        return _route()

    def embed_texts(route, texts, *, input_type, failures=None):
        return [[0.25] * 8], llm_gateway.Call(route.stage, "embed", route.models[0].key, "acc2", 3, 0, 0.0, False)

    async def record(db, call, quality_signal, **kw):
        recorded.append(quality_signal)

    monkeypatch.setattr(search_mod.llm_gateway, "load_route", load_route)
    monkeypatch.setattr(search_mod.llm_gateway, "embed_texts", embed_texts)
    monkeypatch.setattr(search_mod.llm_gateway, "record", record)
    vec, reason = asyncio.run(search_mod._query_vector("an unusual query text", Pool([_row()])))
    assert reason is None and vec == [0.25] * 8
    assert recorded[-1]["embedding_space"] == COHERE
