"""AA-642 S263 E2: TripPlanner ledger rows carry prompt_version, prompt_hash and request_id. Fully stubbed: no AWS,
no DB, invented model keys and text."""
from __future__ import annotations

import hashlib

import pytest

from backend import config
from backend.shared import bedrock_satellite as bs
from backend.shared import llm_gateway as gw
from backend.shared import prompt_registry
from backend.tests.test_llm_gateway import FakeDb, StubBedrock, _row, make_factory

SYS = "You narrate an invented trip through Zedport."
SYS_HASH = hashlib.sha256(SYS.encode("utf-8")).hexdigest()


@pytest.fixture(autouse=True)
def _gateway_state(monkeypatch):
    monkeypatch.setattr(config, "BEDROCK_ROLE_NAME", "")
    monkeypatch.setattr(config, "BEDROCK_ROLE_NAME_PRIMARY", "PrimaryBedrockRole")
    monkeypatch.setattr(config, "BEDROCK_ACCT_PRIMARY", "111111111111")
    monkeypatch.setattr(config, "EMBED_DIM", 3)
    monkeypatch.setattr(gw, "_spend_unreadable_until", 0.0)
    prompt_registry.invalidate()
    gw._cache.clear()


WRITER_A = gw.Model("zz-writer-a", "anthropic", "anthropic_native", {"acc3": "zz.writer-a"}, 3.0, 15.0)
WRITER_B = gw.Model("zz-writer-b", "anthropic", "converse", {"acc3": "zz.writer-b"}, 2.0, 10.0)


def _native(stop="end_turn"):
    return {"content": [{"type": "text", "text": "Day 1: Lanterns over Qorvale."}], "stop_reason": stop,
            "usage": {"input_tokens": 100, "output_tokens": 20}}


class RequestIdStub(StubBedrock):
    """invoke_model replies carry a Bedrock RequestId; converse raises a ClientError-like error that carries one."""

    def invoke_model(self, **kwargs):
        out = super().invoke_model(**kwargs)
        out["ResponseMetadata"] = {"RequestId": "zz-req-ok", "HTTPHeaders": {}}
        return out

    def converse(self, **kwargs):
        self.calls.append(("converse", kwargs["modelId"], kwargs))
        err = RuntimeError("An error occurred (AccessDeniedException) when calling the Converse operation")
        err.response = {"ResponseMetadata": {"RequestId": "zz-req-denied"}}
        raise err


def test_every_attempt_carries_the_prompt_and_request_ids():
    route = gw.Route("tp_compose", "writer", (WRITER_B, WRITER_A), "acc3")
    failures: list = []
    _, call = gw.generate_text(route, SYS, "user", 50, failures=failures,
                               client_factory=make_factory({"acc3": RequestIdStub(response=_native())}, []))
    (refused,) = failures
    assert (refused.request_id, refused.status) == ("zz-req-denied", "refused")
    assert call.request_id == "zz-req-ok"
    for attempt in (refused, call):
        assert (attempt.prompt_hash, attempt.prompt_version) == (SYS_HASH, "v1")


def test_a_stage_outside_the_manifest_has_a_hash_but_no_version():
    route = gw.Route("tp_extract", "writer", (WRITER_A,), "acc3")
    _, call = gw.generate_text(route, SYS, "user", 50,
                               client_factory=make_factory({"acc3": RequestIdStub(response=_native())}, []))
    assert (call.prompt_hash, call.prompt_version) == (SYS_HASH, None)


def test_truncated_and_deadline_rows_carry_the_prompt_too(monkeypatch):
    clock = [10.0]
    monkeypatch.setattr(gw.time, "monotonic", lambda: clock[0])

    class SlowCut(RequestIdStub):
        def invoke_model(self, **kwargs):
            clock[0] += 19.5
            return super().invoke_model(**kwargs)

    route = gw.Route("tp_compose", "writer", (WRITER_A, WRITER_B), "acc3")
    failures: list = []
    with pytest.raises(gw.RouteDeadlineExceeded):
        gw.generate_text(route, SYS, "user", 50, failures=failures,
                         client_factory=make_factory({"acc3": SlowCut(response=_native(stop="max_tokens"))}, []))
    cut, stopped = failures
    assert (cut.status, cut.request_id) == ("truncated", "zz-req-ok")
    assert (stopped.error_class, stopped.request_id) == ("deadline", None)
    assert {(f.prompt_hash, f.prompt_version) for f in failures} == {(SYS_HASH, "v1")}


async def test_record_writes_the_three_columns():
    db = FakeDb()
    call = gw.Call("tp_compose", "writer", "zz-writer-a", "acc3", 10, 5, 0.0001, False, "end_turn",
                   request_id="zz-req-ok", prompt_hash=SYS_HASH, prompt_version="v1")
    await gw.record(db, call, {"mode": "compose"})
    row = _row(db.executed[0][1])
    assert (row["request_id"], row["prompt_hash"], row["prompt_version"]) == ("zz-req-ok", SYS_HASH, "v1")
    assert row["app"] == "tripplanner"


def test_embed_rows_carry_the_request_id_but_no_prompt():
    acc2 = StubBedrock(response={"embeddings": {"float": [[0.1, 0.2, 0.3]]}})
    original = acc2.invoke_model

    def with_meta(**kwargs):
        out = original(**kwargs)
        out["ResponseMetadata"] = {"RequestId": "zz-req-embed", "HTTPHeaders": {}}
        return out

    acc2.invoke_model = with_meta
    _, call = gw.embed_texts(gw.builtin_route("tp_search_embed"), ["beach town"],
                             client_factory=make_factory({"acc2": acc2}, []))
    assert (call.request_id, call.prompt_hash, call.prompt_version) == ("zz-req-embed", None, None)


@pytest.mark.parametrize("source, expected", [
    ({"ResponseMetadata": {"RequestId": "zz-1"}}, "zz-1"),
    ({"ResponseMetadata": {}}, None),
    ({}, None),
    (bs.BedrockError("no response attribute"), None),
    (None, None),
])
def test_request_id_extraction(source, expected):
    assert gw._request_id(source) == expected
