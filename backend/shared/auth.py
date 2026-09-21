"""Edge shared-secret check for both Lambdas.

API Gateway sits in front with authorization = NONE (see the Infra
tripplanner.tf note). Rather than add a separate authorizer Lambda for the
MVP, each function verifies a shared secret in the X-TripPlanner-Key header
that only the server-side BFF knows. This is a coarse "is this our BFF"
gate, not per-user auth (guests are anonymous by design).

Disabled when config.TRIPPLANNER_API_KEY is empty (local dev + unit tests),
so nothing needs the secret to run offline. Enabled automatically in
production where the Lambda env provides the key (sourced from Secrets
Manager by Terraform) and the BFF sends the same value.
"""
from __future__ import annotations

import hmac
from typing import Any, Optional

from backend import config


def _headers_lower(event: dict) -> dict[str, str]:
    """API Gateway v2 lowercases header names, but be defensive for other
    event shapes / local servers by normalising here."""
    raw = event.get("headers") or {}
    return {str(k).lower(): v for k, v in raw.items()}


def unauthorized_response(headers: Optional[dict] = None) -> dict:
    body = '{"error": "unauthorized"}'
    return {
        "statusCode": 401,
        "headers": headers or {"content-type": "application/json", "cache-control": "no-store"},
        "body": body,
    }


def check_event(event: dict) -> Optional[dict]:
    """Return a 401 response dict if the shared-secret gate is enabled and
    the request's header is missing or wrong; otherwise None (allow).

    Uses hmac.compare_digest for a constant-time comparison so a wrong key
    can't be discovered by timing.
    """
    expected = config.TRIPPLANNER_API_KEY
    if not expected:
        return None  # gate disabled (local/test) — allow through

    provided = _headers_lower(event).get(config.API_KEY_HEADER, "")
    if not provided or not hmac.compare_digest(str(provided), str(expected)):
        return unauthorized_response()
    return None
