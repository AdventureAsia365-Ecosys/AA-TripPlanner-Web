"""Unit tests for the Model Gateway client (llm_gateway) and the Bedrock satellite transport —
fully stubbed, no live AWS, no DB."""
from __future__ import annotations

import json

import pytest

from backend import config
from backend.aa_llm import contract, ledger
from backend.shared import bedrock_satellite as bs
from backend.shared import llm_gateway as gw


@pytest.fixture(autouse=True)
def _role_configured(monkeypatch):
    # acc3 is the only satellite account (AA-998); its role name is per-account config.
    monkeypatch.setattr(config, "BEDROCK_ROLE_NAME", "")
    monkeypatch.setattr(config, "BEDROCK_ROLE_NAME_PRIMARY", "PrimaryBedrockRole")
    monkeypatch.setattr(config, "BEDROCK_ACCT_PRIMARY", "111111111111")
    monkeypatch.setattr(config, "EMBED_DIM", 3)
    gw._cache.clear()
    gw._view_missing_until.clear()


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
                 headers: dict | None = None, fail_converse: bool = False):
        self.fail = fail
        self.fail_converse = fail_converse
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
        if self.fail or self.fail_converse:
            raise RuntimeError("simulated failure")
        return self.response


def make_factory(by_account: dict, arns: list):
    """bedrock-runtime clients per account: 'acc3' via assumed creds, 'acc2' direct. A stub for
    the retired 'acc1' (id 222222222222) may be passed to prove it is never reached."""
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


SONNET = gw.Model("sonnet", "anthropic", "anthropic_native", {"acc3": "global.sonnet"}, 3.0, 15.0)
ROUTE = gw.Route("tp_compose", "writer", (SONNET,), "acc3")
# A catalog row as it stood before CIS migration 295 stripped the acc1 profiles (AA-998).
SONNET_STALE = gw.Model("sonnet", "anthropic", "anthropic_native",
                        {"acc3": "global.sonnet", "acc1": "global.sonnet"}, 3.0, 15.0)
CONVERSE = gw.Model("sonnet-5", "anthropic", "converse", {"acc3": "global.sonnet-5"}, 2.0, 10.0)


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


def test_acc3_failure_never_fails_over_to_a_stale_acc1_profile():
    """AA-998 guard: a catalog row that still names acc1 is called on acc3 only; when acc3 fails the
    call fails (no cross-account fallback), and no role in acc1 is ever assumed."""
    arns: list = []
    acc1 = StubBedrock(response=_claude_reply())
    factory = make_factory({"acc3": StubBedrock(fail=True), "acc1": acc1}, arns)
    route = gw.Route("tp_compose", "writer", (SONNET_STALE,), "acc3")
    failures: list = []
    with pytest.raises(bs.BedrockError, match="All models failed"):
        gw.generate_text(route, "sys", "user", 100, client_factory=factory, failures=failures)
    assert acc1.calls == []
    assert arns == ["arn:aws:iam::111111111111:role/PrimaryBedrockRole"]
    assert [f.account for f in failures] == ["acc3"]


def test_account_route_acc1_from_the_db_is_ignored():
    """AA-998 guard: a stage left on account_route='acc1' still runs on acc3."""
    acc1 = StubBedrock(response=_claude_reply())
    acc3 = StubBedrock(response=_claude_reply())
    route = gw.Route("tp_compose", "writer", (SONNET_STALE,), "acc1")
    _, call = gw.generate_text(route, "s", "u", 50,
                               client_factory=make_factory({"acc3": acc3, "acc1": acc1}, []))
    assert call.account == "acc3" and acc1.calls == []
    assert gw._accounts(SONNET_STALE, "acc1") == ["acc3"]


def test_a_model_with_only_an_acc1_profile_is_reported_not_called():
    acc1_only = gw.Model("old", "anthropic", "anthropic_native", {"acc1": "global.old"}, 3.0, 15.0)
    acc1 = StubBedrock(response=_claude_reply())
    with pytest.raises(bs.BedrockError, match="old: no profile on a supported account"):
        gw.generate_text(gw.Route("tp_compose", "writer", (acc1_only,), "acc3"), "s", "u", 50,
                         client_factory=make_factory({"acc1": acc1}, []))
    assert acc1.calls == []


def test_acc1_is_not_a_known_satellite_and_has_no_config():
    with pytest.raises(bs.BedrockError, match="Unknown account 'acc1'"):
        gw._satellite_target("acc1")
    assert gw.SUPPORTED_ACCOUNTS == ("acc3", "acc2")
    for name in ("BEDROCK_ACCT_FALLBACK", "BEDROCK_ROLE_NAME_FALLBACK",
                 "BEDROCK_EXTERNAL_ID_FALLBACK"):
        assert not hasattr(config, name), name
    assert all("acc1" not in m.profiles for r in gw.BUILTIN_ROUTES.values() for m in r.models)


def test_no_backend_module_names_the_retired_acc1_account():
    """AA-998 guard: acc1 left the ecosystem; no runtime code may route to it again (an "acc1" /
    'acc1' string literal, or a read of the removed fallback env)."""
    import pathlib
    import re
    pattern = re.compile(r"""["']acc1["']|_FALLBACK["']""")
    root = pathlib.Path(__file__).resolve().parents[1]
    hits = [str(p.relative_to(root)) for p in root.rglob("*.py")
            if "tests" not in p.relative_to(root).parts and pattern.search(p.read_text())]
    assert hits == []


def test_generate_text_next_model_in_route_marks_fallback_used():
    route = gw.Route("tp_compose", "writer", (CONVERSE, SONNET), "acc3")
    acc3 = StubBedrock(response=_claude_reply(), fail_converse=True)
    _, call = gw.generate_text(route, "s", "u", 50,
                               client_factory=make_factory({"acc3": acc3}, []))
    assert (call.model, call.account, call.fallback_used) == ("sonnet", "acc3", True)
    assert [c[0] for c in acc3.calls] == ["converse", "invoke_model"]


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
    factory = make_factory({"acc3": StubBedrock(fail=True)}, [])
    with pytest.raises(bs.BedrockError, match="All models failed"):
        gw.generate_text(ROUTE, "s", "u", 50, client_factory=factory)


def test_missing_role_raises(monkeypatch):
    monkeypatch.setattr(config, "BEDROCK_ROLE_NAME_PRIMARY", "")
    factory = make_factory({"acc3": StubBedrock(response=_claude_reply())}, [])
    with pytest.raises(bs.BedrockError):
        gw.generate_text(ROUTE, "s", "u", 50, client_factory=factory)



def test_unset_satellite_account_fails_without_an_sts_call(monkeypatch):
    """AA-902: no account-ID default in this public repo; an unset acc3 id is skipped, not assumed."""
    monkeypatch.setattr(config, "BEDROCK_ACCT_PRIMARY", "")
    arns: list = []
    factory = make_factory({"acc3": StubBedrock(response=_claude_reply())}, arns)
    with pytest.raises(bs.BedrockError, match="BEDROCK_ACCT_PRIMARY is unset"):
        gw.generate_text(ROUTE, "s", "u", 50, client_factory=factory)
    assert arns == []
    with pytest.raises(bs.BedrockError, match="BEDROCK_ACCT_PRIMARY is unset"):
        gw._satellite_target("acc3")

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

class PgError(Exception):
    """An asyncpg-like error with its SQLSTATE."""

    def __init__(self, sqlstate, message="pg error"):
        super().__init__(message)
        self.sqlstate = sqlstate


def _row(args) -> dict:
    """The ledger v2 INSERT arguments by column name."""
    assert len(args) == len(ledger.COLUMNS)
    return dict(zip(ledger.COLUMNS, args))


class FakeDb:
    """`view_rows` None = CIS 323 not applied (the view read raises undefined_table)."""

    def __init__(self, role_rows=None, catalog_rows=None, fail=False, fail_execute=False, view_rows=None,
                 view_error=None):
        self.role_rows = role_rows or []
        self.catalog_rows = catalog_rows or []
        self.view_rows = view_rows
        self.view_error = view_error
        self.fail = fail
        self.fail_execute = fail_execute
        self.fetches = 0
        self.sql: list = []
        self.executed: list = []

    async def fetch(self, sql, *args):
        self.fetches += 1
        self.sql.append(sql)
        if self.fail:
            raise RuntimeError("permission denied for table llm_role_config")
        if contract.GATEWAY_CONFIG_VIEW in sql:
            if self.view_error is not None:
                raise self.view_error
            if self.view_rows is None:
                raise PgError("42P01", f'relation "{contract.GATEWAY_CONFIG_VIEW}" does not exist')
            return self.view_rows
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
    assert db.fetches == 3  # the missing view, then the two tables; the second call served from the cache


async def test_load_route_pauses_ai_when_db_is_unreadable(capsys):
    """AA-642 S0: an unreadable route never falls back to the hard-coded models (fail closed)."""
    with pytest.raises(gw.AiPaused) as e:
        await gw.load_route(FakeDb(fail=True), "tp_search_embed")
    assert e.value.reason == "route_unreadable"
    assert isinstance(e.value, gw.BedrockError)
    assert "tp_search_embed" not in gw._cache                 # not cached: the next request reads again
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["evt"] == "llm_gateway_error" and line["level"] == "error"
    assert line["what"] == "route_unreadable"


async def test_record_writes_one_row_tagged_with_the_app():
    db = FakeDb()
    call = gw.Call("tp_compose", "writer", "sonnet", "acc3", 10, 5, 0.0001, False, "end_turn")
    await gw.record(db, call, {"mode": "compose"})
    sql, args = db.executed[0]
    assert sql == ledger.insert_sql()
    row = _row(args)
    assert (row["stage"], row["role"], row["model"]) == ("tp_compose", "writer", "sonnet")
    assert json.loads(row["quality_signal"]) == {"app": "tripplanner", "mode": "compose"}
    assert (row["provider"], row["app"], row["status"]) == ("bedrock-satellite", "tripplanner", "ok")


async def test_record_never_raises():
    call = gw.Call("tp_compose", "writer", "sonnet", "acc3", 1, 1, 0.0, False)
    await gw.record(FakeDb(fail_execute=True), call, {})


# --- AA-805: fork defects ---------------------------------------------------------

def test_failed_attempts_are_reported_for_logging():
    route = gw.Route("tp_compose", "writer", (CONVERSE, SONNET), "acc3")
    factory = make_factory({"acc3": StubBedrock(response=_claude_reply(), fail_converse=True)}, [])
    failures: list = []
    _, call = gw.generate_text(route, "s", "u", 50, client_factory=factory, failures=failures)
    assert (call.model, call.account, call.failed) == ("sonnet", "acc3", False)
    assert [(f.model, f.account, f.failed, f.stop_reason, f.cost_usd) for f in failures] == \
        [("sonnet-5", "acc3", True, "error", 0.0)]
    assert "simulated failure" in failures[0].extra["error"]


def test_all_failed_attempts_are_reported_before_the_raise():
    route = gw.Route("tp_compose", "writer", (CONVERSE, SONNET), "acc3")
    factory = make_factory({"acc3": StubBedrock(fail=True)}, [])
    failures: list = []
    with pytest.raises(bs.BedrockError):
        gw.generate_text(route, "s", "u", 50, client_factory=factory, failures=failures)
    assert [(f.model, f.account) for f in failures] == [("sonnet-5", "acc3"), ("sonnet", "acc3")]


def test_embed_failed_attempt_is_reported():
    failures: list = []
    with pytest.raises(bs.BedrockError):
        gw.embed_texts(gw.builtin_route("tp_search_embed"), ["x"],
                       client_factory=make_factory({"acc2": StubBedrock(fail=True)}, []),
                       failures=failures)
    assert len(failures) == 1 and failures[0].stage == "tp_search_embed"


def test_missing_catalog_price_is_null_not_zero():
    unpriced = gw.Model("new", "anthropic", "anthropic_native", {"acc3": "g.new"}, None, None)
    acc3 = StubBedrock(response=_claude_reply(tin=100, tout=20))
    _, call = gw.generate_text(gw.Route("tp_compose", "writer", (unpriced,), "acc3"), "s", "u", 50,
                               client_factory=make_factory({"acc3": acc3}, []))
    assert call.cost_usd is None
    # an embedding needs no output price
    emb = gw.Model("e", "cohere", "embed", {"acc2": "e"}, 0.12, None)
    assert gw._cost(emb, 1000, 0) == pytest.approx(0.00012)


async def test_load_route_keeps_a_null_price_and_notes_a_shadow(capsys):
    db = FakeDb(
        role_rows=[{"role": "writer", "model_id": "sonnet", "fallback_model_ids": [],
                    "account_route": "acc3", "shadow_model_id": "sonnet-5", "shadow_sample_pct": 10}],
        catalog_rows=[{"model_key": "sonnet", "vendor": "anthropic", "api_style": "anthropic_native",
                       "bedrock_profile_ids": {"acc3": "g.s"}, "price_in_per_mtok": None,
                       "price_out_per_mtok": 15, "price_cache_read_per_mtok": None,
                       "price_cache_write_per_mtok": None, "enabled": True}])
    route = await gw.load_route(db, "tp_compose")
    assert route.models[0].price_in is None and route.models[0].price_out == 15.0
    assert route.shadow_model == "sonnet-5"
    assert "does not run shadows" in capsys.readouterr().out


async def test_record_logs_null_cost_and_failed_flag_and_counts_spend():
    db = FakeDb()
    failed = gw._failed(ROUTE, SONNET, "acc3", 0, "AccessDenied")
    await gw.record(db, failed, {"mode": "compose"})
    sql, args = db.executed[0]
    row = _row(args)
    assert row["cost_usd"] == 0.0 and row["stop_reason"] == "error"
    assert (row["status"], row["error_class"], row["pricing_mode"]) == ("error", "other", None)
    signal = json.loads(row["quality_signal"])
    assert signal["failed"] is True and signal["error"] == "AccessDenied"
    assert "tripplanner.llm_spend_daily" in db.executed[1][0]

    db = FakeDb()
    await gw.record(db, gw.Call("tp_compose", "writer", "new", "acc3", 10, 5, None, False), {})
    assert _row(db.executed[0][1])["cost_usd"] is None        # NULL, not 0
    assert _row(db.executed[0][1])["pricing_mode"] is None    # nothing priced it
    assert db.executed[1][1] == ("tp_compose", 0.0, 1)        # adds nothing, counted as unpriced


class SpendDb:
    def __init__(self, spent=None, fail=False):
        self.spent, self.fail = spent, fail

    async def fetchrow(self, sql, *args):
        if self.fail:
            raise RuntimeError('relation "tripplanner.llm_spend_daily" does not exist')
        return {"spent": self.spent}


async def test_budget_status_against_the_cap(monkeypatch):
    monkeypatch.setattr(config, "TP_DAILY_SPEND_CAP_USD", 5.0)
    under = await gw.budget_status(SpendDb(1.25))
    assert under["exhausted"] is False and under["reason"] is None
    over = await gw.budget_status(SpendDb(5.0))
    assert over["exhausted"] is True and over["reason"] == "cap_reached"


async def test_budget_status_fails_closed_when_the_counter_is_unreadable(monkeypatch, capsys):
    """AA-642 S0 (root ADR 0009 decision 6): replaces the AA-805 fail-open test."""
    monkeypatch.setattr(config, "TP_DAILY_SPEND_CAP_USD", 5.0)
    closed = await gw.budget_status(SpendDb(fail=True))
    assert closed == {"spent_usd": None, "cap_usd": 5.0, "exhausted": True,
                      "reason": "spend_unreadable", "near_cap": False}
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["evt"] == "llm_gateway_error" and line["level"] == "error"


async def test_a_failed_spend_write_pauses_ai_for_the_hold_window(monkeypatch):
    """AA-642 S0: a lost counter write undercounts, so AI pauses for SPEND_UNREADABLE_HOLD_SECONDS
    even though the counter itself reads fine, then recovers."""
    monkeypatch.setattr(config, "TP_DAILY_SPEND_CAP_USD", 5.0)
    now = [1000.0]
    monkeypatch.setattr(gw.time, "monotonic", lambda: now[0])
    db = FakeDb(fail_execute=True)
    await gw.record(db, gw.Call("tp_compose", "writer", "sonnet", "acc3", 1, 1, 0.01, False), {})
    assert (await gw.budget_status(SpendDb(0.0)))["reason"] == "spend_unreadable"
    now[0] += gw.SPEND_UNREADABLE_HOLD_SECONDS - 1
    assert (await gw.budget_status(SpendDb(0.0)))["exhausted"] is True
    now[0] += 2
    assert (await gw.budget_status(SpendDb(0.0)))["exhausted"] is False


async def test_a_failed_ledger_write_is_logged_as_an_error(capsys):
    """AA-642 S0: an unlogged paid call is an ERROR line, never a quiet note."""
    db = FakeDb(fail_execute=True)
    await gw.record(db, gw.Call("tp_extract", "writer", "sonnet", "acc3", 1, 1, 0.01, False), {})
    lines = [json.loads(x) for x in capsys.readouterr().out.strip().splitlines()]
    assert [x["what"] for x in lines if x["evt"] == "llm_gateway_error"] == ["call_log_write_failed"]
    assert gw.spend_unreadable_flag_active() is False         # offline stage: no counter write


class SpendLedgerDb:
    """In-memory llm_call_log + llm_spend_daily: upserts add per stage, the read sums the
    stages passed as $1 (mirrors _SPEND_UPSERT_SQL / _SPEND_TODAY_SQL)."""

    def __init__(self):
        self.by_stage: dict[str, float] = {}
        self.logged: list[str] = []

    async def execute(self, sql, *args):
        if "shared.llm_call_log" in sql:
            self.logged.append(args[1])
        elif "tripplanner.llm_spend_daily" in sql:
            stage, cost, _unpriced = args
            self.by_stage[stage] = self.by_stage.get(stage, 0.0) + cost

    async def fetchrow(self, sql, *args):
        assert "stage = ANY($1" in sql
        return {"spent": sum(v for k, v in self.by_stage.items() if k in args[0])}


async def test_offline_stages_never_exhaust_the_customer_budget(monkeypatch):
    """AA-805 review: an extraction/embedding backfill over the cap must not 503 search or drop
    narration to 'basic' — only ONLINE_STAGES count."""
    monkeypatch.setattr(config, "TP_DAILY_SPEND_CAP_USD", 5.0)
    db = SpendLedgerDb()
    await gw.record(db, gw.Call("tp_extract", "writer", "sonnet", "acc3", 1, 1, 40.0, False), {})
    await gw.record(db, gw.Call("tp_component_embed", "embed", "cohere", None, 1, 0, 9.0, False), {})
    assert db.logged == ["tp_extract", "tp_component_embed"]   # still in llm_call_log
    assert db.by_stage == {}                                    # but not in the spend counter
    status = await gw.budget_status(db)
    assert status["exhausted"] is False and status["spent_usd"] == 0.0

    await gw.record(db, gw.Call("tp_compose", "writer", "sonnet", "acc3", 1, 1, 3.0, False), {})
    await gw.record(db, gw.Call("tp_search_embed", "embed", "cohere", None, 1, 0, 2.0, False), {})
    assert (await gw.budget_status(db))["exhausted"] is True


def test_online_stages_are_the_customer_facing_routes():
    assert set(gw.ONLINE_STAGES) == {"tp_compose", "tp_search_embed"}
    assert all(s in gw.BUILTIN_ROUTES for s in gw.ONLINE_STAGES)


# --- AA-1015: stage caps and the 80 % alert ------------------------------------------------------

class StageSpendDb:
    """Answers the spend read per stage list: the combined read gets the sum, a one-stage read that stage."""

    def __init__(self, by_stage):
        self.by_stage = by_stage
        self.reads: list = []

    async def fetchrow(self, sql, *args):
        self.reads.append(list(args[0]))
        return {"spent": sum(v for k, v in self.by_stage.items() if k in args[0])}


async def test_without_stage_caps_only_the_combined_cap_applies(monkeypatch):
    """Unset stage caps (the default) = the AA-805 behaviour: one combined read, one combined cap."""
    monkeypatch.setattr(config, "TP_DAILY_SPEND_CAP_USD", 5.0)
    monkeypatch.setattr(config, "TP_DAILY_SPEND_CAP_COMPOSE_USD", None)
    db = StageSpendDb({"tp_compose": 3.0, "tp_search_embed": 1.0})
    status = await gw.budget_status(db, "tp_compose")
    assert status == {"spent_usd": 4.0, "cap_usd": 5.0, "exhausted": False, "reason": None, "near_cap": True}
    assert db.reads == [list(gw.ONLINE_STAGES)]


async def test_a_stage_cap_stops_that_stage_only(monkeypatch):
    monkeypatch.setattr(config, "TP_DAILY_SPEND_CAP_USD", 5.0)
    monkeypatch.setattr(config, "TP_DAILY_SPEND_CAP_SEARCH_USD", 1.0)
    monkeypatch.setattr(config, "TP_DAILY_SPEND_CAP_COMPOSE_USD", 4.0)
    db = StageSpendDb({"tp_compose": 0.5, "tp_search_embed": 1.0})
    search = await gw.budget_status(db, "tp_search_embed")
    assert search["exhausted"] is True and search["reason"] == "cap_reached"
    assert (search["stage"], search["stage_spent_usd"], search["stage_cap_usd"]) == ("tp_search_embed", 1.0, 1.0)
    compose = await gw.budget_status(db, "tp_compose")
    assert compose["exhausted"] is False and compose["near_cap"] is False and compose["stage_cap_usd"] == 4.0
    # the combined cap still applies over a stage cap
    over = await gw.budget_status(StageSpendDb({"tp_compose": 0.1, "tp_search_embed": 5.0}), "tp_compose")
    assert over["exhausted"] is True and over["stage_spent_usd"] == 0.1


async def test_near_cap_and_cap_alerts_print_once_per_day_and_scope(monkeypatch, capsys):
    """AA-1015: the 80 % alert fires (a warning line), the cap alert fires (an error line), each once."""
    monkeypatch.setattr(config, "TP_DAILY_SPEND_CAP_USD", 5.0)
    monkeypatch.setattr(config, "TP_DAILY_SPEND_CAP_SEARCH_USD", 1.0)
    db = StageSpendDb({"tp_compose": 3.5, "tp_search_embed": 0.85})
    for _ in range(3):
        status = await gw.budget_status(db, "tp_search_embed")
    assert status["near_cap"] is True and status["exhausted"] is False
    db.by_stage["tp_search_embed"] = 1.2
    await gw.budget_status(db, "tp_search_embed")
    await gw.budget_status(db, "tp_search_embed")
    alerts = [json.loads(x) for x in capsys.readouterr().out.splitlines() if '"llm_spend_alert"' in x]
    got = [(a["scope"], a["threshold"], a["level"]) for a in alerts]
    assert got == [("total", "near", "warning"), ("tp_search_embed", "near", "warning"),
                   ("tp_search_embed", "cap", "error")]


async def test_under_80_percent_prints_no_alert(monkeypatch, capsys):
    monkeypatch.setattr(config, "TP_DAILY_SPEND_CAP_USD", 5.0)
    status = await gw.budget_status(StageSpendDb({"tp_compose": 3.9}))
    assert status["near_cap"] is False
    assert "llm_spend_alert" not in capsys.readouterr().out


# --- AA-642 S8: the CIS contract views (CIS 323) and the ledger v2 row ---------------------------------------

def _view_row(position, key, *, in_catalog=True, api_style="converse", enabled=True, price_in=2.0,
              profiles=None, role="writer", shadow=None, shadow_pct=0):
    return {"stage": "tp_compose", "role": role, "is_active": True, "account_route": "acc3",
            "allowed_scopes": None, "shadow_model_id": shadow, "shadow_sample_pct": shadow_pct,
            "position": position, "model_key": key, "in_catalog": in_catalog,
            "vendor": "anthropic" if in_catalog else None, "provider": "bedrock" if in_catalog else None,
            "api_style": api_style if in_catalog else None,
            "bedrock_profile_ids": (profiles or {"acc3": f"zz.{key}"}) if in_catalog else None,
            "wire_model": None, "max_output_tokens": 4096, "model_enabled": enabled if in_catalog else None,
            "price_in_per_mtok": price_in if in_catalog else None, "price_out_per_mtok": 10 if in_catalog else None,
            "price_cache_read_per_mtok": None, "price_cache_write_per_mtok": None, "deployments": [],
            "route_updated_at": None}


async def test_load_route_reads_the_contract_view_in_chain_order():
    db = FakeDb(view_rows=[_view_row(2, "zz-writer-c", profiles=json.dumps({"acc3": "zz.c"})),
                           _view_row(1, "zz-not-in-catalog", in_catalog=False),
                           _view_row(0, "zz-writer-a", enabled=False)])
    route = await gw.load_route(db, "tp_compose")
    assert [m.key for m in route.models] == ["zz-writer-a", "zz-writer-c"]     # position order, unknown key skipped
    assert route.models[0].enabled is False and route.models[1].profiles == {"acc3": "zz.c"}
    assert (route.role, route.account_route) == ("writer", "acc3")
    assert db.fetches == 1 and contract.GATEWAY_CONFIG_VIEW in db.sql[0]       # no read of the shared.* tables


async def test_load_route_from_the_view_notes_a_shadow(capsys):
    db = FakeDb(view_rows=[_view_row(0, "zz-writer-a", shadow="zz-writer-b", shadow_pct=5)])
    route = await gw.load_route(db, "tp_compose")
    assert route.shadow_model == "zz-writer-b"
    assert "does not run shadows" in capsys.readouterr().out


async def test_an_empty_view_pauses_ai():
    with pytest.raises(gw.AiPaused):
        await gw.load_route(FakeDb(view_rows=[]), "tp_compose")


async def test_a_view_error_other_than_missing_pauses_ai_without_the_tables():
    db = FakeDb(view_error=PgError("57014", "canceling statement due to statement timeout"),
                role_rows=[{"role": "writer", "model_id": "sonnet", "fallback_model_ids": [],
                            "account_route": "acc3"}])
    with pytest.raises(gw.AiPaused):
        await gw.load_route(db, "tp_compose")
    assert db.fetches == 1


@pytest.mark.parametrize("sqlstate", ["42P01", "42501"])
async def test_a_missing_or_ungranted_view_falls_back_and_is_not_retried_for_a_while(monkeypatch, capsys, sqlstate):
    now = [500.0]
    monkeypatch.setattr(gw.time, "monotonic", lambda: now[0])
    db = FakeDb(view_error=PgError(sqlstate),
                role_rows=[{"role": "writer", "model_id": "sonnet", "fallback_model_ids": [], "account_route": "acc3"}],
                catalog_rows=[{"model_key": "sonnet", "vendor": "anthropic", "api_style": "anthropic_native",
                               "bedrock_profile_ids": {"acc3": "g.s"}, "price_in_per_mtok": 3,
                               "price_out_per_mtok": 15, "price_cache_read_per_mtok": None,
                               "price_cache_write_per_mtok": None, "enabled": True}])
    assert [m.key for m in (await gw.load_route(db, "tp_compose")).models] == ["sonnet"]
    assert db.fetches == 3 and "not readable yet" in capsys.readouterr().out
    gw._cache.clear()
    await gw.load_route(db, "tp_compose")
    assert db.fetches == 5                                    # the tables only: the view waits VIEW_RETRY_SECONDS
    gw._cache.clear()
    now[0] += gw.VIEW_RETRY_SECONDS + 1
    await gw.load_route(db, "tp_compose")
    assert db.fetches == 8 and contract.GATEWAY_CONFIG_VIEW in db.sql[5]


class ViewSpendDb:
    """The spend read: the contract view when `view` holds a value, a missing view (42P01) when None."""

    def __init__(self, view=None, counter=0.0, view_error=None):
        self.view, self.counter, self.view_error = view, counter, view_error
        self.sql: list = []

    async def fetchrow(self, sql, *args):
        self.sql.append(sql)
        if contract.APP_SPEND_VIEW in sql:
            if self.view_error is not None:
                raise self.view_error
            if self.view is None:
                raise PgError("42P01")
            return {"spent": self.view}
        assert "tripplanner.llm_spend_daily" in sql
        return {"spent": self.counter}


async def test_budget_reads_the_spend_view(monkeypatch):
    monkeypatch.setattr(config, "TP_DAILY_SPEND_CAP_USD", 5.0)
    db = ViewSpendDb(view=5.5, counter=0.0)
    status = await gw.budget_status(db)
    assert status["exhausted"] is True and status["spent_usd"] == 5.5
    assert db.sql == [contract.APP_SPEND_SQL]


async def test_budget_falls_back_to_the_counter_while_the_view_is_missing(monkeypatch):
    monkeypatch.setattr(config, "TP_DAILY_SPEND_CAP_USD", 5.0)
    db = ViewSpendDb(view=None, counter=1.0)
    status = await gw.budget_status(db)
    assert status["spent_usd"] == 1.0 and status["exhausted"] is False
    await gw.budget_status(db)
    assert sum(contract.APP_SPEND_VIEW in q for q in db.sql) == 1          # not retried within the window


async def test_budget_fails_closed_on_another_view_error(monkeypatch):
    monkeypatch.setattr(config, "TP_DAILY_SPEND_CAP_USD", 5.0)
    status = await gw.budget_status(ViewSpendDb(view_error=PgError("08006", "connection failure"), counter=0.0))
    assert status["reason"] == "spend_unreadable" and status["spent_usd"] is None


def test_attempts_carry_number_latency_wire_and_outcome():
    route = gw.Route("tp_compose", "writer", (CONVERSE, SONNET), "acc3")
    acc3 = StubBedrock(response=_claude_reply(), fail_converse=True)
    failures: list = []
    _, call = gw.generate_text(route, "s", "u", 50, client_factory=make_factory({"acc3": acc3}, []),
                               failures=failures)
    (failed,) = failures
    assert (failed.attempt_no, call.attempt_no) == (1, 2)
    assert failed.latency_ms >= 0 and call.latency_ms >= 0
    assert (failed.wire_id, call.wire_id) == ("global.sonnet-5", "global.sonnet")
    assert (failed.status, failed.error_class) == ("error", "other")         # "simulated failure": not a refusal


def test_a_refused_attempt_is_classified_by_the_shared_core():
    refused = gw._failed(ROUTE, SONNET, "acc3", 0,
                         "An error occurred (AccessDeniedException) when calling the InvokeModel operation")
    assert (refused.status, refused.error_class) == ("refused", "access_denied")
    throttled = gw._failed(ROUTE, SONNET, "acc3", 0, "ThrottlingException: Too many requests")
    assert (throttled.status, throttled.error_class) == ("refused", "throttled")


def test_cache_tokens_reach_the_call():
    reply = _claude_reply()
    reply["usage"].update(cache_read_input_tokens=300, cache_creation_input_tokens=40)
    _, call = gw.generate_text(ROUTE, "s", "u", 50,
                               client_factory=make_factory({"acc3": StubBedrock(response=reply)}, []))
    assert (call.cache_read_tokens, call.cache_write_tokens) == (300, 40)


async def test_record_writes_the_ledger_v2_columns():
    db = FakeDb()
    call = gw.Call("tp_search_embed", "embed", "cohere-embed-v4", "acc2", 12, 0, 0.0000014, False,
                   latency_ms=38, attempt_no=1, cache_read_tokens=0, wire_id="us.zz-embed")
    await gw.record(db, call, {"cache": "miss"})
    row = _row(db.executed[0][1])
    assert (row["app"], row["status"], row["error_class"]) == ("tripplanner", "ok", None)
    assert (row["latency_ms"], row["attempt_no"], row["connection_key"]) == (38, 1, "acc2")
    assert (row["deployment_wire_id"], row["pricing_mode"], row["provider"]) == ("us.zz-embed", "catalog",
                                                                                   "bedrock-native")
    assert (row["cache_read_tokens"], row["cache_write_tokens"], row["is_shadow"]) == (None, None, False)


class LegacyLedgerDb(FakeDb):
    """llm_call_log before CIS 319: the v2 INSERT fails with undefined_column."""

    async def execute(self, sql, *args):
        if "attempt_no" in sql:
            raise PgError("42703", 'column "app" of relation "llm_call_log" does not exist')
        self.executed.append((sql, args))


async def test_record_falls_back_to_the_pre_319_row():
    db = LegacyLedgerDb()
    await gw.record(db, gw.Call("tp_compose", "writer", "sonnet", "acc3", 10, 5, 0.0001, False, "end_turn"),
                    {"mode": "compose"})
    sql, args = db.executed[0]
    assert sql == gw._LEGACY_INSERT_SQL and len(args) == 12
    assert args[1:4] == ("tp_compose", "writer", "sonnet") and args[-1] == "bedrock-satellite"
    assert json.loads(args[7]) == {"app": "tripplanner", "mode": "compose"}
    assert gw.spend_unreadable_flag_active() is False


async def test_a_lost_ledger_row_of_an_online_stage_pauses_ai(monkeypatch):
    """The spend view sums the ledger: a lost online row undercounts, so AI pauses for the hold window even when
    the tp-010 counter write succeeded."""
    monkeypatch.setattr(config, "TP_DAILY_SPEND_CAP_USD", 5.0)
    now = [2000.0]
    monkeypatch.setattr(gw.time, "monotonic", lambda: now[0])

    class LedgerDown(FakeDb):
        async def execute(self, sql, *args):
            if "shared.llm_call_log" in sql:
                raise PgError("08006", "connection failure")
            self.executed.append((sql, args))

    db = LedgerDown()
    await gw.record(db, gw.Call("tp_search_embed", "embed", "cohere-embed-v4", "acc2", 1, 0, 0.001, False), {})
    assert "tripplanner.llm_spend_daily" in db.executed[0][0]              # the counter write still happened
    assert (await gw.budget_status(ViewSpendDb(view=0.0)))["reason"] == "spend_unreadable"
