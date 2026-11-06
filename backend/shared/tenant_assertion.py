"""AA-1038 R5 slice 2 (root ADR 0019 d10): the signed tenant assertion the BFF sends with a /{slug} request.

The browser never names a tenant to the Lambdas. The BFF (frontend/lib/tenantAssertion.ts) resolves the slug of the
page through GET /browse/tenant/{slug} (CIS view catalog.v_tenant_tripplanner_v1: served tenants only), then signs
``{"exp", "iat", "slug", "tid"}`` per request and sends it in ``x-tp-tenant``. The assembly Lambda verifies it here
BEFORE it takes a connection, and binds ``tid`` only when the check passes (backend/shared/tenant.py). A forged,
altered, expired or over-long assertion is a 401 with no SQL and no bind.

Format: ``v1.<payload>.<signature>``, both parts base64url without padding; payload = compact JSON with sorted keys;
signature = HMAC-SHA256 over ``"v1." + payload`` (the ASCII text) with the assertion key.

Key: HMAC-SHA256(edge key, ``KEY_LABEL``), where the edge key is the X-TripPlanner-Key secret both Lambdas already read
(``tripplanner/<ENVIRONMENT>/api-key``, backend/shared/auth.py) and the BFF already holds (Vercel env
``TRIPPLANNER_API_KEY``). The label separates the two uses of one secret: the derived key never travels, and a leaked
assertion does not reveal the edge key. No new secret, IAM grant or env var. Fail closed: when the edge key cannot be
read, a request WITH an assertion is refused (503); a request without one keeps the default tenant.

Nothing here logs a header value: refusals log the reason only.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Optional

from backend import config
from backend.shared import observability, tenant

VERSION = "v1"
KEY_LABEL = b"aa-tripplanner tenant assertion v1"
# The slug shape of shared.tenants (varchar(100); lower-case letters, digits, '-' and '_').
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,99}$")
_MAX_LEN = 1024
_PART_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class AssertionRefused(Exception):
    """The assertion must not be trusted (401). ``reason`` is a fixed word, never the value."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class AssertionKeyUnavailable(Exception):
    """The key cannot be derived (no edge key): an assertion cannot be checked (503)."""


@dataclass(frozen=True)
class TenantAssertion:
    tenant_id: str
    slug: str
    expires_at: int


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    if not _PART_RE.match(text):
        raise AssertionRefused("malformed")
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (ValueError, TypeError):
        raise AssertionRefused("malformed") from None


def derive_key(edge_key: str) -> bytes:
    """The assertion key from the edge key (domain-separated HMAC)."""
    return hmac.new(edge_key.encode("utf-8"), KEY_LABEL, hashlib.sha256).digest()


def sign(key: bytes, tenant_id: str, slug: str, *, now: Optional[int] = None, lifetime_s: int = 120) -> str:
    """The header value for (tenant_id, slug). Used by tests and the local server; the BFF has its own copy
    (frontend/lib/tenantAssertion.ts), pinned equal by a shared test vector."""
    iat = int(time.time() if now is None else now)
    body = {"exp": iat + int(lifetime_s), "iat": iat, "slug": slug, "tid": tenant.canonical(tenant_id)}
    payload = _b64(json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signed = f"{VERSION}.{payload}"
    sig = _b64(hmac.new(key, signed.encode("ascii"), hashlib.sha256).digest())
    return f"{signed}.{sig}"


def verify(value: str, key: bytes, *, now: Optional[int] = None) -> TenantAssertion:
    """The checked assertion, or AssertionRefused(reason). Signature first, then the claims."""
    if not isinstance(value, str) or not value or len(value) > _MAX_LEN:
        raise AssertionRefused("malformed")
    parts = value.strip().split(".")
    if len(parts) != 3 or parts[0] != VERSION:
        raise AssertionRefused("malformed")
    _, payload, sig = parts
    expected = hmac.new(key, f"{VERSION}.{payload}".encode("ascii", "replace"), hashlib.sha256).digest()
    if not hmac.compare_digest(_unb64(sig), expected):
        raise AssertionRefused("signature")
    try:
        body: Any = json.loads(_unb64(payload))
    except (ValueError, UnicodeDecodeError):
        raise AssertionRefused("payload") from None
    if not isinstance(body, dict) or set(body) != {"exp", "iat", "slug", "tid"}:
        raise AssertionRefused("payload")
    exp, iat, slug, tid = body["exp"], body["iat"], body["slug"], body["tid"]
    if (not isinstance(exp, int) or isinstance(exp, bool) or not isinstance(iat, int) or isinstance(iat, bool)
            or not isinstance(slug, str) or not SLUG_RE.match(slug)):
        raise AssertionRefused("payload")
    try:
        tid = tenant.canonical(tid)
    except tenant.InvalidTenant:
        raise AssertionRefused("payload") from None
    t = int(time.time() if now is None else now)
    if exp - iat > config.TENANT_ASSERTION_MAX_LIFETIME_S or exp <= iat:
        raise AssertionRefused("lifetime")
    if iat > t + config.TENANT_ASSERTION_SKEW_S:
        raise AssertionRefused("not_yet_valid")
    if t >= exp:
        raise AssertionRefused("expired")
    return TenantAssertion(tenant_id=tid, slug=slug, expires_at=exp)


def assertion_key() -> bytes:
    """The key from the edge key the Lambda already reads; AssertionKeyUnavailable when there is none (local dev
    without TRIPPLANNER_API_KEY, or the secret unreadable)."""
    from backend.shared import auth

    try:
        edge = auth.expected_key()
    except auth.ApiKeyUnavailable:
        raise AssertionKeyUnavailable("edge key unavailable") from None
    if not edge:
        raise AssertionKeyUnavailable("no edge key")
    return derive_key(edge)


def from_headers(headers: dict[str, str]) -> Optional[TenantAssertion]:
    """The verified assertion of a request, None when the request carries none (the bare path). Raises
    AssertionRefused / AssertionKeyUnavailable; logs the refusal reason (never the value)."""
    raw = headers.get(config.TENANT_ASSERTION_HEADER)
    if raw is None or raw == "":
        return None
    try:
        return verify(raw, assertion_key())
    except AssertionRefused as e:
        observability.emit("tenant_assertion_refused", reason=e.reason)
        raise
    except AssertionKeyUnavailable:
        observability.emit("tenant_assertion_refused", reason="key_unavailable")
        raise


def refused_response() -> dict:
    return {"statusCode": 401, "headers": {"content-type": "application/json", "cache-control": "no-store"},
            "body": json.dumps({"error": "tenant_assertion_invalid"})}


def unavailable_response() -> dict:
    return {"statusCode": 503, "headers": {"content-type": "application/json", "cache-control": "no-store"},
            "body": json.dumps({"error": "tenant_assertion_unavailable"})}
