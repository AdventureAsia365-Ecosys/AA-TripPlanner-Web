"""AA-1092 (S278 audit M19) — the dedicated tenant-assertion key, offline.

The key material is the secret tripplanner/<ENVIRONMENT>/tenant-assertion-key, no longer the edge key. Pinned here:
an assertion signed with the current key is accepted; one forged with any other key, or signed with the edge-derived
key once the secret holds a value, is refused 401; the previous key only inside its grace window, the edge-derived key
only inside the cut-over window (or while the secret is still a shell); a grace longer than the maximum is ignored;
an expired assertion is refused whatever key signed it; a malformed secret or a short key fails closed (503); a read
error keeps the cached keys; the secret is read once per cache window; no key material or header value is logged.
The cross-language vector with the dedicated key is pinned by frontend/lib/tenantAssertion.test.ts too.
"""
from __future__ import annotations

import json

import pytest

from backend import config
from backend.assembly import handler
from backend.shared import auth, tenant_assertion as ta

TENANT_A = "4b1d7e57-0000-4000-8000-0000000000a1"
SLUG = "quillmere-voyages"
NOW = 1762400000
# Built at run time (gitleaks: no literal that looks like a credential). The BFF test builds the same values.
EDGE = "-".join(["aa1038", "vector", "edge"])
CURRENT = "-".join(["aa1092", "vector", "dedicated", "assertion", "key", "material"])
PREVIOUS = "-".join(["aa1092", "vector", "previous", "assertion", "key", "material"])
OTHER = "-".join(["aa1092", "someone", "else", "entirely", "made", "up", "material"])
# The cross-language vector: the same claims as the AA-1038 vector, signed with derive_key(CURRENT).
VECTOR_HEADER = (
    "v1.eyJleHAiOjE3NjI0MDAxMjAsImlhdCI6MTc2MjQwMDAwMCwic2x1ZyI6InF1aWxsbWVyZS12b3lhZ2VzIiwidGlkIjoiNGIxZDdlNTct"
    "MDAwMC00MDAwLTgwMDAtMDAwMDAwMDAwMGExIn0.i7y_-KKqk8Lg_ud0BU1z94x1kcy0_TbUGwM1OsUt1RM"
)


def _sign(material: str, *, now: int = NOW) -> str:
    return ta.sign(ta.derive_key(material), TENANT_A, SLUG, now=now)


def _lines(capsys) -> list[dict]:
    return [json.loads(ln) for ln in capsys.readouterr().out.splitlines() if ln.startswith("{")]


@pytest.fixture
def on_lambda(monkeypatch):
    """The assembly Lambda: edge key from Secrets Manager (auth.py), the dedicated keyset from its own secret."""
    monkeypatch.setattr(config, "TRIPPLANNER_API_KEY", "")
    monkeypatch.setattr(config, "ENVIRONMENT", "test")
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "aa-tripplanner-test-assembly")
    monkeypatch.setattr(auth, "_fetch_secret", lambda name: EDGE)
    auth.reset_cache()
    state = {"value": None, "reads": []}

    def fetch(name):
        state["reads"].append(name)
        if isinstance(state["value"], Exception):
            raise state["value"]
        return state["value"]

    monkeypatch.setattr(ta, "_fetch_secret", fetch)
    ta.reset_cache()
    yield state
    auth.reset_cache()
    ta.reset_cache()


def _keyset(**fields) -> str:
    return json.dumps(fields)


def _answer(value: str) -> dict:
    return handler.tenant_assertion_answer({config.TENANT_ASSERTION_HEADER: value})


# --- the vector and the derivation -----------------------------------------------------------------------------------

def test_the_dedicated_vector_matches_the_bff():
    # Same claims as the AA-1038 vector: only the signature differs (another key material).
    assert _sign(CURRENT) == VECTOR_HEADER
    assert ta.verify(VECTOR_HEADER, ta.derive_key(CURRENT), now=NOW).tenant_id == TENANT_A
    with pytest.raises(ta.AssertionRefused):
        ta.verify(VECTOR_HEADER, ta.derive_key(EDGE), now=NOW)


def test_the_dedicated_key_is_not_the_edge_derived_key():
    assert ta.derive_key(CURRENT) != ta.derive_key(EDGE)
    assert ta.derive_key(CURRENT) != CURRENT.encode()


# --- parse_keyset ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("raw", [None, "", "  \n", "REPLACE_ME", _keyset(current="REPLACE_ME"), _keyset(current="")])
def test_a_shell_or_placeholder_is_not_provisioned(raw):
    assert ta.parse_keyset(raw) == ta.KeySet()


@pytest.mark.parametrize("raw", [
    "not json",
    json.dumps(["a", "list"]),
    _keyset(current=CURRENT, surprise=1),
    _keyset(current="too-short"),
    _keyset(current=12345678901234567890123456789012345),
    _keyset(current=CURRENT, previous="short"),
    _keyset(current=CURRENT, previous=PREVIOUS, previous_until="tomorrow"),
    _keyset(current=CURRENT, edge_derived_until=True),
])
def test_a_malformed_secret_fails_closed(raw):
    with pytest.raises(ta.AssertionKeyUnavailable):
        ta.parse_keyset(raw)


def test_keys_for_orders_current_first_and_honours_each_window():
    ks = ta.KeySet(current=CURRENT, previous=PREVIOUS, previous_until=NOW + 60, edge_derived_until=NOW + 60)
    assert [k.label for k in ta.keys_for(ks, EDGE, now=NOW)] == ["current", "previous", "edge_derived"]
    assert [k.label for k in ta.keys_for(ks, EDGE, now=NOW + 60)] == ["current"]
    assert [k.label for k in ta.keys_for(ta.KeySet(), EDGE, now=NOW)] == ["edge_derived"]
    assert ta.keys_for(ta.KeySet(), None, now=NOW) == ()


def test_a_grace_longer_than_the_maximum_is_ignored(capsys):
    far = NOW + config.TENANT_ASSERTION_MAX_GRACE_S + 1
    ks = ta.KeySet(current=CURRENT, previous=PREVIOUS, previous_until=far, edge_derived_until=far)
    assert [k.label for k in ta.keys_for(ks, EDGE, now=NOW)] == ["current"]
    assert {ln.get("key") for ln in _lines(capsys) if ln["evt"] == "tenant_assertion_grace_ignored"} == {
        "previous", "edge_derived"}


def test_key_material_never_shows_in_a_repr():
    ks = ta.KeySet(current=CURRENT, previous=PREVIOUS)
    assert CURRENT not in repr(ks) and PREVIOUS not in repr(ks)
    assert CURRENT not in repr(ta.keys_for(ks, EDGE, now=NOW))


# --- the verify path on the Lambda -------------------------------------------------------------------------------------

def test_current_key_is_accepted_and_binds_the_tenant(on_lambda, capsys):
    on_lambda["value"] = _keyset(current=CURRENT)
    got = _answer(_sign(CURRENT, now=_now()))
    assert got["tenant"]["tenant_id"] == TENANT_A
    assert not [ln for ln in _lines(capsys) if ln["evt"] == "tenant_assertion_key_used"]


def test_a_forged_assertion_is_refused_401(on_lambda, capsys):
    on_lambda["value"] = _keyset(current=CURRENT)
    value = _sign(OTHER, now=_now())
    got = _answer(value)
    assert got["response"]["statusCode"] == 401
    out = capsys.readouterr().out
    assert value not in out and CURRENT not in out and OTHER not in out
    assert '"reason": "signature"' in out


def test_the_edge_derived_key_is_refused_once_the_secret_holds_a_value(on_lambda):
    on_lambda["value"] = _keyset(current=CURRENT)
    assert _answer(_sign(EDGE, now=_now()))["response"]["statusCode"] == 401


def test_the_edge_derived_key_is_accepted_inside_the_cut_over_window_and_logged(on_lambda, capsys):
    on_lambda["value"] = _keyset(current=CURRENT, edge_derived_until=_now() + 3600)
    assert _answer(_sign(EDGE, now=_now()))["tenant"]["tenant_id"] == TENANT_A
    assert {"evt": "tenant_assertion_key_used", "key": "edge_derived"}.items() <= next(
        ln for ln in _lines(capsys) if ln["evt"] == "tenant_assertion_key_used").items()


def test_the_edge_derived_key_is_refused_after_the_cut_over_window(on_lambda):
    on_lambda["value"] = _keyset(current=CURRENT, edge_derived_until=_now() - 1)
    assert _answer(_sign(EDGE, now=_now()))["response"]["statusCode"] == 401


def test_a_shell_secret_keeps_the_edge_derived_key(on_lambda, capsys):
    on_lambda["value"] = None
    assert _answer(_sign(EDGE, now=_now()))["tenant"]["tenant_id"] == TENANT_A
    assert any(ln.get("key") == "edge_derived" for ln in _lines(capsys) if ln["evt"] == "tenant_assertion_key_used")


def test_the_old_key_inside_its_grace_is_accepted(on_lambda, capsys):
    on_lambda["value"] = _keyset(current=CURRENT, previous=PREVIOUS, previous_until=_now() + 600)
    assert _answer(_sign(PREVIOUS, now=_now()))["tenant"]["tenant_id"] == TENANT_A
    assert any(ln.get("key") == "previous" for ln in _lines(capsys) if ln["evt"] == "tenant_assertion_key_used")


def test_the_old_key_after_its_grace_is_refused_401(on_lambda):
    on_lambda["value"] = _keyset(current=CURRENT, previous=PREVIOUS, previous_until=_now() - 1)
    assert _answer(_sign(PREVIOUS, now=_now()))["response"]["statusCode"] == 401


def test_the_old_key_without_a_grace_is_refused_401(on_lambda):
    on_lambda["value"] = _keyset(current=CURRENT, previous=PREVIOUS)
    assert _answer(_sign(PREVIOUS, now=_now()))["response"]["statusCode"] == 401


def test_an_expired_assertion_is_refused_whatever_key_signed_it(on_lambda, capsys):
    on_lambda["value"] = _keyset(current=CURRENT, previous=PREVIOUS, previous_until=_now() + 600)
    for material in (CURRENT, PREVIOUS):
        assert _answer(_sign(material, now=_now() - 600))["response"]["statusCode"] == 401
    assert {ln.get("reason") for ln in _lines(capsys) if ln["evt"] == "tenant_assertion_refused"} == {"expired"}


@pytest.mark.parametrize("raw", ["not json", _keyset(current="too-short"), _keyset(current=CURRENT, extra=1)])
def test_a_malformed_secret_answers_503(on_lambda, raw):
    on_lambda["value"] = raw
    assert _answer(_sign(CURRENT, now=_now()))["response"]["statusCode"] == 503


def test_a_read_error_without_a_cached_value_answers_503(on_lambda):
    on_lambda["value"] = RuntimeError("AccessDenied")
    assert _answer(_sign(CURRENT, now=_now()))["response"]["statusCode"] == 503


def test_a_read_error_after_a_good_read_keeps_the_cached_keys(on_lambda, monkeypatch):
    on_lambda["value"] = _keyset(current=CURRENT)
    assert "tenant" in _answer(_sign(CURRENT, now=_now()))
    monkeypatch.setattr(config, "API_KEY_CACHE_SECONDS", 0)   # the cache window is over
    on_lambda["value"] = RuntimeError("Throttling")
    assert "tenant" in _answer(_sign(CURRENT, now=_now()))


def test_the_secret_is_read_once_per_cache_window(on_lambda):
    on_lambda["value"] = _keyset(current=CURRENT)
    for _ in range(3):
        assert "tenant" in _answer(_sign(CURRENT, now=_now()))
    assert on_lambda["reads"] == ["tripplanner/test/tenant-assertion-key"]


def test_no_header_never_reads_the_secret(on_lambda):
    on_lambda["value"] = RuntimeError("must not be read")
    assert _answer("") == {"tenant": {}}
    assert on_lambda["reads"] == []


# --- local / env ------------------------------------------------------------------------------------------------------

def test_an_env_key_wins_and_refuses_the_edge_derived_key(monkeypatch):
    monkeypatch.setattr(config, "TRIPPLANNER_API_KEY", EDGE)
    monkeypatch.setattr(config, "TENANT_ASSERTION_KEY", CURRENT + "\n")
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    assert _answer(_sign(CURRENT, now=_now()))["tenant"]["tenant_id"] == TENANT_A
    assert _answer(_sign(EDGE, now=_now()))["response"]["statusCode"] == 401


def test_a_short_env_key_answers_503(monkeypatch):
    monkeypatch.setattr(config, "TENANT_ASSERTION_KEY", "short")
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    assert _answer(_sign(CURRENT, now=_now()))["response"]["statusCode"] == 503


def _now() -> int:
    import time

    return int(time.time())
