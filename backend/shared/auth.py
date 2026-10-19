"""Edge shared-secret check for both Lambdas.

API Gateway sits in front with authorization = NONE (see the Infra
tripplanner.tf note). Rather than add a separate authorizer Lambda for the
MVP, each function verifies a shared secret in the X-TripPlanner-Key header
that only the server-side BFF knows. This is a coarse "is this our BFF"
gate, not per-user auth (guests are anonymous by design).

Where the expected key comes from (AA-878):

1. ``config.TRIPPLANNER_API_KEY`` (the env) when set — local dev, and the
   rollout window before Infra removes it from the Lambda env.
2. On Lambda (the runtime sets ``AWS_LAMBDA_FUNCTION_NAME``): the Secrets
   Manager secret ``tripplanner/<ENVIRONMENT>/api-key``, read by name with the
   function's IAM role and cached per container for
   ``config.API_KEY_CACHE_SECONDS``. No plaintext key in the function config.
3. Off Lambda with no env key: the gate is disabled (local dev + unit tests),
   so nothing needs the secret to run offline.

Fail closed: on Lambda, a key that is missing, unreadable, empty or still the
``REPLACE_ME`` placeholder answers 503 — never "allow through".
"""
from __future__ import annotations

import hmac
import json
import os
import time
from typing import Any, Optional

from backend import config

_PLACEHOLDER = "REPLACE_ME"


class ApiKeyUnavailable(Exception):
    """The expected edge key cannot be determined; the request must be refused (503)."""


# Per-container cache of the key read from Secrets Manager.
_cache: dict[str, Any] = {"value": None, "fetched_at": 0.0, "retry_at": 0.0}


def reset_cache() -> None:
    """Forget the cached key (tests)."""
    _cache.update(value=None, fetched_at=0.0, retry_at=0.0)


def _on_lambda() -> bool:
    return bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))


def _usable(value: Optional[str]) -> bool:
    return bool(value) and bool(value.strip()) and value.strip() != _PLACEHOLDER


def secret_name() -> str:
    """Secret holding the edge key, derived from the existing ENVIRONMENT env."""
    env = (config.ENVIRONMENT or "").strip()
    if not env:
        raise ApiKeyUnavailable("ENVIRONMENT is not set")
    return config.API_KEY_SECRET_NAME_TEMPLATE.format(environment=env)


def _fetch_secret(name: str) -> Optional[str]:
    """Read the secret string by name. Separate function so tests can replace it."""
    import boto3  # lazy: local/tests never reach this

    client = boto3.client("secretsmanager", region_name=config.BEDROCK_REGION)
    return client.get_secret_value(SecretId=name).get("SecretString")


def _log_unavailable(reason: str) -> None:
    # Names only; never the value.
    print(json.dumps({"evt": "api_key_unavailable", "reason": reason}))


def _key_from_secrets_manager() -> str:
    now = time.monotonic()
    cached = _cache["value"]
    if cached is not None and now - _cache["fetched_at"] < config.API_KEY_CACHE_SECONDS:
        return cached
    if now < _cache["retry_at"]:
        if cached is not None:
            return cached
        raise ApiKeyUnavailable("recent read failed")

    try:
        name = secret_name()
        value = _fetch_secret(name)
    except ApiKeyUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - any read error must refuse, not crash
        _cache["retry_at"] = now + config.API_KEY_RETRY_AFTER_SECONDS
        _log_unavailable(f"read_failed:{type(exc).__name__}")
        if cached is not None:
            # A key that was valid moments ago beats refusing every request on a blip.
            return cached
        raise ApiKeyUnavailable("read failed") from None

    if not _usable(value):
        _cache["retry_at"] = now + config.API_KEY_RETRY_AFTER_SECONDS
        _log_unavailable("empty_or_placeholder")
        raise ApiKeyUnavailable("secret is empty or the placeholder")

    _cache.update(value=value.strip(), fetched_at=now, retry_at=0.0)
    return _cache["value"]


def expected_key() -> Optional[str]:
    """The key the request must carry; None means the gate is disabled (local only).

    Raises ApiKeyUnavailable when the key cannot be determined (fail closed).
    """
    env_key = config.TRIPPLANNER_API_KEY
    if env_key:
        if not _usable(env_key):
            _log_unavailable("env_placeholder")
            raise ApiKeyUnavailable("env key is the placeholder")
        return env_key
    if not _on_lambda():
        return None  # local dev / unit tests: gate disabled
    return _key_from_secrets_manager()


def _headers_lower(event: dict) -> dict[str, str]:
    """API Gateway v2 lowercases header names, but be defensive for other
    event shapes / local servers by normalising here."""
    raw = event.get("headers") or {}
    return {str(k).lower(): v for k, v in raw.items()}


def _json_response(status: int, error: str, headers: Optional[dict] = None) -> dict:
    return {
        "statusCode": status,
        "headers": headers or {"content-type": "application/json", "cache-control": "no-store"},
        "body": json.dumps({"error": error}),
    }


def unauthorized_response(headers: Optional[dict] = None) -> dict:
    return _json_response(401, "unauthorized", headers)


def unavailable_response(headers: Optional[dict] = None) -> dict:
    return _json_response(503, "api key unavailable", headers)


def check_event(event: dict) -> Optional[dict]:
    """Return a 401 (wrong/missing header) or 503 (key unavailable) response dict
    when the request must be refused; otherwise None (allow).

    Uses hmac.compare_digest for a constant-time comparison so a wrong key
    can't be discovered by timing.
    """
    try:
        expected = expected_key()
    except ApiKeyUnavailable:
        return unavailable_response()
    if expected is None:
        return None  # gate disabled (local/test) — allow through

    provided = _headers_lower(event).get(config.API_KEY_HEADER, "")
    if not provided or not hmac.compare_digest(str(provided), str(expected)):
        return unauthorized_response()
    return None
