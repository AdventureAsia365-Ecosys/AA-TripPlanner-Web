"""JSON log lines, request ids and Sentry for the two Lambdas (AA-871).

**Request id.** Each invocation gets one id: the BFF's ``x-request-id`` header when it is well formed (the BFF
forwards the id it echoes to the browser, so one id follows a request from Vercel to the Lambda), else API Gateway's
``requestContext.requestId``, else the Lambda ``aws_request_id``, else a new ``uuid4().hex``. It is bound to
:mod:`structlog.contextvars` for the invocation, returned as ``x-request-id`` on the response and set as the Sentry tag
``request_id``. ``aws_request_id`` is bound too, to join the platform's START / END / REPORT lines.

**Log lines.** Every line is one JSON object:

- :func:`emit` for this repo's event lines (``{"evt": ..., ...}``; the fields come first, then the bound context);
- stdlib ``logging`` calls, rendered by the root handler :func:`configure_logging` installs (structlog
  ``ProcessorFormatter``; ``event`` = the formatted message).

One ``request_completed`` line per invocation: service, method, route **name** (never the path: it carries trip and
session ids, which this repo does not log, AA-827), status, ``duration_ms``. Values pass through
:func:`backend.shared.scrub.scrub`.

**Sentry** starts only when a DSN is found: the ``SENTRY_DSN`` env (local runs, tests) or, on Lambda with no env value,
the Secrets Manager secret ``tripplanner/<ENVIRONMENT>/sentry-dsn`` read by name once per cold start (AA-871; the same
by-name pattern as the edge key in :mod:`backend.shared.auth`, so no DSN sits in the Lambda environment; Infra grants
the read). A missing secret value or a failed read leaves Sentry off (``sentry_disabled`` with the reason) and never
fails a request. Errors only (no tracing),
``send_default_pii=False``, no frame locals, no request data, ``release`` = the git SHA baked into the zip
(``backend/GIT_SHA``, written by ``package_lambdas.sh``) or ``GIT_SHA``, tag ``version`` = the release version.
``SENTRY_REQUIRED=true`` with no DSN makes the first invocation fail (the missing-DSN guard; off by default).

**Release version** (AA-891): every log line carries ``version`` next to ``git_sha``: ``APP_VERSION`` env, else
``backend/VERSION`` (written by ``package_lambdas.sh`` from ``.release-please-manifest.json``), else the manifest itself
in a checkout (local runs, tests), else ``unknown``.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import IO, Any, Optional

import structlog

from backend.shared.scrub import FILTERED, scrub

REQUEST_ID_HEADER = "x-request-id"
_VALID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
HANDLER_NAME = "aa-json"
_GIT_SHA_FILE = Path(__file__).resolve().parent.parent / "GIT_SHA"
_VERSION_FILE = Path(__file__).resolve().parent.parent / "VERSION"
# Only in a checkout (the Lambda zip has backend/ at its root and no manifest): the repo root's release-please manifest.
_MANIFEST_FILE = Path(__file__).resolve().parent.parent.parent / ".release-please-manifest.json"
# Fields this code writes itself; never redacted (an id is not personal data, DM-6.4).
_KEEP = frozenset({
    "evt", "timestamp", "level", "logger", "service", "environment", "git_sha", "version", "request_id",
    "aws_request_id", "method", "route", "status", "duration_ms", "outcome", "reason", "error", "at",
})
# Exceptions whose message echoes input values (asyncpg: bad parameters; pydantic: input_value=...): the message is
# not sent to Sentry at all, only the type and the stack.
_VALUE_ECHOING_MODULES = frozenset({"asyncpg", "pydantic", "pydantic_core", "jsonschema", "json"})
SAFE_HEADERS = frozenset({"accept", "content-type", "content-length", "user-agent", REQUEST_ID_HEADER})

_state: dict[str, Any] = {"configured": False, "sentry": False}


SENTRY_TEST_KEY = "sentry_test"
# AA-871: where the Lambdas find the DSN when the env has none (AA-CIS-Infra tripplanner.tf, grant ReadSentryDsnSecret).
SENTRY_DSN_SECRET_NAME_TEMPLATE = "tripplanner/{environment}/sentry-dsn"


class SentryTestError(RuntimeError):
    """Raised on purpose by a direct ``{"sentry_test": true}`` invoke (live check of the Sentry wiring)."""


class SentryConfigError(RuntimeError):
    """SENTRY_REQUIRED is on but SENTRY_DSN is empty."""


def git_sha() -> str:
    env = (os.environ.get("GIT_SHA") or "").strip()
    if env:
        return env
    try:
        return _GIT_SHA_FILE.read_text().strip() or "unknown"
    except OSError:
        return "unknown"


def release_version() -> str:
    """The release version this build carries (AA-891); see the module docstring for the lookup order."""
    env = (os.environ.get("APP_VERSION") or "").strip()
    if env:
        return env
    try:
        value = _VERSION_FILE.read_text().strip()
        if value:
            return value
    except OSError:
        pass
    try:
        value = json.loads(_MANIFEST_FILE.read_text()).get(".")
    except (OSError, ValueError, AttributeError):
        return "unknown"
    return value.strip() if isinstance(value, str) and value.strip() else "unknown"


def _service() -> str:
    return os.environ.get("AWS_LAMBDA_FUNCTION_NAME") or "aa-tripplanner"


def _environment() -> str:
    return os.environ.get("ENVIRONMENT") or "dev"


# --- request id ---------------------------------------------------------------------------------------------------


def accept_request_id(value: Optional[str]) -> Optional[str]:
    """The caller's id when it is safe to log and echo, else None."""
    if isinstance(value, str) and _VALID.match(value.strip()):
        return value.strip()
    return None


def request_id_for(event: dict, context: Any = None) -> str:
    raw_headers = event.get("headers")
    headers = {str(k).lower(): v for k, v in raw_headers.items()} if isinstance(raw_headers, dict) else {}
    rc = event.get("requestContext")
    return (
        accept_request_id(headers.get(REQUEST_ID_HEADER))
        or accept_request_id(rc.get("requestId") if isinstance(rc, dict) else None)
        or accept_request_id(getattr(context, "aws_request_id", None))
        or uuid.uuid4().hex
    )


def with_request_id(response: Any, request_id: str) -> Any:
    """A copy of a Lambda proxy response with ``x-request-id`` added (anything else is returned unchanged)."""
    if not isinstance(response, dict) or "statusCode" not in response:
        return response
    return {**response, "headers": {**(response.get("headers") or {}), REQUEST_ID_HEADER: request_id}}


# --- log lines ----------------------------------------------------------------------------------------------------


def emit(evt: str, **fields: Any) -> None:
    """One JSON line on stdout: ``{"evt": evt, **fields, **bound context}`` (redacted)."""
    line: dict[str, Any] = {"evt": evt, **fields}
    for k, v in structlog.contextvars.get_contextvars().items():
        line.setdefault(k, v)
    print(json.dumps(scrub(line, keep=_KEEP), default=str), flush=True)


def _scrub_event(_logger: Any, _method: str, event_dict: dict) -> dict:
    if event_dict.get("message") == event_dict.get("event"):
        event_dict.pop("message")
    return scrub(event_dict, keep=_KEEP)


def configure_logging(stream: Optional[IO[str]] = None, level: int = logging.INFO) -> None:
    """Route stdlib logging through one JSON handler on the root logger. Idempotent."""
    static = {
        "service": _service(), "environment": _environment(), "git_sha": git_sha(), "version": release_version(),
    }

    def add_static(_logger: Any, _method: str, event_dict: dict) -> dict:
        for k, v in static.items():
            event_dict.setdefault(k, v)
        return event_dict

    shared = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        add_static,
    ]
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=[*shared, structlog.stdlib.ExtraAdder()],
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            _scrub_event,
            structlog.processors.JSONRenderer(default=str),
        ],
    )
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.set_name(HANDLER_NAME)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    on_lambda = bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))
    for h in list(root.handlers):
        # On Lambda the runtime's own text handler goes (else every line prints twice); elsewhere only ours or a
        # plain StreamHandler, so pytest's capture handlers stay.
        if on_lambda or h.get_name() == HANDLER_NAME or type(h) is logging.StreamHandler:
            root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(level)
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )


# --- Sentry -------------------------------------------------------------------------------------------------------


def _scrub_frames(holder: dict) -> None:
    for frame in (holder.get("stacktrace") or {}).get("frames") or []:
        frame.pop("vars", None)
        for key in ("context_line", "pre_context", "post_context"):
            if key in frame:
                frame[key] = scrub(frame[key])


def before_send(event: dict, hint: Any) -> dict:
    """No request data, user or frame locals; value-echoing exception messages dropped; PII redacted elsewhere."""
    request = event.get("request")
    if isinstance(request, dict):
        for key in ("data", "cookies", "env", "query_string"):
            request.pop(key, None)
        headers = request.get("headers")
        if isinstance(headers, dict):
            request["headers"] = {k: v for k, v in headers.items() if str(k).lower() in SAFE_HEADERS}
        if isinstance(request.get("url"), str):
            request["url"] = scrub(request["url"])
    event.pop("user", None)
    for exc in (event.get("exception") or {}).get("values") or []:
        if str(exc.get("module") or "").split(".")[0] in _VALUE_ECHOING_MODULES:
            exc["value"] = FILTERED
        elif isinstance(exc.get("value"), str):
            exc["value"] = scrub(exc["value"])
        _scrub_frames(exc)
    for thread in (event.get("threads") or {}).get("values") or []:
        _scrub_frames(thread)
    if "message" in event:
        event["message"] = scrub(event["message"])
    for key in ("logentry", "extra", "contexts"):
        if isinstance(event.get(key), dict):
            event[key] = scrub(event[key])
    if isinstance(event.get("tags"), dict):
        event["tags"] = scrub(event["tags"], keep=_KEEP)
    crumbs = event.get("breadcrumbs")
    if isinstance(crumbs, dict) and isinstance(crumbs.get("values"), list):
        crumbs["values"] = [before_breadcrumb(c, None) for c in crumbs["values"] if isinstance(c, dict)]
    return event


def before_breadcrumb(crumb: dict, hint: Any) -> dict:
    for key in ("message", "data"):
        if key in crumb:
            crumb[key] = scrub(crumb[key])
    return crumb


def _flag(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _fetch_secret(name: str) -> Optional[str]:
    """Read one secret string by name. Separate function so tests can replace it."""
    import boto3  # lazy: local runs and tests never reach this

    client = boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION") or "us-west-1")
    return client.get_secret_value(SecretId=name).get("SecretString")


def resolve_dsn() -> tuple[str, str]:
    """``(dsn, reason)``: the ``SENTRY_DSN`` env, else (on Lambda only) the secret named after ``ENVIRONMENT``.
    ``reason`` says why the DSN is empty; the value itself is never logged."""
    dsn = (os.environ.get("SENTRY_DSN") or "").strip()
    if dsn:
        return dsn, "env"
    if not os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        return "", "SENTRY_DSN not set"
    env = (os.environ.get("ENVIRONMENT") or "").strip()
    if not env:
        return "", "ENVIRONMENT not set"
    try:
        value = _fetch_secret(SENTRY_DSN_SECRET_NAME_TEMPLATE.format(environment=env))
    except Exception as exc:  # noqa: BLE001 - no value yet (ResourceNotFoundException), IAM, network: Sentry stays off
        return "", f"secret_read_failed:{type(exc).__name__}"
    value = (value or "").strip()
    return (value, "secrets_manager") if value else ("", "secret_empty")


def init_sentry(service: str) -> bool:
    """Start the SDK when a DSN is found (:func:`resolve_dsn`). Returns whether it started."""
    dsn, reason = resolve_dsn()
    if not dsn:
        if _flag("SENTRY_REQUIRED"):
            raise SentryConfigError(f"SENTRY_REQUIRED is on but SENTRY_DSN is empty ({reason})")
        emit("sentry_disabled", reason=reason)
        return False
    if not dsn.startswith("https://"):
        raise SentryConfigError("SENTRY_DSN must be an https:// URL")  # the value is never echoed
    import sentry_sdk  # only paid for when Sentry is on

    sentry_sdk.init(
        dsn=dsn,
        environment=_environment(),
        release=git_sha(),
        server_name=service,
        send_default_pii=False,
        include_local_variables=False,
        max_request_body_size="never",
        auto_enabling_integrations=False,
        traces_sample_rate=0.0,
        before_send=before_send,
        before_breadcrumb=before_breadcrumb,
    )
    sentry_sdk.set_tag("service", service)
    sentry_sdk.set_tag("version", release_version())
    emit("sentry_enabled", environment=_environment(), release=git_sha(), version=release_version(),
         outcome=f"dsn_from_{reason}")
    return True


def capture(exc: BaseException) -> None:
    """Send one exception to Sentry and flush (the Lambda may freeze right after the response). No-op when off."""
    if not _state["sentry"]:
        return
    import sentry_sdk

    sentry_sdk.capture_exception(exc)
    sentry_sdk.flush(timeout=2)


def setup(service: str) -> None:
    """Once per container: JSON logging, then Sentry."""
    if _state["configured"]:
        return
    configure_logging()
    _state["sentry"] = init_sentry(service)
    _state["configured"] = True


# --- the handler wrapper ------------------------------------------------------------------------------------------


@contextmanager
def request_scope(event: dict, context: Any = None) -> Iterator[str]:
    request_id = request_id_for(event, context)
    bound = {"request_id": request_id}
    aws_request_id = getattr(context, "aws_request_id", None)
    if isinstance(aws_request_id, str) and aws_request_id != request_id:
        bound["aws_request_id"] = aws_request_id
    with ExitStack() as stack:
        if _state["sentry"]:
            import sentry_sdk

            # A fresh scope per invocation: a warm container must not carry one request's tags or breadcrumbs into
            # the next request's events.
            scope = stack.enter_context(sentry_sdk.isolation_scope())
            scope.set_tag("request_id", request_id)
        stack.enter_context(structlog.contextvars.bound_contextvars(**bound))
        yield request_id


def instrument(
    handler: Callable[[dict, Any], Any],
    service: str,
    route_name: Callable[[str, str], str],
) -> Callable[[dict, Any], Any]:
    """Wrap a Lambda handler: set-up on first call, request id, one ``request_completed`` line, Sentry on an
    unhandled error (re-raised, so the platform still records it)."""

    def wrapped(event: dict, context: Any = None) -> Any:
        setup(service)
        event = event if isinstance(event, dict) else {}
        rc = event.get("requestContext")
        http = (rc.get("http") if isinstance(rc, dict) else None) or {}
        method = str(http.get("method") or event.get("httpMethod") or "-")
        path = str(http.get("path") or event.get("rawPath") or event.get("path") or "")
        route = route_name(method, path) if path else "direct_invoke"
        started = time.perf_counter()
        with request_scope(event, context) as request_id:
            try:
                if SENTRY_TEST_KEY in event and rc is None:
                    # Operator check only (direct `lambda invoke`, IAM-authorised; an API Gateway event always has
                    # requestContext, so this is not reachable over HTTP): proves the DSN, release and scrubbing.
                    raise SentryTestError("AA-871 Sentry test event (expected); contact test@example.com")
                response = handler(event, context)
            except Exception as exc:
                emit("request_completed", method=method, route=route, status=500, error=type(exc).__name__,
                     duration_ms=round((time.perf_counter() - started) * 1000, 1))
                capture(exc)
                raise
            status = response.get("statusCode") if isinstance(response, dict) else None
            emit("request_completed", method=method, route=route, status=status,
                 duration_ms=round((time.perf_counter() - started) * 1000, 1))
            return with_request_id(response, request_id)

    wrapped.__wrapped__ = handler  # type: ignore[attr-defined]
    return wrapped
