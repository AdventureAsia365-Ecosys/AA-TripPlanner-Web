"""AA-862: Cohere embed fall-through acc2 -> acc3 (-> acc1) and the cached satellite embed client.
Fully stubbed: no live AWS, no DB, boto3 never called."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from backend import config
from backend.shared import bedrock_satellite as bs
from backend.shared import llm_gateway as gw

THROTTLE = ("An error occurred (ThrottlingException) when calling the InvokeModel operation: "
            "Too many requests, please wait before trying again.")
EXPIRED = ("An error occurred (ExpiredTokenException) when calling the InvokeModel operation: "
           "The security token included in the request is expired")

ACC2 = gw.Model("cohere-embed-v4", "cohere", "embed", {"acc2": "global.cohere.embed-v4:0"},
                0.12, 0.0)
ACC3 = gw.Model("cohere-embed-v4-acc3", "cohere", "embed", {"acc3": "global.cohere.embed-v4:0"},
                0.12, 0.0)
ACC1_OFF = gw.Model("cohere-embed-v4-acc1", "cohere", "embed", {"acc1": "global.cohere.embed-v4:0"},
                    0.12, 0.0, enabled=False)
ROUTE = gw.Route("tp_search_embed", "embed", (ACC2, ACC3))


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    monkeypatch.setattr(config, "BEDROCK_ROLE_NAME", "")
    monkeypatch.setattr(config, "BEDROCK_ROLE_NAME_PRIMARY", "PrimaryBedrockRole")
    monkeypatch.setattr(config, "BEDROCK_ROLE_NAME_FALLBACK", "FallbackBedrockRole")
    monkeypatch.setattr(config, "BEDROCK_ACCT_PRIMARY", "111111111111")
    monkeypatch.setattr(config, "BEDROCK_ACCT_FALLBACK", "222222222222")
    monkeypatch.setattr(config, "BEDROCK_EXTERNAL_ID_PRIMARY", "ext-acc3")
    monkeypatch.setattr(config, "EMBED_DIM", 3)
    monkeypatch.setattr(gw, "_acc2_embed", None)
    gw._sat_embed.clear()
    yield
    gw._sat_embed.clear()


class _Body:
    def __init__(self, payload: dict):
        self._raw = json.dumps(payload).encode()

    def read(self):
        return self._raw


class StubEmbed:
    """bedrock-runtime stub: raises the queued errors first, then answers one vector per text."""

    def __init__(self, *errors: str):
        self.errors = list(errors)
        self.calls: list = []

    def invoke_model(self, *, modelId, body, **kwargs):
        texts = json.loads(body)["texts"]
        self.calls.append(modelId)
        if self.errors:
            raise RuntimeError(self.errors.pop(0))
        return {"body": _Body({"embeddings": {"float": [[0.1, 0.2, 0.3] for _ in texts]}}),
                "ResponseMetadata": {"HTTPHeaders": {"x-amzn-bedrock-input-token-count": "10000"}}}


# --- route walk with an injected factory (the per-call path) ------------------------------------

class StubSts:
    def __init__(self, seen: list):
        self.seen = seen

    def assume_role(self, **kwargs):
        self.seen.append(kwargs)
        acct = kwargs["RoleArn"].split(":")[4]
        return {"Credentials": {"AccessKeyId": f"AKIA-{acct}", "SecretAccessKey": "s",
                                "SessionToken": "t"}}


def make_factory(acc2, acc3, acc1=None, seen=None):
    by_id = {"111111111111": acc3, "222222222222": acc1}

    def factory(service, *, creds=None, region=None):
        if service == "sts":
            return StubSts(seen if seen is not None else [])
        if creds is None:
            return acc2
        return by_id[creds.access_key_id.split("-")[1]]

    return factory


def test_acc2_throttle_falls_through_to_the_acc3_row():
    acc2, acc3, seen, failures = StubEmbed(THROTTLE), StubEmbed(), [], []
    vectors, call = gw.embed_texts(ROUTE, ["beach town"], input_type="search_query",
                                   client_factory=make_factory(acc2, acc3, seen=seen),
                                   failures=failures)
    assert vectors == [[0.1, 0.2, 0.3]]
    assert (call.model, call.account, call.provider) == ("cohere-embed-v4-acc3", "acc3",
                                                         "bedrock-satellite")
    assert call.fallback_used is True
    assert call.tokens_in == 10000 and call.cost_usd == pytest.approx(0.0012)
    assert acc3.calls == ["global.cohere.embed-v4:0"]
    assert seen[0]["RoleArn"] == "arn:aws:iam::111111111111:role/PrimaryBedrockRole"
    assert seen[0]["ExternalId"] == "ext-acc3"
    assert [(f.model, f.account, f.failed) for f in failures] == [("cohere-embed-v4", "acc2", True)]
    assert gw.is_throttle(failures[0].extra["error"])


def test_acc2_success_never_touches_the_satellite():
    acc2, acc3, seen = StubEmbed(), StubEmbed(), []
    _, call = gw.embed_texts(ROUTE, ["x"], client_factory=make_factory(acc2, acc3, seen=seen))
    assert (call.account, call.fallback_used) == ("acc2", False)
    assert seen == [] and acc3.calls == []


def test_disabled_acc1_row_is_skipped_and_the_chain_error_still_reads_as_throttle():
    route = gw.Route("tp_search_embed", "embed", (ACC2, ACC3, ACC1_OFF))
    acc1 = StubEmbed()
    failures: list = []
    with pytest.raises(bs.BedrockError) as e:
        gw.embed_texts(route, ["x"], client_factory=make_factory(
            StubEmbed(THROTTLE), StubEmbed(THROTTLE), acc1), failures=failures)
    assert acc1.calls == []
    assert "cohere-embed-v4-acc1: skipped" in str(e.value)
    assert [f.account for f in failures] == ["acc2", "acc3"]
    # search.py keys its keyword cooldown on this: only when every account failed with a throttle
    assert gw.is_throttle(str(e.value))


# --- the cached no-retry clients (default factory, production path) ----------------------------

@pytest.fixture
def prod(monkeypatch):
    """Default-factory path with boto3 replaced: counts AssumeRole calls, hands out stub clients."""
    state = {"assumes": [], "acc2": StubEmbed(), "sat": {}, "now": 1_000_000.0,
             "expiration": "default"}

    def assume(account_id, role_name, external_id, client_factory):
        assert client_factory is bs.default_client_factory
        state["assumes"].append((account_id, role_name, external_id))
        exp = state["expiration"]
        if exp == "default":
            exp = datetime.fromtimestamp(state["now"] + 3600, tz=timezone.utc)
        return bs.Credentials(f"AKIA-{account_id}-{len(state['assumes'])}", "s", "t", exp)

    def new_client(creds=None):
        if creds is None:
            return state["acc2"]
        acct = creds.access_key_id.split("-")[1]
        return state["sat"].setdefault(acct, StubEmbed())

    monkeypatch.setattr(gw.bedrock_satellite, "assume_credentials", assume)
    monkeypatch.setattr(gw, "_new_embed_client", new_client)
    monkeypatch.setattr(gw, "_wall_clock", lambda: state["now"])
    return state


def test_one_assume_role_serves_many_satellite_embeds(prod):
    prod["acc2"] = StubEmbed(THROTTLE, THROTTLE)
    for _ in range(2):
        _, call = gw.embed_texts(ROUTE, ["x"])
        assert call.account == "acc3"
    assert prod["assumes"] == [("111111111111", "PrimaryBedrockRole", "ext-acc3")]
    assert len(prod["sat"]["111111111111"].calls) == 2
    assert len(prod["acc2"].calls) == 2  # acc2 is still tried first every time


def test_satellite_client_is_rebuilt_five_minutes_before_the_creds_expire(prod):
    prod["acc2"] = StubEmbed(THROTTLE, THROTTLE, THROTTLE)
    gw.embed_texts(ROUTE, ["x"])
    prod["now"] += 3600 - 300 - 1
    gw.embed_texts(ROUTE, ["x"])
    assert len(prod["assumes"]) == 1
    prod["now"] += 1
    gw.embed_texts(ROUTE, ["x"])
    assert len(prod["assumes"]) == 2


def test_missing_expiration_assumes_the_shortest_session(prod):
    prod["expiration"] = None
    prod["acc2"] = StubEmbed(THROTTLE, THROTTLE)
    gw.embed_texts(ROUTE, ["x"])
    prod["now"] += gw.SAT_CREDS_ASSUMED_LIFETIME_SECONDS - gw.SAT_CREDS_REFRESH_MARGIN_SECONDS
    gw.embed_texts(ROUTE, ["x"])
    assert len(prod["assumes"]) == 2


def test_expired_token_drops_the_cached_client_and_the_next_call_assumes_again(prod):
    prod["acc2"] = StubEmbed(THROTTLE, THROTTLE)
    prod["sat"]["111111111111"] = StubEmbed(EXPIRED)
    with pytest.raises(bs.BedrockError):
        gw.embed_texts(ROUTE, ["x"])
    assert "acc3" not in gw._sat_embed
    _, call = gw.embed_texts(ROUTE, ["x"])
    assert call.account == "acc3"
    assert len(prod["assumes"]) == 2


def test_a_satellite_throttle_keeps_the_cached_client(prod):
    prod["acc2"] = StubEmbed(THROTTLE, THROTTLE)
    prod["sat"]["111111111111"] = StubEmbed(THROTTLE)
    with pytest.raises(bs.BedrockError):
        gw.embed_texts(ROUTE, ["x"])
    assert "acc3" in gw._sat_embed
    gw.embed_texts(ROUTE, ["x"])
    assert len(prod["assumes"]) == 1


def test_acc2_client_is_built_once_per_container(prod, monkeypatch):
    built = []

    def new_client(creds=None):
        built.append(creds)
        return prod["acc2"]

    monkeypatch.setattr(gw, "_new_embed_client", new_client)
    gw.embed_texts(ROUTE, ["x"])
    gw.embed_texts(ROUTE, ["y"])
    assert built == [None] and prod["assumes"] == []


def test_real_embed_clients_make_exactly_one_attempt():
    """botocore reads the legacy `max_attempts` as retries; `total_max_attempts=1` is one attempt.
    Builds real clients (no request is sent)."""
    pytest.importorskip("boto3")
    sat = gw._new_embed_client(bs.Credentials("AKIAFAKE", "s", "t"))
    acc2 = gw._new_embed_client()
    for client in (sat, acc2):
        assert client.meta.config.retries["total_max_attempts"] == 1
        assert client.meta.region_name == config.BEDROCK_REGION
    assert sat._request_signer._credentials.access_key == "AKIAFAKE"


# --- bedrock_satellite --------------------------------------------------------------------------

def test_credentials_keep_the_sts_expiration_and_the_old_name_still_works():
    exp = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
    seen: list = []

    class Sts:
        def assume_role(self, **kwargs):
            seen.append(kwargs)
            return {"Credentials": {"AccessKeyId": "a", "SecretAccessKey": "s",
                                    "SessionToken": "t", "Expiration": exp}}

    creds = bs.assume_credentials("111111111111", "R", "ext", lambda service: Sts())
    assert creds.expiration == exp
    assert bs._assume is bs.assume_credentials
    assert bs.Credentials.from_sts_response(
        {"Credentials": {"AccessKeyId": "a", "SecretAccessKey": "s", "SessionToken": "t"}}
    ).expiration is None
