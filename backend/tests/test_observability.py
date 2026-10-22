"""AA-871: request ids, JSON log lines, PII scrubbing and Sentry for the two Lambdas."""
from __future__ import annotations

import io
import json
import logging
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import structlog

from backend.assembly import handler as assembly_handler
from backend.browse import handler as browse_handler
from backend.shared import observability as obs
from backend.shared.scrub import FILTERED, scrub_text

CTX = SimpleNamespace(aws_request_id="lambda-req-1")


def _event(path="/browse/countries", method="GET", headers=None, request_id="apigw-req-1"):
    return {
        "headers": headers or {},
        "requestContext": {"requestId": request_id, "http": {"method": method, "path": path}},
        "rawPath": path,
    }


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    monkeypatch.delenv("SENTRY_REQUIRED", raising=False)
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    monkeypatch.setitem(obs._state, "configured", True)  # skip root-logger set-up unless a test asks for it
    monkeypatch.setitem(obs._state, "sentry", False)
    yield
    structlog.contextvars.clear_contextvars()


def _lines(out: str) -> list[dict]:
    return [json.loads(x) for x in out.splitlines() if x.startswith("{")]


# --- request id ---------------------------------------------------------------------------------------------------


def test_request_id_prefers_the_bff_header_then_api_gateway_then_lambda():
    assert obs.request_id_for(_event(headers={"X-Request-ID": "bff-1"}), CTX) == "bff-1"
    assert obs.request_id_for(_event(), CTX) == "apigw-req-1"
    assert obs.request_id_for({"requestContext": {}}, CTX) == "lambda-req-1"
    assert len(obs.request_id_for({}, None)) == 32


@pytest.mark.parametrize("bad", ["", "x" * 129, "a b", "id\r\nx: y", "<s>"])
def test_malformed_header_is_ignored(bad):
    assert obs.request_id_for(_event(headers={"x-request-id": bad}), CTX) == "apigw-req-1"


def test_wrapper_echoes_the_id_and_binds_it_to_every_line(capsys):
    def fake(event, context):
        obs.emit("guest_auth", outcome="ok", route="add")
        return {"statusCode": 200, "headers": {"content-type": "application/json"}, "body": "{}"}

    wrapped = obs.instrument(fake, "assembly", assembly_handler._route_name)
    resp = wrapped(_event("/trip/3f1c2a9e-8f0b-4c1d-9e2a-0b1c2d3e4f50/components", "POST",
                          headers={"x-request-id": "bff-7"}), CTX)
    assert resp["headers"]["x-request-id"] == "bff-7"
    assert resp["headers"]["content-type"] == "application/json"
    lines = _lines(capsys.readouterr().out)
    assert [x["evt"] for x in lines] == ["guest_auth", "request_completed"]
    assert all(x["request_id"] == "bff-7" and x["aws_request_id"] == "lambda-req-1" for x in lines)
    done = lines[-1]
    assert done["route"] == "add" and done["status"] == 200 and done["method"] == "POST"
    assert "3f1c2a9e" not in json.dumps(lines)  # never the path: it carries the trip id
    assert structlog.contextvars.get_contextvars() == {}


def test_shared_headers_dict_is_not_mutated():
    shared = {"content-type": "application/json"}
    out = obs.with_request_id({"statusCode": 200, "headers": shared}, "r1")
    assert out["headers"]["x-request-id"] == "r1" and "x-request-id" not in shared
    assert obs.with_request_id({"ok": True}, "r1") == {"ok": True}  # direct invoke result untouched


def test_unhandled_error_is_logged_captured_and_reraised(capsys):
    def boom(event, context):
        raise RuntimeError("x")

    wrapped = obs.instrument(boom, "browse", browse_handler.route_name)
    with patch.object(obs, "capture") as capture, pytest.raises(RuntimeError):
        wrapped(_event("/browse/search"), CTX)
    capture.assert_called_once()
    done = _lines(capsys.readouterr().out)[-1]
    assert done == {**done, "evt": "request_completed", "route": "search", "status": 500, "error": "RuntimeError"}


def test_direct_sentry_test_invoke_raises_and_is_captured(capsys):
    called = []
    wrapped = obs.instrument(lambda e, c: called.append(1), "browse", browse_handler.route_name)
    with patch.object(obs, "capture") as capture, pytest.raises(obs.SentryTestError):
        wrapped({"sentry_test": True}, CTX)
    assert called == [] and capture.call_count == 1
    done = _lines(capsys.readouterr().out)[-1]
    assert done["route"] == "direct_invoke" and done["error"] == "SentryTestError"
    # over HTTP (requestContext present) the key is just ignored
    wrapped2 = obs.instrument(lambda e, c: {"statusCode": 200}, "browse", browse_handler.route_name)
    assert wrapped2({**_event(), "sentry_test": True}, CTX)["statusCode"] == 200


def test_malformed_events_do_not_break_the_wrapper():
    wrapped = obs.instrument(lambda e, c: {"statusCode": 200}, "browse", browse_handler.route_name)
    for ev in ({"requestContext": "x", "headers": "y"}, {"headers": None}, {}):
        assert wrapped(ev, CTX)["statusCode"] == 200


def test_browse_route_names():
    assert browse_handler.route_name("GET", "/browse/tiles/10_100_5") == "tiles"
    assert browse_handler.route_name("GET", "/browse/catalog/tours/some-slug") == "catalog_tour"
    assert browse_handler.route_name("GET", "/nope") == "unknown"


def test_emit_without_a_request_keeps_the_old_line_shape(capsys):
    obs.emit("guest_auth", outcome="ok", route="add")
    assert capsys.readouterr().out == '{"evt": "guest_auth", "outcome": "ok", "route": "add"}\n'


def test_emit_scrubs(capsys):
    obs.emit("llm_gateway_note", detail="write failed: Key (email)=(a@b.example) for +84 90 123 4567",
             email="a@b.example")
    line = _lines(capsys.readouterr().out)[0]
    assert "a@b.example" not in json.dumps(line) and "123 4567" not in json.dumps(line)
    assert line["email"] == FILTERED


# --- stdlib logging as JSON ---------------------------------------------------------------------------------------


def test_stdlib_lines_are_json_with_the_request_id():
    root = logging.getLogger()
    saved = list(root.handlers), root.level
    buf = io.StringIO()
    try:
        obs.configure_logging(stream=buf)
        with structlog.contextvars.bound_contextvars(request_id="req-9"):
            logging.getLogger("backend.assembly.notify").info("hand-off for %s logged", "trip1")
            logging.getLogger("x").warning("mail %s", "jane@example.com")
    finally:
        for h in list(root.handlers):
            if h.get_name() == obs.HANDLER_NAME:
                root.removeHandler(h)
        for h in saved[0]:
            if h not in root.handlers:
                root.addHandler(h)
        root.setLevel(saved[1])
        structlog.reset_defaults()
    lines = _lines(buf.getvalue())
    assert lines[0]["event"] == "hand-off for trip1 logged" and lines[0]["request_id"] == "req-9"
    assert lines[0]["logger"] == "backend.assembly.notify" and lines[0]["level"] == "info"
    assert lines[1]["event"] == "mail [email]"


# --- Sentry -------------------------------------------------------------------------------------------------------


def test_sentry_off_without_dsn(capsys):
    with patch("sentry_sdk.init") as init:
        assert obs.init_sentry("browse") is False
    init.assert_not_called()
    assert _lines(capsys.readouterr().out)[0]["evt"] == "sentry_disabled"


def test_missing_dsn_guard(monkeypatch):
    monkeypatch.setenv("SENTRY_REQUIRED", "true")
    with pytest.raises(obs.SentryConfigError, match="SENTRY_DSN is empty"):
        obs.init_sentry("browse")


def test_sentry_init_options(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "https://k@o1.ingest.example/1")
    monkeypatch.setenv("GIT_SHA", "deadbeef")
    monkeypatch.setenv("ENVIRONMENT", "dev")
    with patch("sentry_sdk.init") as init, patch("sentry_sdk.set_tag"):
        assert obs.init_sentry("assembly") is True
    kw = init.call_args.kwargs
    assert kw["release"] == "deadbeef" and kw["environment"] == "dev" and kw["server_name"] == "assembly"
    assert kw["send_default_pii"] is False and kw["include_local_variables"] is False
    assert kw["traces_sample_rate"] == 0.0 and kw["auto_enabling_integrations"] is False
    assert kw["before_send"] is obs.before_send


def _on_lambda(monkeypatch, environment="dev"):
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "aa-tripplanner-dev-browse")
    monkeypatch.setenv("ENVIRONMENT", environment)


def test_dsn_from_secret_on_lambda(monkeypatch, capsys):
    """AA-871: no SENTRY_DSN env on Lambda -> the secret tripplanner/<ENVIRONMENT>/sentry-dsn, read by name."""
    _on_lambda(monkeypatch)
    with patch.object(obs, "_fetch_secret", return_value=" https://k@o1.ingest.example/2\n") as fetch, \
            patch("sentry_sdk.init") as init, patch("sentry_sdk.set_tag"):
        assert obs.init_sentry("browse") is True
    fetch.assert_called_once_with("tripplanner/dev/sentry-dsn")
    assert init.call_args.kwargs["dsn"] == "https://k@o1.ingest.example/2"
    out = capsys.readouterr().out
    assert "ingest.example" not in out  # the DSN is never logged
    assert _lines(out)[0]["evt"] == "sentry_enabled"


def test_env_dsn_wins_over_the_secret(monkeypatch):
    _on_lambda(monkeypatch)
    monkeypatch.setenv("SENTRY_DSN", "https://k@o1.ingest.example/1")
    with patch.object(obs, "_fetch_secret") as fetch, patch("sentry_sdk.init"), patch("sentry_sdk.set_tag"):
        assert obs.init_sentry("browse") is True
    fetch.assert_not_called()


def test_no_secret_read_off_lambda(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "dev")
    with patch.object(obs, "_fetch_secret") as fetch:
        assert obs.resolve_dsn() == ("", "SENTRY_DSN not set")
    fetch.assert_not_called()


@pytest.mark.parametrize("fetched, reason", [
    (RuntimeError("ResourceNotFoundException"), "secret_read_failed:RuntimeError"),
    ("   ", "secret_empty"),
    (None, "secret_empty"),
])
def test_secret_unavailable_leaves_sentry_off(monkeypatch, capsys, fetched, reason):
    _on_lambda(monkeypatch)
    kwargs = {"side_effect": fetched} if isinstance(fetched, Exception) else {"return_value": fetched}
    with patch.object(obs, "_fetch_secret", **kwargs), patch("sentry_sdk.init") as init:
        assert obs.init_sentry("assembly") is False
    init.assert_not_called()
    line = _lines(capsys.readouterr().out)[0]
    assert line["evt"] == "sentry_disabled" and line["reason"] == reason


def test_secret_unavailable_with_guard_raises(monkeypatch):
    _on_lambda(monkeypatch)
    monkeypatch.setenv("SENTRY_REQUIRED", "true")
    with patch.object(obs, "_fetch_secret", side_effect=RuntimeError("x")):
        with pytest.raises(obs.SentryConfigError, match="secret_read_failed"):
            obs.init_sentry("browse")


def test_no_environment_on_lambda_reads_nothing(monkeypatch):
    _on_lambda(monkeypatch, environment="")
    with patch.object(obs, "_fetch_secret") as fetch:
        assert obs.resolve_dsn() == ("", "ENVIRONMENT not set")
    fetch.assert_not_called()


def test_git_sha_from_the_packaged_file(monkeypatch, tmp_path):
    monkeypatch.delenv("GIT_SHA", raising=False)
    f = tmp_path / "GIT_SHA"
    f.write_text("abc123\n")
    monkeypatch.setattr(obs, "_GIT_SHA_FILE", f)
    assert obs.git_sha() == "abc123"
    monkeypatch.setattr(obs, "_GIT_SHA_FILE", tmp_path / "missing")
    assert obs.git_sha() == "unknown"


def test_before_send_strips_pii():
    event = {
        "request": {"url": "https://x/trip?token=abc", "data": {"customer": {"email": "a@b.example"}},
                    "headers": {"x-tripplanner-key": "K", "x-tripplanner-guest": "G", "x-request-id": "r1"},
                    "query_string": "q=jane"},
        "user": {"ip_address": "1.2.3.4"},
        "exception": {"values": [
            {"type": "DataError", "module": "asyncpg.exceptions", "value": "invalid input: 'Jane Doe'"},
            {"type": "ValueError", "module": "builtins", "value": "mail a@b.example",
             "stacktrace": {"frames": [{"vars": {"customer": {"name": "Jane"}}, "context_line": "x = 'a@b.example'"}]}},
        ]},
        "tags": {"request_id": "r1"},
        "breadcrumbs": {"values": [{"message": "mail a@b.example"}]},
    }
    out = obs.before_send(event, {})
    text = json.dumps(out)
    assert "Jane" not in text and "a@b.example" not in text and '"K"' not in text and '"G"' not in text
    assert out["exception"]["values"][0]["value"] == FILTERED
    assert out["request"]["headers"] == {"x-request-id": "r1"}
    assert "user" not in out and out["tags"]["request_id"] == "r1"


def test_real_sdk_event_carries_request_id_and_no_pii(monkeypatch):
    import sentry_sdk
    import sentry_sdk.transport

    class Capture(sentry_sdk.transport.Transport):
        def __init__(self, options=None):
            super().__init__(options)
            self.events = []

        def capture_envelope(self, envelope):
            self.events.extend(i.payload.json for i in envelope.items if i.type == "event")

    transport = Capture()
    monkeypatch.setenv("SENTRY_DSN", "https://k@o1.ingest.example/1")
    real_init = sentry_sdk.init
    with patch("sentry_sdk.init", lambda **kw: real_init(**kw, transport=transport)):
        monkeypatch.setitem(obs._state, "sentry", obs.init_sentry("assembly"))

    def boom(event, context):
        raise ValueError("customer jane@example.com")

    try:
        with pytest.raises(ValueError):
            obs.instrument(boom, "assembly", assembly_handler._route_name)(
                _event("/trip/x/send-to-advisor", "POST", headers={"x-request-id": "bff-s"}), CTX)
    finally:
        sentry_sdk.get_client().close()
        real_init()
    assert transport.events, "no event captured"
    ev = transport.events[0]
    assert ev["tags"]["request_id"] == "bff-s"
    assert "jane@example.com" not in json.dumps(ev)


def test_scrub_copy_matches_booking_rules():
    assert scrub_text("Bearer abcdefghijkl and eyJhbGciOi.eyJzdWIiOi.sig") == "Bearer [Filtered] and [jwt]"
