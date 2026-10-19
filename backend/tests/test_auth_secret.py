"""AA-878: the edge key comes from Secrets Manager on Lambda and the gate fails closed.

No AWS call is made: ``auth._fetch_secret`` is replaced in every test that reaches it.
"""
from __future__ import annotations

import json

import pytest

from backend import config
from backend.shared import auth

KEY = "k" * 48


def _event(headers: dict | None = None) -> dict:
    return {"headers": headers or {}}


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    auth.reset_cache()
    monkeypatch.setattr(config, "TRIPPLANNER_API_KEY", "")
    monkeypatch.setattr(config, "ENVIRONMENT", "dev")
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    yield
    auth.reset_cache()


@pytest.fixture
def on_lambda(monkeypatch):
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "aa-tripplanner-dev-browse")


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(auth.time, "monotonic", c)
    return c


class _Fetch:
    """Stands in for Secrets Manager; records the names it was asked for."""

    def __init__(self, value=KEY, error: Exception | None = None) -> None:
        self.value = value
        self.error = error
        self.names: list[str] = []

    def __call__(self, name: str):
        self.names.append(name)
        if self.error is not None:
            raise self.error
        return self.value


def _use(monkeypatch, fetch: _Fetch) -> _Fetch:
    monkeypatch.setattr(auth, "_fetch_secret", fetch)
    return fetch


# --- env key wins -------------------------------------------------------------------------

def test_env_key_wins_on_lambda_without_reading_the_secret(monkeypatch, on_lambda):
    monkeypatch.setattr(config, "TRIPPLANNER_API_KEY", "envkey")
    fetch = _use(monkeypatch, _Fetch())
    assert auth.check_event(_event({"x-tripplanner-key": "envkey"})) is None
    assert auth.check_event(_event({"x-tripplanner-key": KEY}))["statusCode"] == 401
    assert fetch.names == []


def test_env_placeholder_is_refused(monkeypatch, on_lambda):
    monkeypatch.setattr(config, "TRIPPLANNER_API_KEY", "REPLACE_ME")
    resp = auth.check_event(_event({"x-tripplanner-key": "REPLACE_ME"}))
    assert resp["statusCode"] == 503


# --- local: gate disabled (unchanged behaviour) -------------------------------------------

def test_local_without_key_disables_the_gate(monkeypatch):
    fetch = _use(monkeypatch, _Fetch())
    assert auth.check_event(_event({})) is None
    assert fetch.names == []


# --- Lambda: key from Secrets Manager ------------------------------------------------------

def test_lambda_reads_secret_by_name_derived_from_environment(monkeypatch, on_lambda):
    fetch = _use(monkeypatch, _Fetch())
    assert auth.check_event(_event({"x-tripplanner-key": KEY})) is None
    assert fetch.names == ["tripplanner/dev/api-key"]


def test_lambda_wrong_or_missing_key_is_401(monkeypatch, on_lambda):
    _use(monkeypatch, _Fetch())
    assert auth.check_event(_event({"x-tripplanner-key": "nope"}))["statusCode"] == 401
    assert auth.check_event(_event({}))["statusCode"] == 401


def test_lambda_secret_value_is_stripped(monkeypatch, on_lambda):
    _use(monkeypatch, _Fetch(value=f"  {KEY}\n"))
    assert auth.check_event(_event({"x-tripplanner-key": KEY})) is None


def test_lambda_read_error_is_503_and_logged_without_value(monkeypatch, on_lambda, capsys):
    _use(monkeypatch, _Fetch(error=RuntimeError("AccessDenied")))
    resp = auth.check_event(_event({"x-tripplanner-key": KEY}))
    assert resp["statusCode"] == 503
    assert json.loads(resp["body"]) == {"error": "api key unavailable"}
    out = capsys.readouterr().out
    assert "api_key_unavailable" in out
    assert "read_failed:RuntimeError" in out
    assert KEY not in out


@pytest.mark.parametrize("value", ["", "   ", None, "REPLACE_ME"])
def test_lambda_empty_or_placeholder_secret_is_503(monkeypatch, on_lambda, value):
    _use(monkeypatch, _Fetch(value=value))
    resp = auth.check_event(_event({"x-tripplanner-key": "REPLACE_ME"}))
    assert resp["statusCode"] == 503


def test_lambda_without_environment_is_503(monkeypatch, on_lambda):
    monkeypatch.setattr(config, "ENVIRONMENT", "")
    fetch = _use(monkeypatch, _Fetch())
    assert auth.check_event(_event({"x-tripplanner-key": KEY}))["statusCode"] == 503
    assert fetch.names == []


# --- cache --------------------------------------------------------------------------------

def test_cache_reuses_the_key_within_ttl_and_refreshes_after(monkeypatch, on_lambda, clock):
    fetch = _use(monkeypatch, _Fetch())
    assert auth.check_event(_event({"x-tripplanner-key": KEY})) is None
    clock.now += config.API_KEY_CACHE_SECONDS - 1
    assert auth.check_event(_event({"x-tripplanner-key": KEY})) is None
    assert len(fetch.names) == 1

    # Rotation: after the TTL the new value is read and the old one stops working.
    fetch.value = "n" * 48
    clock.now += 2
    assert auth.check_event(_event({"x-tripplanner-key": "n" * 48})) is None
    assert auth.check_event(_event({"x-tripplanner-key": KEY}))["statusCode"] == 401
    assert len(fetch.names) == 2


def test_failed_refresh_keeps_the_last_good_key(monkeypatch, on_lambda, clock):
    fetch = _use(monkeypatch, _Fetch())
    assert auth.check_event(_event({"x-tripplanner-key": KEY})) is None
    clock.now += config.API_KEY_CACHE_SECONDS + 1
    fetch.error = RuntimeError("throttled")
    assert auth.check_event(_event({"x-tripplanner-key": KEY})) is None
    assert auth.check_event(_event({"x-tripplanner-key": "nope"}))["statusCode"] == 401


def test_failed_read_is_not_retried_on_every_request(monkeypatch, on_lambda, clock):
    fetch = _use(monkeypatch, _Fetch(error=RuntimeError("down")))
    assert auth.check_event(_event({"x-tripplanner-key": KEY}))["statusCode"] == 503
    assert auth.check_event(_event({"x-tripplanner-key": KEY}))["statusCode"] == 503
    assert len(fetch.names) == 1

    # After the retry window the next request tries again and recovers.
    fetch.error = None
    clock.now += config.API_KEY_RETRY_AFTER_SECONDS + 0.1
    assert auth.check_event(_event({"x-tripplanner-key": KEY})) is None
    assert len(fetch.names) == 2
