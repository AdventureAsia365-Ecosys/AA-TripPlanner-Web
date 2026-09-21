"""Unit tests for the Bedrock satellite — fully stubbed, no live AWS."""
from __future__ import annotations

import io
import json

import pytest

from backend import config
from backend.shared import bedrock_satellite as bs


@pytest.fixture(autouse=True)
def _role_configured(monkeypatch):
    # Ensure per-account role names exist so _role_arn doesn't raise. The two
    # satellite accounts use DIFFERENT role names in production (acc3
    # AA3-Bedrock-Invoker / acc1 AA-Bedrock-Invoker); mirror that here.
    monkeypatch.setattr(config, "BEDROCK_ROLE_NAME", "")
    monkeypatch.setattr(config, "BEDROCK_ROLE_NAME_PRIMARY", "PrimaryBedrockRole")
    monkeypatch.setattr(config, "BEDROCK_ROLE_NAME_FALLBACK", "FallbackBedrockRole")
    monkeypatch.setattr(config, "BEDROCK_ACCT_PRIMARY", "111111111111")
    monkeypatch.setattr(config, "BEDROCK_ACCT_FALLBACK", "222222222222")


# --- Stub building blocks ---------------------------------------------------

class StubSts:
    def __init__(self, account_seen: list, arns_seen: list | None = None):
        self._seen = account_seen
        self._arns = arns_seen if arns_seen is not None else []

    def assume_role(self, *, RoleArn, RoleSessionName, ExternalId=None):
        # Record which account's role was assumed, and the full ARN.
        acct = RoleArn.split(":")[4]
        self._seen.append(acct)
        self._arns.append(RoleArn)
        return {
            "Credentials": {
                "AccessKeyId": f"AKIA-{acct}",
                "SecretAccessKey": "secret",
                "SessionToken": "token",
            }
        }


class _Body:
    def __init__(self, payload: dict):
        self._raw = json.dumps(payload).encode()

    def read(self):
        return self._raw


class StubBedrock:
    def __init__(self, *, fail: bool = False, response: dict | None = None,
                 stream_chunks: list[dict] | None = None):
        self._fail = fail
        self._response = response or {"ok": True}
        self._stream_chunks = stream_chunks or []

    def invoke_model(self, *, modelId, body, **kwargs):
        if self._fail:
            raise RuntimeError("simulated primary failure")
        return {"body": _Body(self._response)}

    def invoke_model_with_response_stream(self, *, modelId, body, **kwargs):
        if self._fail:
            raise RuntimeError("simulated primary stream failure")
        events = [
            {"chunk": {"bytes": json.dumps(c).encode()}}
            for c in self._stream_chunks
        ]
        return {"body": iter(events)}


def make_factory(*, primary_bedrock: StubBedrock, fallback_bedrock: StubBedrock,
                 account_seen: list, primary="111111111111",
                 direct_bedrock: StubBedrock | None = None):
    """Return a ClientFactory that routes bedrock clients by which account's
    STS creds were used (encoded in the access key id). A bedrock-runtime
    client requested with NO creds (the direct embed path) returns
    `direct_bedrock`."""

    def factory(service, *, creds=None, region=None):
        if service == "sts":
            return StubSts(account_seen)
        if service == "bedrock-runtime":
            if creds is None:
                assert direct_bedrock is not None, "direct embed call unexpected"
                return direct_bedrock
            acct = creds.access_key_id.split("-")[1]
            return primary_bedrock if acct == primary else fallback_bedrock
        raise AssertionError(f"unexpected service {service}")

    return factory


# --- Tests ------------------------------------------------------------------

def test_invoke_succeeds_on_primary():
    seen: list = []
    factory = make_factory(
        primary_bedrock=StubBedrock(response={"result": 42}),
        fallback_bedrock=StubBedrock(fail=True),
        account_seen=seen,
    )
    out = bs.invoke("model-x", {"prompt": "hi"}, client_factory=factory)
    assert out == {"result": 42}
    # Only the primary account was assumed.
    assert seen == ["111111111111"]


def test_invoke_fails_over_to_fallback():
    seen: list = []
    factory = make_factory(
        primary_bedrock=StubBedrock(fail=True),
        fallback_bedrock=StubBedrock(response={"result": "from-fallback"}),
        account_seen=seen,
    )
    out = bs.invoke("model-x", {"prompt": "hi"}, client_factory=factory)
    assert out == {"result": "from-fallback"}
    # Both accounts were attempted, primary first.
    assert seen == ["111111111111", "222222222222"]


def test_invoke_raises_when_both_fail():
    seen: list = []
    factory = make_factory(
        primary_bedrock=StubBedrock(fail=True),
        fallback_bedrock=StubBedrock(fail=True),
        account_seen=seen,
    )
    with pytest.raises(bs.BedrockError):
        bs.invoke("model-x", {"prompt": "hi"}, client_factory=factory)
    assert seen == ["111111111111", "222222222222"]


def test_invoke_stream_yields_chunks():
    seen: list = []
    chunks = [{"delta": "Hello"}, {"delta": " world"}]
    factory = make_factory(
        primary_bedrock=StubBedrock(stream_chunks=chunks),
        fallback_bedrock=StubBedrock(fail=True),
        account_seen=seen,
    )
    got = list(bs.invoke_stream("model-x", {"prompt": "hi"}, client_factory=factory))
    assert got == chunks


def test_embed_returns_vector_direct_no_assume_role(monkeypatch):
    # embed() is a DIRECT acc2 call (no assume-role) using the Cohere v4
    # response shape {"embeddings": {"float": [[...]]}}. Set EMBED_DIM to
    # match the stub vector length.
    monkeypatch.setattr(config, "EMBED_DIM", 3)
    seen: list = []
    cohere = StubBedrock(response={"embeddings": {"float": [[0.1, 0.2, 0.3]]}})
    factory = make_factory(
        primary_bedrock=StubBedrock(fail=True),
        fallback_bedrock=StubBedrock(fail=True),
        account_seen=seen,
        direct_bedrock=cohere,
    )
    vec = bs.embed("some text", client_factory=factory)
    assert vec == [0.1, 0.2, 0.3]
    # No STS assume-role happened for embeddings.
    assert seen == []


def test_missing_role_raises(monkeypatch):
    # All three role-name sources empty -> _role_arn must raise.
    monkeypatch.setattr(config, "BEDROCK_ROLE_NAME", "")
    monkeypatch.setattr(config, "BEDROCK_ROLE_NAME_PRIMARY", "")
    monkeypatch.setattr(config, "BEDROCK_ROLE_NAME_FALLBACK", "")
    seen: list = []
    factory = make_factory(
        primary_bedrock=StubBedrock(),
        fallback_bedrock=StubBedrock(),
        account_seen=seen,
    )
    with pytest.raises(bs.BedrockError):
        bs.invoke("model-x", {"prompt": "hi"}, client_factory=factory)


def test_failover_uses_per_account_role_names():
    """Regression: acc3 and acc1 have DIFFERENT satellite role names, so the
    assumed-role ARNs must carry the per-account role name, not one shared
    name for both."""
    seen: list = []
    arns: list = []

    def factory(service, *, creds=None, region=None):
        if service == "sts":
            return StubSts(seen, arns)
        if service == "bedrock-runtime":
            acct = creds.access_key_id.split("-")[1]
            # primary fails so we exercise BOTH assume-role calls
            return StubBedrock(fail=True) if acct == "111111111111" else \
                StubBedrock(response={"ok": "fallback"})
        raise AssertionError(f"unexpected service {service}")

    out = bs.invoke("model-x", {"prompt": "hi"}, client_factory=factory)
    assert out == {"ok": "fallback"}
    assert arns == [
        "arn:aws:iam::111111111111:role/PrimaryBedrockRole",
        "arn:aws:iam::222222222222:role/FallbackBedrockRole",
    ]
