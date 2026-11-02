"""AA-642 S263 E1: a reply cut at its token limit is a failed (billed) attempt, never returned, and every
generate_text request has a route deadline. Fully stubbed: no AWS, no DB, invented model keys and text."""
from __future__ import annotations

import json
import sys
import types

import pytest

from backend import config
from backend.aa_llm import ledger
from backend.shared import bedrock_satellite as bs
from backend.shared import llm_gateway as gw
from backend.tests.test_llm_gateway import FakeDb, StubBedrock, _row, make_factory


@pytest.fixture(autouse=True)
def _gateway_state(monkeypatch):
    monkeypatch.setattr(config, "BEDROCK_ROLE_NAME", "")
    monkeypatch.setattr(config, "BEDROCK_ROLE_NAME_PRIMARY", "PrimaryBedrockRole")
    monkeypatch.setattr(config, "BEDROCK_ACCT_PRIMARY", "111111111111")
    monkeypatch.setattr(config, "LLM_ROUTE_DEADLINE_DEFAULT_S", 20.0)
    monkeypatch.setattr(config, "LLM_ROUTE_DEADLINE_MAX_S", 25.0)
    monkeypatch.setattr(config, "LLM_ROUTE_DEADLINE_MIN_ATTEMPT_S", 1.0)
    monkeypatch.setattr(gw, "_spend_unreadable_until", 0.0)
    gw._cache.clear()
    gw._view_missing_until.clear()


WRITER_A = gw.Model("zz-writer-a", "anthropic", "anthropic_native", {"acc3": "zz.writer-a"}, 3.0, 15.0)
WRITER_B = gw.Model("zz-writer-b", "anthropic", "converse", {"acc3": "zz.writer-b"}, 2.0, 10.0)
ROUTE = gw.Route("tp_compose", "writer", (WRITER_A, WRITER_B), "acc3")


def _native(text="Day 1: A quiet morning in Zedport.", stop="end_turn", tin=1000, tout=200):
    return {"content": [{"type": "text", "text": text}], "stop_reason": stop,
            "usage": {"input_tokens": tin, "output_tokens": tout}}


def _converse(text="Day 1: Lanterns over Qorvale.", stop="end_turn", tin=100, tout=20):
    return {"output": {"message": {"content": [{"text": text}]}}, "stopReason": stop,
            "usage": {"inputTokens": tin, "outputTokens": tout}}


class TwoStyleStub(StubBedrock):
    """invoke_model answers `native`, converse answers `conv` (one acc3 client for both models)."""

    def __init__(self, native: dict, conv: dict):
        super().__init__(response=native)
        self.conv = conv

    def converse(self, **kwargs):
        self.calls.append(("converse", kwargs["modelId"], kwargs))
        return self.conv


# --- truncation ---------------------------------------------------------------------------------------------------

def test_a_truncated_reply_fails_over_and_is_never_returned():
    stub = TwoStyleStub(_native(text="Day 1: Half a sent", stop="max_tokens"), _converse())
    failures: list = []
    text, call = gw.generate_text(ROUTE, "sys", "user", 50, client_factory=make_factory({"acc3": stub}, []),
                                  failures=failures)
    assert text == "Day 1: Lanterns over Qorvale."
    assert (call.model, call.fallback_used, call.failed) == ("zz-writer-b", True, False)
    (cut,) = failures
    assert (cut.model, cut.failed, cut.status, cut.error_class) == ("zz-writer-a", True, "truncated", "truncated")
    assert (cut.tokens_in, cut.tokens_out, cut.stop_reason) == (1000, 200, "max_tokens")
    assert cut.cost_usd == pytest.approx(0.006)                     # billed: 1000 x $3/M + 200 x $15/M
    assert (cut.attempt_no, call.attempt_no) == (1, 2)
    assert "token limit" in cut.extra["error"]


def test_every_reply_truncated_raises_instead_of_returning_text():
    stub = TwoStyleStub(_native(stop="max_tokens"), _converse(stop="max_tokens"))
    failures: list = []
    with pytest.raises(bs.BedrockError, match="All models failed.*token limit"):
        gw.generate_text(ROUTE, "sys", "user", 50, client_factory=make_factory({"acc3": stub}, []),
                         failures=failures)
    assert [(f.model, f.status) for f in failures] == [("zz-writer-a", "truncated"), ("zz-writer-b", "truncated")]


@pytest.mark.parametrize("stop", sorted(ledger.TRUNCATED_STOP_REASONS))
def test_every_core_truncated_stop_reason_counts(stop):
    route = gw.Route("tp_compose", "writer", (WRITER_A,), "acc3")
    with pytest.raises(bs.BedrockError):
        gw.generate_text(route, "s", "u", 50,
                         client_factory=make_factory({"acc3": StubBedrock(response=_native(stop=stop))}, []))


def test_a_complete_reply_is_unchanged():
    route = gw.Route("tp_compose", "writer", (WRITER_A,), "acc3")
    failures: list = []
    text, call = gw.generate_text(route, "s", "u", 50, failures=failures,
                                  client_factory=make_factory({"acc3": StubBedrock(response=_native())}, []))
    assert text.startswith("Day 1:") and call.stop_reason == "end_turn" and failures == []


async def test_a_truncated_attempt_is_ledgered_as_billed_and_counts_spend():
    stub = TwoStyleStub(_native(stop="max_tokens"), _converse())
    failures: list = []
    gw.generate_text(ROUTE, "s", "u", 50, client_factory=make_factory({"acc3": stub}, []), failures=failures)
    db = FakeDb()
    await gw.record(db, failures[0], {"mode": "compose"})
    row = _row(db.executed[0][1])
    assert (row["status"], row["error_class"], row["stop_reason"]) == ("truncated", "truncated", "max_tokens")
    assert (row["tokens_in"], row["tokens_out"]) == (1000, 200)
    assert row["cost_usd"] == pytest.approx(0.006) and row["pricing_mode"] == "catalog"
    assert json.loads(row["quality_signal"])["failed"] is True
    spend_sql, spend_args = db.executed[1]
    assert "tripplanner.llm_spend_daily" in spend_sql and spend_args[1] == pytest.approx(0.006)


# --- route deadline -----------------------------------------------------------------------------------------------

def test_route_deadline_default_route_value_and_cap():
    assert gw.route_deadline_s(ROUTE) == 20.0
    assert gw.route_deadline_s(gw.Route("tp_compose", "writer", (WRITER_A,), "acc3", None, 8)) == 8.0
    assert gw.route_deadline_s(gw.Route("tp_compose", "writer", (WRITER_A,), "acc3", None, 600)) == 25.0


@pytest.mark.parametrize("raw, expected", [(12, 12), (None, None), (0, None), (3601, None), (True, None),
                                           ("12", None)])
def test_deadline_value_keeps_only_what_the_check_allows(raw, expected):
    assert gw._deadline_value(raw) == expected


class SlowStub(StubBedrock):
    """Each call moves the fake clock on by `seconds` (a slow provider)."""

    def __init__(self, clock: list, seconds: float, **kw):
        super().__init__(**kw)
        self.clock, self.seconds = clock, seconds

    def invoke_model(self, **kwargs):
        self.clock[0] += self.seconds
        return super().invoke_model(**kwargs)

    def converse(self, **kwargs):
        self.clock[0] += self.seconds
        return super().converse(**kwargs)


def test_a_slow_attempt_stops_the_route_with_a_deadline_row(monkeypatch, capsys):
    clock = [1000.0]
    monkeypatch.setattr(gw.time, "monotonic", lambda: clock[0])
    slow = SlowStub(clock, 19.5, fail=True)                         # 19.5 s, then an error: 0.5 s left of 20
    failures: list = []
    with pytest.raises(gw.RouteDeadlineExceeded) as e:
        gw.generate_text(ROUTE, "s", "u", 50, client_factory=make_factory({"acc3": slow}, []), failures=failures)
    assert e.value.reason == "route_deadline" and isinstance(e.value, bs.BedrockError)
    assert "tried: zz-writer-a@acc3" in str(e.value)
    assert [c[0] for c in slow.calls] == ["invoke_model"]           # the second model was never called
    first, stopped = failures
    assert (first.status, first.error_class) == ("error", "other")
    assert (stopped.model, stopped.status, stopped.error_class) == ("zz-writer-b", "error", "deadline")
    assert (stopped.cost_usd, stopped.tokens_in, stopped.latency_ms, stopped.attempt_no) == (0.0, 0, 0, 2)
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["evt"] == "llm_route_deadline_passed" and line["stage"] == "tp_compose"


async def test_the_deadline_row_is_a_zero_cost_error_on_the_ledger():
    stopped = gw._deadline_passed(ROUTE, WRITER_B, "acc3", 1, 20.0, attempt_no=2)
    db = FakeDb()
    await gw.record(db, stopped, {"mode": "compose"})
    row = _row(db.executed[0][1])
    assert (row["status"], row["error_class"], row["cost_usd"], row["pricing_mode"]) == ("error", "deadline", 0.0,
                                                                                           None)


def test_each_attempt_gets_the_time_left_as_its_timeout(monkeypatch):
    clock = [50.0]
    monkeypatch.setattr(gw.time, "monotonic", lambda: clock[0])
    seen: list = []

    def fake_text_call(model, account, system, user, max_tokens, client_factory, *, timeout_s=None):
        seen.append(timeout_s)
        clock[0] += 6.0
        raise bs.BedrockError("simulated outage")

    monkeypatch.setattr(gw, "_text_call", fake_text_call)
    with pytest.raises(bs.BedrockError, match="All models failed"):
        gw.generate_text(ROUTE, "s", "u", 50)
    assert seen == [20.0, 14.0]


def test_the_production_client_is_bounded_by_the_time_left(monkeypatch):
    seen: dict = {}

    class FakeConfig:
        def __init__(self, **kw):
            seen["config"] = kw

    fake_boto3 = types.SimpleNamespace(client=lambda service, **kw: seen.setdefault("client", (service, kw)))
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    monkeypatch.setitem(sys.modules, "botocore.config", types.SimpleNamespace(Config=FakeConfig))
    gw._runtime(gw._legacy_deployment(connection_key="acc2", wire_id="w"), bs.default_client_factory, 7.5)
    assert seen["client"][0] == "bedrock-runtime"
    assert seen["config"] == {"connect_timeout": 5.0, "read_timeout": 7.5,
                              "retries": {"total_max_attempts": 1, "mode": "standard"}}


def test_without_a_timeout_the_production_client_keeps_the_boto_defaults(monkeypatch):
    seen: dict = {}
    fake_boto3 = types.SimpleNamespace(client=lambda service, **kw: seen.setdefault("client", (service, kw)))
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    bs.default_client_factory("sts")
    assert "config" not in seen["client"][1]


def test_the_satellite_sts_call_is_bounded_too(monkeypatch):
    """acc3: the AssumeRole client and the bedrock-runtime client both get the time left."""
    built: list = []

    def fake_default(service, *, creds=None, region=None, timeout_s=None):
        built.append((service, timeout_s))
        if service == "sts":
            return types.SimpleNamespace(assume_role=lambda **kw: {"Credentials": {
                "AccessKeyId": "a", "SecretAccessKey": "s", "SessionToken": "t"}})
        return object()

    monkeypatch.setattr(bs, "default_client_factory", fake_default)
    gw._runtime(gw._legacy_deployment(connection_key="acc3", wire_id="w"), fake_default, 4.0)
    assert built == [("sts", 4.0), ("bedrock-runtime", 4.0)]


# --- route_deadline_s from the config read ------------------------------------------------------------------------

async def test_load_route_reads_route_deadline_from_the_tables():
    db = FakeDb(
        role_rows=[{"role": "writer", "model_id": "zz-writer-a", "fallback_model_ids": [], "account_route": "acc3",
                    "route_deadline_s": 9}],
        catalog_rows=[{"model_key": "zz-writer-a", "vendor": "anthropic", "api_style": "anthropic_native",
                       "bedrock_profile_ids": {"acc3": "zz.writer-a"}, "price_in_per_mtok": 3,
                       "price_out_per_mtok": 15, "price_cache_read_per_mtok": None,
                       "price_cache_write_per_mtok": None, "enabled": True}])
    route = await gw.load_route(db, "tp_compose")
    assert route.deadline_s == 9 and gw.route_deadline_s(route) == 9.0
    assert any("route_deadline_s" in q for q in db.sql if "llm_role_config" in q)


async def test_load_route_from_the_view_without_the_column_uses_the_default():
    from backend.tests.test_llm_gateway import _view_row

    route = await gw.load_route(FakeDb(view_rows=[_view_row(0, "zz-writer-a")]), "tp_compose")
    assert route.deadline_s is None and gw.route_deadline_s(route) == 20.0


async def test_load_route_from_a_view_row_that_carries_the_column():
    from backend.tests.test_llm_gateway import _view_row

    row = {**_view_row(0, "zz-writer-a"), "route_deadline_s": 12}
    route = await gw.load_route(FakeDb(view_rows=[row]), "tp_compose")
    assert route.deadline_s == 12
