"""AA-1027 E4 (root plan S263, gap TP-5; root ADR 0009 §4): the gateway applies the stage x model parameters
(`shared.stage_model_params`, read as the `stage_params` column CIS 350 appends to the config view, with the catalog's
`supports_temperature`) to each text attempt's request body, with CIS's precedence. A stub bedrock-runtime client
logs every body it is sent. Fully stubbed: no AWS, no DB. Invented model keys, wire ids and text only."""
from __future__ import annotations

import json

import pytest

from backend import config
from backend.aa_llm import contract
from backend.shared import llm_gateway as gw
from backend.tests.test_llm_gateway import FakeDb, PgError, StubBedrock, _claude_reply, _row, _view_row, make_factory


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    monkeypatch.setattr(config, "BEDROCK_ROLE_NAME", "")
    monkeypatch.setattr(config, "BEDROCK_ROLE_NAME_PRIMARY", "ZzInvokerRole")
    monkeypatch.setattr(config, "BEDROCK_ACCT_PRIMARY", "111111111111")
    monkeypatch.setattr(config, "LLM_STAGE_PARAMS", True)
    gw._cache.clear()
    gw._view_missing_until.clear()
    gw._PARAMS_NOT_APPLIED.clear()
    yield
    gw._PARAMS_NOT_APPLIED.clear()


def _model(api_style="anthropic_native", *, supports=True, **params):
    return gw.Model("zz-writer", "anthropic", api_style, {"acc3": "zz.wire-writer"}, 3.0, 15.0,
                    supports_temperature=supports, params=gw.StageParams(**params) if params else None)


def _converse_reply():
    return {"output": {"message": {"content": [{"text": "Day 1: Lanterns over Qorvale."}]}}, "stopReason": "end_turn",
            "usage": {"inputTokens": 100, "outputTokens": 20}}


def _run(model, *, stub=None, max_tokens=900, failures=None):
    stub = stub or StubBedrock(response=_converse_reply() if model.api_style == "converse" else _claude_reply())
    route = gw.Route("tp_compose", "writer", (model,), "acc3")
    _, call = gw.generate_text(route, "zz system", "zz user", max_tokens,
                               client_factory=make_factory({"acc3": stub}, []), failures=failures)
    return stub, call


def _events(capsys, evt):
    return [json.loads(x) for x in capsys.readouterr().out.splitlines() if f'"{evt}"' in x]


# --- the request body ---------------------------------------------------------------------------------------------

def test_no_params_row_keeps_the_request_body_exactly_as_before():
    stub, call = _run(_model())
    (_, wire, body), = stub.calls
    assert wire == "zz.wire-writer"
    assert body == {"anthropic_version": "bedrock-2023-05-31", "max_tokens": 900, "system": "zz system",
                    "messages": [{"role": "user", "content": "zz user"}]}
    assert call.effort is None and "stage_params" not in call.extra


def test_temperature_goes_into_the_anthropic_native_body_but_no_effort(capsys):
    # Bedrock InvokeModel: CIS sends effort only on the Anthropic Messages API, never on Bedrock, until a probe has
    # shown the field; so the row's effort is said once and not sent, and the ledger effort stays NULL.
    stub, call = _run(_model(temperature=0.3, effort="low"))
    (_, _, body), = stub.calls
    assert body["temperature"] == 0.3 and "output_config" not in body
    assert set(body) == {"anthropic_version", "max_tokens", "system", "messages", "temperature"}
    assert body["max_tokens"] == 900                                     # the call site's, untouched
    assert call.effort is None and call.extra["stage_params"] == {"temperature": 0.3}
    [applied] = _events(capsys, "llm_stage_params_applied")
    assert (applied["stage"], applied["model"], applied["fields"]) == ("tp_compose", "zz-writer", ["temperature"])


def test_an_effort_only_row_leaves_the_body_unchanged_and_is_said_once(capsys):
    model = _model(effort="high")
    stub, call = _run(model)
    _run(model)
    (_, _, body), = stub.calls[:1]
    assert body == {"anthropic_version": "bedrock-2023-05-31", "max_tokens": 900, "system": "zz system",
                    "messages": [{"role": "user", "content": "zz user"}]}
    assert call.effort is None and "stage_params" not in call.extra
    out = capsys.readouterr().out
    assert '"llm_stage_params_applied"' not in out
    notes = [json.loads(x) for x in out.splitlines() if '"llm_stage_params_not_applied"' in x]
    assert [(n["field"], n["api_style"]) for n in notes] == [("effort", "anthropic_native")]   # once per container


async def test_the_ledger_row_carries_the_applied_fields_and_no_effort():
    _, call = _run(_model(temperature=0.3, effort="medium"))
    db = FakeDb()
    await gw.record(db, call, {"mode": "compose"})
    row = _row(db.executed[0][1])
    assert row["effort"] is None
    assert json.loads(row["quality_signal"])["stage_params"] == {"temperature": 0.3}


def test_a_model_that_takes_no_temperature_never_gets_one(capsys):
    for supports in (False, None):                                       # None: the catalog said nothing
        gw._PARAMS_NOT_APPLIED.clear()
        stub, call = _run(_model(supports=supports, temperature=0.7))
        (_, _, body), = stub.calls
        assert "temperature" not in body and "stage_params" not in call.extra
        [note] = _events(capsys, "llm_stage_params_not_applied")
        assert (note["field"], note["level"]) == ("temperature", "warning")


def test_converse_takes_the_temperature_but_no_effort_said_once(capsys):
    model = _model("converse", temperature=0.2, effort="high")
    stub, call = _run(model)
    _run(model)
    (_, _, kwargs) = stub.calls[0]
    assert kwargs["inferenceConfig"] == {"maxTokens": 900, "temperature": 0.2}
    assert "output_config" not in kwargs and "additionalModelRequestFields" not in kwargs
    assert call.effort is None and call.extra["stage_params"] == {"temperature": 0.2}
    notes = _events(capsys, "llm_stage_params_not_applied")
    assert [(n["field"], n["api_style"]) for n in notes] == [("effort", "converse")]   # once per container


def test_the_call_sites_max_tokens_wins_over_the_row(capsys):
    stub, call = _run(_model(max_tokens=4000, min_output_tokens=2000), max_tokens=700)
    (_, _, body), = stub.calls
    assert body["max_tokens"] == 700 and "stage_params" not in call.extra
    assert [n["field"] for n in _events(capsys, "llm_stage_params_not_applied")] == ["max_tokens"]


def test_reasoning_mode_is_never_sent(capsys):
    stub, _ = _run(_model(reasoning_mode="adaptive"))
    (_, _, body), = stub.calls
    assert set(body) == {"anthropic_version", "max_tokens", "system", "messages"}
    assert [n["field"] for n in _events(capsys, "llm_stage_params_not_applied")] == ["reasoning_mode"]


def test_the_switch_off_sends_nothing(monkeypatch):
    monkeypatch.setattr(config, "LLM_STAGE_PARAMS", False)
    stub, call = _run(_model(temperature=0.3, effort="low"))
    (_, _, body), = stub.calls
    assert "temperature" not in body and "output_config" not in body and call.effort is None


def test_failed_attempts_say_which_params_their_body_carried():
    failures: list = []
    fail_then = StubBedrock(fail=True)
    route = gw.Route("tp_compose", "writer", (_model(temperature=0.4, effort="low"),), "acc3")
    with pytest.raises(gw.BedrockError):
        gw.generate_text(route, "s", "u", 100, client_factory=make_factory({"acc3": fail_then}, []),
                         failures=failures)
    (failed,) = failures
    assert failed.effort is None and failed.extra["stage_params"] == {"temperature": 0.4}
    assert fail_then.calls[0][2]["temperature"] == 0.4 and "output_config" not in fail_then.calls[0][2]


def test_each_model_of_the_route_uses_its_own_row():
    a = gw.Model("zz-writer-a", "anthropic", "anthropic_native", {"acc3": "zz.a"}, 3.0, 15.0,
                 supports_temperature=True, params=gw.StageParams(temperature=0.9))
    b = gw.Model("zz-writer-b", "anthropic", "anthropic_native", {"acc3": "zz.b"}, 3.0, 15.0,
                 supports_temperature=True)

    class FirstFails(StubBedrock):
        def invoke_model(self, *, modelId, body, **kwargs):
            self.calls.append(("invoke_model", modelId, json.loads(body)))
            if modelId == "zz.a":
                raise RuntimeError("simulated failure")
            return {"body": type("B", (), {"read": lambda s: json.dumps(_claude_reply()).encode()})()}

    stub = FirstFails()
    gw.generate_text(gw.Route("tp_compose", "writer", (a, b), "acc3"), "s", "u", 100,
                     client_factory=make_factory({"acc3": stub}, []))
    assert stub.calls[0][2]["temperature"] == 0.9 and "temperature" not in stub.calls[1][2]


# --- the config read (CIS 350 columns) ----------------------------------------------------------------------------

def _row350(**over):
    return {**_view_row(0, "zz-writer", api_style="anthropic_native"), "route_deadline_s": None,
            "deployment_routing": [], **over}


async def test_the_350_columns_give_the_params_and_supports_temperature():
    db = FakeDb(view_rows=[_row350(supports_temperature=True, stage_params=json.dumps(
        {"max_tokens": None, "min_output_tokens": None, "temperature": 0.25, "effort": "high",
         "reasoning_mode": None}))])
    route = await gw.load_route(db, "tp_compose")
    assert db.sql == [gw.GATEWAY_PARAMS_SQL] and "stage_params" in db.sql[0]
    (m,) = route.models
    assert m.supports_temperature is True and m.params == gw.StageParams(temperature=0.25, effort="high")


async def test_a_null_stage_params_is_no_row():
    db = FakeDb(view_rows=[_row350(supports_temperature=False, stage_params=None)])
    (m,) = (await gw.load_route(db, "tp_compose")).models
    assert (m.supports_temperature, m.params) == (False, None)
    assert gw.request_params("tp_compose", m) == {}


async def test_values_outside_the_table_checks_are_dropped():
    db = FakeDb(view_rows=[_row350(supports_temperature=True, stage_params={
        "temperature": 7, "effort": "turbo", "reasoning_mode": "always", "max_tokens": 0, "min_output_tokens": True})])
    (m,) = (await gw.load_route(db, "tp_compose")).models
    assert m.params is None


async def test_before_350_the_341_columns_are_read_and_no_params_apply(capsys):
    class Before350(FakeDb):
        async def fetch(self, sql, *args):
            self.sql.append(sql)
            if "stage_params" in sql:
                raise PgError("42703", 'column "stage_params" does not exist')
            return self.view_rows

    db = Before350(view_rows=[_row350()])
    (m,) = (await gw.load_route(db, "tp_compose")).models
    assert db.sql == [gw.GATEWAY_PARAMS_SQL, gw.GATEWAY_ROUTING_SQL]
    assert (m.supports_temperature, m.params) == (None, None)
    assert "CIS migration 350" in capsys.readouterr().out
    gw._cache.clear()
    await gw.load_route(db, "tp_compose")                                # within VIEW_RETRY_SECONDS
    assert db.sql[2:] == [gw.GATEWAY_ROUTING_SQL]


def test_the_contract_select_is_a_prefix_of_every_column_set():
    for _, columns, _ in gw._COLUMN_SETS:
        assert columns[:len(contract.GATEWAY_CONFIG_COLUMNS)] == contract.GATEWAY_CONFIG_COLUMNS
    assert gw._COLUMN_SETS[0][1][-2:] == ("supports_temperature", "stage_params")
