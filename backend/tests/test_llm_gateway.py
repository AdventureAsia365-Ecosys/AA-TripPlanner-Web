"""Unit tests for the Model Gateway client (llm_gateway) and the Bedrock satellite transport —
fully stubbed, no live AWS, no DB."""
from __future__ import annotations

import json

import pytest

from backend import config
from backend.shared import bedrock_satellite as bs
from backend.shared import llm_gateway as gw


@pytest.fixture(autouse=True)
def _role_configured(monkeypatch):
    # The two satellite accounts use DIFFERENT role names in production (acc3
    # AA3-Bedrock-Invoker / acc1 AA-Bedrock-Invoker); mirror that here.
    monkeypatch.setattr(config, "BEDROCK_ROLE_NAME", "")
    monkeypatch.setattr(config, "BEDROCK_ROLE_NAME_PRIMARY", "PrimaryBedrockRole")
    monkeypatch.setattr(config, "BEDROCK_ROLE_NAME_FALLBACK", "FallbackBedrockRole")
    monkeypatch.setattr(config, "BEDROCK_ACCT_PRIMARY", "111111111111")
    monkeypatch.setattr(config, "BEDROCK_ACCT_FALLBACK", "222222222222")
    monkeypatch.setattr(config, "EMBED_DIM", 3)
    gw._cache.clear()


# --- Stub building blocks ---------------------------------------------------

class StubSts:
    def __init__(self, arns_seen: list):
        self._arns = arns_seen

    def assume_role(self, *, RoleArn, RoleSessionName, ExternalId=None):
        self._arns.append(RoleArn)
        acct = RoleArn.split(":")[4]
        return {"Credentials": {"AccessKeyId": f"AKIA-{acct}", "SecretAccessKey": "s",
                                "SessionToken": "t"}}


class _Body:
    def __init__(self, payload: dict):
        self._raw = json.dumps(payload).encode()

    def read(self):
        return self._raw


class StubBedrock:
    def __init__(self, *, fail: bool = False, response: dict | None = None,
                 headers: dict | None = None):
        self.fail = fail
        self.response = response or {}
        self.headers = headers
        self.calls: list = []

    def invoke_model(self, *, modelId, body, **kwargs):
        self.calls.append(("invoke_model", modelId, json.loads(body)))
        if self.fail:
            raise RuntimeError("simulated failure")
        out = {"body": _Body(self.response)}
        if self.headers is not None:
            out["ResponseMetadata"] = {"HTTPHeaders": self.headers}
        return out

    def converse(self, **kwargs):
        self.calls.append(("converse", kwargs["modelId"], kwargs))
        if self.fail:
            raise RuntimeError("simulated failure")
        return self.response


def make_factory(by_account: dict, arns: list):
    """bedrock-runtime clients per account: 'acc3'/'acc1' via assumed creds, 'acc2' direct."""
    ids = {"111111111111": "acc3", "222222222222": "acc1"}

    def factory(service, *, creds=None, region=None):
        if service == "sts":
            return StubSts(arns)
        if service == "bedrock-runtime":
            if creds is None:
                return by_account["acc2"]
            return by_account[ids[creds.access_key_id.split("-")[1]]]
        raise AssertionError(f"unexpected service {service}")

    return factory


def _claude_reply(text="Day 1: Hello.", tin=100, tout=20):
    return {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn",
            "usage": {"input_tokens": tin, "output_tokens": tout}}


SONNET = gw.Model("sonnet", "anthropic", "anthropic_native",
                  {"acc3": "global.sonnet", "acc1": "global.sonnet"}, 3.0, 15.0)
ROUTE = gw.Route("tp_compose", "writer", (SONNET,), "acc3")


# --- generate_text ------------------------------------------------------------

def test_generate_text_on_primary_prices_from_catalog():
    acc3 = StubBedrock(response=_claude_reply(tin=1000, tout=200))
    arns: list = []
    text, call = gw.generate_text(ROUTE, "sys", "user", 1200,
                                  client_factory=make_factory({"acc3": acc3}, arns))
    assert text == "Day 1: Hello."
    assert (call.stage, call.model, call.account, call.provider) == \
        ("tp_compose", "sonnet", "acc3", "bedrock-satellite")
    assert (call.tokens_in, call.tokens_out, call.stop_reason) == (1000, 200, "end_turn")
    assert call.cost_usd == pytest.approx(0.006)  # 1000 x $3/M + 200 x $15/M
    assert call.fallback_used is False
    _, model_id, body = acc3.calls[0]
    assert model_id == "global.sonnet"
    assert body["system"] == "sys" and body["max_tokens"] == 1200
    assert body["messages"] == [{"role": "user", "content": "user"}]


def test_generate_text_fails_over_acc3_to_acc1_with_per_account_roles():
    """Regression: acc3 and acc1 have DIFFERENT satellite role names."""
    arns: list = []
    factory = make_factory({"acc3": StubBedrock(fail=True),
                            "acc1": StubBedrock(response=_claude_reply())}, arns)
    _, call = gw.generate_text(ROUTE, "sys", "user", 100, client_factory=factory)
    assert call.account == "acc1" and call.fallback_used is False  # same model, other account
    assert arns == ["arn:aws:iam::111111111111:role/PrimaryBedrockRole",
                    "arn:aws:iam::222222222222:role/FallbackBedrockRole"]


def test_generate_text_next_model_in_route_marks_fallback_used():
    converse_model = gw.Model("sonnet-5", "anthropic", "converse", {"acc3": "global.sonnet-5"},
                              2.0, 10.0)
    route = gw.Route("tp_compose", "writer", (converse_model, SONNET), "acc3")
    acc3 = StubBedrock(fail=True)
    acc1 = StubBedrock(response=_claude_reply())
    _, call = gw.generate_text(route, "s", "u", 50,
                               client_factory=make_factory({"acc3": acc3, "acc1": acc1}, []))
    assert call.model == "sonnet" and call.fallback_used is True
    assert acc3.calls[0][0] == "converse"


def test_generate_text_converse_shape_and_usage():
    model = gw.Model("sonnet-5", "anthropic", "converse", {"acc3": "global.sonnet-5"}, 2.0, 10.0)
    acc3 = StubBedrock(response={"output": {"message": {"content": [{"text": "hi"}]}},
                                 "usage": {"inputTokens": 10, "outputTokens": 5},
                                 "stopReason": "end_turn"})
    text, call = gw.generate_text(gw.Route("tp_compose", "writer", (model,), "acc3"), "s", "u", 50,
                                  client_factory=make_factory({"acc3": acc3}, []))
    assert text == "hi" and (call.tokens_in, call.tokens_out) == (10, 5)
    kwargs = acc3.calls[0][2]
    assert kwargs["system"] == [{"text": "s"}]
    assert kwargs["inferenceConfig"] == {"maxTokens": 50}


def test_generate_text_raises_when_everything_fails():
    factory = make_factory({"acc3": StubBedrock(fail=True), "acc1": StubBedrock(fail=True)}, [])
    with pytest.raises(bs.BedrockError, match="All models failed"):
        gw.generate_text(ROUTE, "s", "u", 50, client_factory=factory)


def test_missing_role_raises(monkeypatch):
    monkeypatch.setattr(config, "BEDROCK_ROLE_NAME_PRIMARY", "")
    monkeypatch.setattr(config, "BEDROCK_ROLE_NAME_FALLBACK", "")
    factory = make_factory({"acc3": StubBedrock(response=_claude_reply())}, [])
    with pytest.raises(bs.BedrockError):
        gw.generate_text(ROUTE, "s", "u", 50, client_factory=factory)


# --- embed_texts ----------------------------------------------------------------

def test_embed_is_direct_on_acc2_and_reads_token_header():
    acc2 = StubBedrock(response={"embeddings": {"float": [[0.1, 0.2, 0.3]]}},
                       headers={"x-amzn-bedrock-input-token-count": "1000"})
    arns: list = []
    vectors, call = gw.embed_texts(gw.builtin_route("tp_search_embed"), ["beach town"],
                                   client_factory=make_factory({"acc2": acc2}, arns))
    assert vectors == [[0.1, 0.2, 0.3]]
    assert arns == []  # no assume-role for embeddings
    assert (call.account, call.provider, call.tokens_in) == ("acc2", "bedrock-native", 1000)
    assert call.cost_usd == pytest.approx(0.00012)
    assert call.extra == {"tokens_estimated": False}
    body = acc2.calls[0][2]
    assert body == {"texts": ["beach town"], "input_type": "search_document",
                    "embedding_types": ["float"], "output_dimension": 3}


def test_embed_wrong_dimension_is_a_failure():
    acc2 = StubBedrock(response={"embeddings": {"float": [[0.1, 0.2]]}})
    with pytest.raises(bs.BedrockError):
        gw.embed_texts(gw.builtin_route("tp_search_embed"), ["x"],
                       client_factory=make_factory({"acc2": acc2}, []))


# --- load_route / record ----------------------------------------------------------

class FakeDb:
    def __init__(self, role_rows=None, catalog_rows=None, fail=False, fail_execute=False):
        self.role_rows = role_rows or []
        self.catalog_rows = catalog_rows or []
        self.fail = fail
        self.fail_execute = fail_execute
        self.fetches = 0
        self.executed: list = []

    async def fetch(self, sql, *args):
        self.fetches += 1
        if self.fail:
            raise RuntimeError("permission denied for table llm_role_config")
        return self.role_rows if "llm_role_config" in sql else self.catalog_rows

    async def execute(self, sql, *args):
        if self.fail_execute:
            raise RuntimeError("permission denied for table llm_call_log")
        self.executed.append((sql, args))


async def test_load_route_reads_config_and_catalog_and_caches():
    db = FakeDb(
        role_rows=[{"role": "writer", "model_id": "sonnet-5", "fallback_model_ids": ["sonnet"],
                    "account_route": "acc3"}],
        catalog_rows=[
            {"model_key": "sonnet", "vendor": "anthropic", "api_style": "anthropic_native",
             "bedrock_profile_ids": json.dumps({"acc3": "g.s"}), "price_in_per_mtok": 3,
             "price_out_per_mtok": 15, "price_cache_read_per_mtok": None,
             "price_cache_write_per_mtok": None, "enabled": True},
            {"model_key": "sonnet-5", "vendor": "anthropic", "api_style": "converse",
             "bedrock_profile_ids": {"acc3": "g.s5"}, "price_in_per_mtok": 2,
             "price_out_per_mtok": 10, "price_cache_read_per_mtok": 0.2,
             "price_cache_write_per_mtok": 2.5, "enabled": True},
        ])
    route = await gw.load_route(db, "tp_compose")
    assert [m.key for m in route.models] == ["sonnet-5", "sonnet"]  # route order, not row order
    assert route.models[1].profiles == {"acc3": "g.s"}
    await gw.load_route(db, "tp_compose")
    assert db.fetches == 2  # second call served from the cache


async def test_load_route_falls_back_to_builtin_when_db_is_unreadable():
    route = await gw.load_route(FakeDb(fail=True), "tp_search_embed")
    assert route == gw.BUILTIN_ROUTES["tp_search_embed"]


async def test_record_writes_one_row_tagged_with_the_app():
    db = FakeDb()
    call = gw.Call("tp_compose", "writer", "sonnet", "acc3", 10, 5, 0.0001, False, "end_turn")
    await gw.record(db, call, {"mode": "compose"})
    sql, args = db.executed[0]
    assert "shared.llm_call_log" in sql
    assert args[1:4] == ("tp_compose", "writer", "sonnet")
    assert json.loads(args[7]) == {"app": "tripplanner", "mode": "compose"}
    assert args[-1] == "bedrock-satellite"


async def test_record_never_raises():
    call = gw.Call("tp_compose", "writer", "sonnet", "acc3", 1, 1, 0.0, False)
    await gw.record(FakeDb(fail_execute=True), call, {})
