"""AA-1038 R5 slice 2 (root ADR 0019 d10): the signed tenant assertion the BFF sends with a /{slug} request.

The browser never names a tenant to the Lambdas. The BFF (frontend/lib/tenantAssertion.ts) resolves the slug of the
page through GET /browse/tenant/{slug} (CIS view catalog.v_tenant_tripplanner_v1: served tenants only), then signs
``{"exp", "iat", "slug", "tid"}`` per request and sends it in ``x-tp-tenant``. The assembly Lambda verifies it here
BEFORE it takes a connection, and binds ``tid`` only when the check passes (backend/shared/tenant.py). A forged,
altered, expired or over-long assertion is a 401 with no SQL and no bind.

Format: ``v1.<payload>.<signature>``, both parts base64url without padding; payload = compact JSON with sorted keys;
signature = HMAC-SHA256 over ``"v1." + payload`` (the ASCII text) with the assertion key.

Key (AA-1092, S278 audit M19): HMAC-SHA256(key material, ``KEY_LABEL``), where the key material is the DEDICATED
assertion secret ``tripplanner/<ENVIRONMENT>/tenant-assertion-key`` (JSON, see ``KeySet``), no longer the edge key.
The tenant binding gets a key of its own, rotated on its own schedule. The BFF signs with the secret's ``current``
value (web task env ``TP_TENANT_ASSERTION_KEY``, frontend/lib/tenantAssertion.ts ``assertionKeyMaterial``). The
Lambdas accept:

  * ``current`` always;
  * ``previous`` only until ``previous_until`` (a rotation's grace window: the web task still signs with the old value
    until its next deployment);
  * the edge-derived key (the pre-AA-1092 key) only until ``edge_derived_until`` (the cut-over grace), or while the
    secret has no value yet (a shell before the first fill: the behaviour before AA-1092, logged on every use).

Each container caches the secret for ``config.API_KEY_CACHE_SECONDS`` (10 minutes). A signature miss re-reads it once
when the cached copy is at least ``config.TENANT_ASSERTION_REFRESH_MIN_AGE_S`` old (``_refresh_after_miss``), then
verifies again: a web task that already signs with a new ``current`` (first fill, rotation) is not refused until the
cache expires. The rollout still waits 10 minutes after a write before the web deploy (AA-CIS-Infra
tools/identity/tripplanner_assertion_key.py ``deploy_not_before``); the re-read covers a deploy that did not.

A grace longer than ``config.TENANT_ASSERTION_MAX_GRACE_S`` is ignored (the old key is refused). A secret that is
malformed, or a ``current`` shorter than ``config.TENANT_ASSERTION_KEY_MIN_LENGTH``, fails closed: a request WITH an
assertion is refused (503); a request without one keeps the default tenant.

Nothing here logs a header value: refusals log the reason only.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence, Union

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
    # Which accepted key signed it ("current", "previous", "edge_derived"; "" when verify got one bare key).
    key_label: str = ""


@dataclass(frozen=True)
class AssertionKey:
    """One key the verifier accepts, with the name logged when it is not the current one."""

    label: str
    key: bytes = field(repr=False)


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    if not _PART_RE.match(text):
        raise AssertionRefused("malformed")
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (ValueError, TypeError):
        raise AssertionRefused("malformed") from None


# The whitespace both sides strip from the edge key before deriving (frontend/lib/tenantAssertion.ts EDGE_KEY_TRIM):
# the HTTP whitespace a fetch() header value loses anyway. auth.py strips the Secrets Manager value and env
# values can carry a trailing newline, so without this the edge check would pass and every assertion fail.
EDGE_KEY_WHITESPACE = " \t\r\n"


def normalise_edge_key(edge_key: str) -> str:
    return edge_key.strip(EDGE_KEY_WHITESPACE)


# The same trim for any key material (the dedicated key in a task env can carry a newline too).
normalise_key_material = normalise_edge_key


def derive_key(material: str) -> bytes:
    """The assertion key from key material (the dedicated secret's value, or the edge key for the legacy key):
    a domain-separated HMAC, after normalise_key_material. Same formula on the BFF (deriveKey)."""
    return hmac.new(normalise_key_material(material).encode("utf-8"), KEY_LABEL, hashlib.sha256).digest()


def sign(key: bytes, tenant_id: str, slug: str, *, now: Optional[int] = None, lifetime_s: int = 120) -> str:
    """The header value for (tenant_id, slug). Used by tests and the local server; the BFF has its own copy
    (frontend/lib/tenantAssertion.ts), pinned equal by a shared test vector."""
    iat = int(time.time() if now is None else now)
    body = {"exp": iat + int(lifetime_s), "iat": iat, "slug": slug, "tid": tenant.canonical(tenant_id)}
    payload = _b64(json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signed = f"{VERSION}.{payload}"
    sig = _b64(hmac.new(key, signed.encode("ascii"), hashlib.sha256).digest())
    return f"{signed}.{sig}"


def verify(value: str, key: Union[bytes, Sequence[AssertionKey]], *, now: Optional[int] = None) -> TenantAssertion:
    """The checked assertion, or AssertionRefused(reason). Signature first (against every accepted key, each compared
    in constant time), then the claims. ``key``: one bare key, or the accepted keys in order (assertion_keys())."""
    if not isinstance(value, str) or not value or len(value) > _MAX_LEN:
        raise AssertionRefused("malformed")
    parts = value.strip().split(".")
    if len(parts) != 3 or parts[0] != VERSION:
        raise AssertionRefused("malformed")
    _, payload, sig = parts
    candidates = (AssertionKey("", key),) if isinstance(key, (bytes, bytearray)) else tuple(key)
    if not candidates:
        raise AssertionKeyUnavailable("no accepted key")
    given = _unb64(sig)
    signed = f"{VERSION}.{payload}".encode("ascii", "replace")
    label = None
    for cand in candidates:
        expected = hmac.new(cand.key, signed, hashlib.sha256).digest()
        if hmac.compare_digest(given, expected) and label is None:
            label = cand.label
    if label is None:
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
    return TenantAssertion(tenant_id=tid, slug=slug, expires_at=exp, key_label=label)


# --- the accepted keys (AA-1092) ------------------------------------------------------------------------------------

_PLACEHOLDER = "REPLACE_ME"
_LABEL_CURRENT, _LABEL_PREVIOUS, _LABEL_EDGE = "current", "previous", "edge_derived"
_KEYSET_FIELDS = {"current", "previous", "previous_until", "edge_derived_until"}

# Per-container cache of the secret string (None = the secret has no value yet), like auth.py's edge key cache.
_cache: dict[str, Any] = {"loaded": False, "raw": None, "fetched_at": 0.0, "retry_at": 0.0}


def reset_cache() -> None:
    """Forget the cached secret (tests)."""
    _cache.update(loaded=False, raw=None, fetched_at=0.0, retry_at=0.0)


@dataclass(frozen=True)
class KeySet:
    """The parsed secret. ``current`` None = not provisioned (a shell, or the placeholder): the edge-derived key only,
    as before AA-1092. Timestamps are epoch seconds."""

    current: Optional[str] = field(default=None, repr=False)
    previous: Optional[str] = field(default=None, repr=False)
    previous_until: Optional[int] = None
    edge_derived_until: Optional[int] = None


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and normalise_key_material(value) in ("", _PLACEHOLDER))


def _epoch(value: Any, name: str) -> Optional[int]:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise AssertionKeyUnavailable(f"{name} is not an epoch second")
    return value


def _material(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise AssertionKeyUnavailable(f"{name} is not a string")
    text = normalise_key_material(value)
    if len(text) < config.TENANT_ASSERTION_KEY_MIN_LENGTH:
        raise AssertionKeyUnavailable(f"{name} is too short")
    return text


def parse_keyset(raw: Optional[str]) -> KeySet:
    """The secret's value -> KeySet. No value / the placeholder / a blank ``current`` = not provisioned. Anything
    else must be the JSON object of the header; a malformed one raises AssertionKeyUnavailable (fail closed)."""
    if raw is None or _blank(raw):
        return KeySet()
    try:
        data = json.loads(raw)
    except ValueError:
        raise AssertionKeyUnavailable("secret is not JSON") from None
    if not isinstance(data, dict) or not set(data) <= _KEYSET_FIELDS:
        raise AssertionKeyUnavailable("secret has unknown fields")
    if _blank(data.get("current")):
        return KeySet()
    previous = None if _blank(data.get("previous")) else _material(data.get("previous"), "previous")
    return KeySet(
        current=_material(data["current"], "current"),
        previous=previous,
        previous_until=_epoch(data.get("previous_until"), "previous_until"),
        edge_derived_until=_epoch(data.get("edge_derived_until"), "edge_derived_until"),
    )


def _in_grace(until: Optional[int], now: int, what: str) -> bool:
    if until is None or until <= now:
        return False
    if until - now > config.TENANT_ASSERTION_MAX_GRACE_S:
        observability.emit("tenant_assertion_grace_ignored", key=what)
        return False
    return True


def _edge_key() -> Optional[str]:
    from backend.shared import auth

    try:
        edge = auth.expected_key()
    except auth.ApiKeyUnavailable:
        return None
    return edge if edge and normalise_edge_key(edge) else None


def keys_for(keyset: KeySet, edge_key: Optional[str], *, now: int) -> tuple[AssertionKey, ...]:
    """The keys an assertion may be signed with at ``now``, current first. Pure (tests)."""
    if keyset.current is None:
        return (AssertionKey(_LABEL_EDGE, derive_key(edge_key)),) if edge_key else ()
    out = [AssertionKey(_LABEL_CURRENT, derive_key(keyset.current))]
    if keyset.previous and _in_grace(keyset.previous_until, now, _LABEL_PREVIOUS):
        out.append(AssertionKey(_LABEL_PREVIOUS, derive_key(keyset.previous)))
    if edge_key and _in_grace(keyset.edge_derived_until, now, _LABEL_EDGE):
        out.append(AssertionKey(_LABEL_EDGE, derive_key(edge_key)))
    return tuple(out)


def _on_lambda() -> bool:
    return bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))


def secret_name() -> str:
    env = (config.ENVIRONMENT or "").strip()
    if not env:
        raise AssertionKeyUnavailable("ENVIRONMENT is not set")
    return config.TENANT_ASSERTION_KEY_SECRET_NAME_TEMPLATE.format(environment=env)


def _fetch_secret(name: str) -> Optional[str]:
    """The secret string, None when the secret has no value yet (ResourceNotFoundException: a shell before its first
    fill). Any other error raises. Separate function so tests can replace it."""
    import boto3  # lazy: local/tests never reach this

    client = boto3.client("secretsmanager", region_name=config.BEDROCK_REGION)
    try:
        return client.get_secret_value(SecretId=name).get("SecretString")
    except client.exceptions.ResourceNotFoundException:
        return None


def _raw_secret() -> Optional[str]:
    """The cached secret string (None = no value yet); AssertionKeyUnavailable when it cannot be read and nothing is
    cached. A read error after a good read keeps the cached value (a blip must not refuse every tenant)."""
    now = time.monotonic()
    if _cache["loaded"] and now - _cache["fetched_at"] < config.API_KEY_CACHE_SECONDS:
        return _cache["raw"]
    if now < _cache["retry_at"]:
        if _cache["loaded"]:
            return _cache["raw"]
        raise AssertionKeyUnavailable("recent read failed")
    try:
        raw = _fetch_secret(secret_name())
    except AssertionKeyUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - any read error must refuse, not crash
        _cache["retry_at"] = now + config.API_KEY_RETRY_AFTER_SECONDS
        observability.emit("tenant_assertion_key_unavailable", reason=f"read_failed:{type(exc).__name__}")
        if _cache["loaded"]:
            return _cache["raw"]
        raise AssertionKeyUnavailable("read failed") from None
    _cache.update(loaded=True, raw=raw, fetched_at=now, retry_at=0.0)
    return raw


def _refresh_after_miss() -> bool:
    """After a signature miss on Lambda: re-read the secret once when the cached copy is at least
    TENANT_ASSERTION_REFRESH_MIN_AGE_S old (and no failed read is backing off). True when the re-read returned another
    value (verify again); False otherwise. Throttled per container, so a flood of forged assertions costs at most one
    Secrets Manager read per TENANT_ASSERTION_REFRESH_MIN_AGE_S."""
    if config.TENANT_ASSERTION_KEY or not _on_lambda() or not _cache["loaded"]:
        return False  # the env key never changes; an unloaded cache was read by this very request
    now = time.monotonic()
    fetched_at = _cache["fetched_at"]
    if now - fetched_at < config.TENANT_ASSERTION_REFRESH_MIN_AGE_S or now < _cache["retry_at"]:
        return False
    before = _cache["raw"]
    expired = now - config.API_KEY_CACHE_SECONDS - 1.0
    _cache["fetched_at"] = expired  # _raw_secret now reads
    try:
        raw = _raw_secret()
    except AssertionKeyUnavailable:  # pragma: no cover - a loaded cache is returned on a read error
        raw = before
    if _cache["fetched_at"] == expired:
        _cache["fetched_at"] = fetched_at  # the read failed: keep the old age (retry_at throttles the next try)
    if raw == before:
        return False
    observability.emit("tenant_assertion_key_refreshed")
    return True


def current_keyset() -> KeySet:
    """The env key (local dev, tests) wins; on Lambda the secret; off Lambda without an env key, not provisioned
    (the edge-derived key, the local behaviour before AA-1092)."""
    if config.TENANT_ASSERTION_KEY:
        return KeySet(current=_material(config.TENANT_ASSERTION_KEY, "TP_TENANT_ASSERTION_KEY"))
    if not _on_lambda():
        return KeySet()
    return parse_keyset(_raw_secret())


def assertion_keys(*, now: Optional[int] = None) -> tuple[AssertionKey, ...]:
    """The accepted keys; AssertionKeyUnavailable when there is none (no edge key and no dedicated key, an unreadable
    or malformed secret)."""
    keyset = current_keyset()
    keys = keys_for(keyset, _edge_key(), now=int(time.time() if now is None else now))
    if not keys:
        raise AssertionKeyUnavailable("no assertion key")
    return keys


def assertion_key() -> bytes:
    """The key the BFF signs with today (the first accepted key): the local server and tests sign with it."""
    return assertion_keys()[0].key


def from_headers(headers: dict[str, str]) -> Optional[TenantAssertion]:
    """The verified assertion of a request, None when the request carries none (the bare path). Raises
    AssertionRefused / AssertionKeyUnavailable; logs the refusal reason (never the value)."""
    raw = headers.get(config.TENANT_ASSERTION_HEADER)
    if raw is None or raw == "":
        return None
    try:
        try:
            checked = verify(raw, assertion_keys())
        except AssertionRefused as e:
            # A key this container has not seen yet (first fill, rotation): one throttled re-read, then verify again.
            if e.reason != "signature" or not _refresh_after_miss():
                raise
            checked = verify(raw, assertion_keys())
    except AssertionRefused as e:
        observability.emit("tenant_assertion_refused", reason=e.reason)
        raise
    except AssertionKeyUnavailable:
        observability.emit("tenant_assertion_refused", reason="key_unavailable")
        raise
    if checked.key_label != _LABEL_CURRENT:
        # A grace key or the pre-AA-1092 edge-derived key: visible in the logs until the cut-over or rotation ends.
        observability.emit("tenant_assertion_key_used", key=checked.key_label)
    return checked


def refused_response() -> dict:
    return {"statusCode": 401, "headers": {"content-type": "application/json", "cache-control": "no-store"},
            "body": json.dumps({"error": "tenant_assertion_invalid"})}


def unavailable_response() -> dict:
    return {"statusCode": 503, "headers": {"content-type": "application/json", "cache-control": "no-store"},
            "body": json.dumps({"error": "tenant_assertion_unavailable"})}
