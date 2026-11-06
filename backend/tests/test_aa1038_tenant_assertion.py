"""AA-1038 R5 slice 2 — the signed BFF tenant assertion (backend/shared/tenant_assertion.py) and the /{slug} tenant
lookup (backend/browse/tenant_site.py), offline.

The assertion: a round trip; the cross-language vector the BFF pins too (frontend/lib/tenantAssertion.test.ts); a
forged signature, an altered payload, an expired, over-long or future assertion, a wrong version, extra or missing
claims and a non-uuid tenant are refused with a fixed reason and no value logged; no edge key = refused 503. The
assembly adapter: a refused assertion answers 401 / 503 before any connection is taken; a valid one binds its tenant
(source "assertion") as the first statement; no header keeps the default tenant.

The lookup: only a served, in-service row is answered, theme names / values outside the CIS CHECK shape are dropped,
a slug of the wrong shape runs no SQL, a view that is absent before CIS 379 is a 404 (never a 500), any other read
error is a 503 (never a cached 404), and the browse route answers 200 / 404 / 503 with no-store. An edge key with
surrounding whitespace derives the same assertion key as the BFF (both strip it).
"""
from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager

import pytest

from backend import config
from backend.assembly import handler
from backend.browse import handler as browse_handler
from backend.browse import tenant_site
from backend.shared import auth, tenant, tenant_assertion as ta

TENANT_A = "4b1d7e57-0000-4000-8000-0000000000a1"
NOW = 1762400000
# Built at run time (gitleaks: no literal that looks like a credential). The BFF test builds the same value.
EDGE = "-".join(["aa1038", "vector", "edge"])
# The cross-language vector: frontend/lib/tenantAssertion.test.ts signs the same claims with the same edge value and
# pins this exact header.
VECTOR_SLUG = "quillmere-voyages"
VECTOR_HEADER = (
    "v1.eyJleHAiOjE3NjI0MDAxMjAsImlhdCI6MTc2MjQwMDAwMCwic2x1ZyI6InF1aWxsbWVyZS12b3lhZ2VzIiwidGlkIjoiNGIxZDdlNTct"
    "MDAwMC00MDAwLTgwMDAtMDAwMDAwMDAwMGExIn0.iPAGhLvUYeTsgBXKhSuQXE-d5hAAnICxwSr22wdzsS8"
)


def _key() -> bytes:
    return ta.derive_key(EDGE)


def _lines(capsys) -> list[dict]:
    return [json.loads(ln) for ln in capsys.readouterr().out.splitlines() if ln.startswith("{")]


def _b64(obj) -> str:
    return ta._b64(json.dumps(obj, separators=(",", ":"), sort_keys=True).encode())


def _signed(payload_b64: str, key: bytes | None = None) -> str:
    import hashlib
    import hmac

    signed = f"v1.{payload_b64}"
    return f"{signed}.{ta._b64(hmac.new(key or _key(), signed.encode(), hashlib.sha256).digest())}"


# --- the assertion ------------------------------------------------------------------------------------------------

def test_round_trip():
    value = ta.sign(_key(), TENANT_A, "quillmere-voyages", now=NOW)
    got = ta.verify(value, _key(), now=NOW + 10)
    assert (got.tenant_id, got.slug, got.expires_at) == (TENANT_A, "quillmere-voyages", NOW + 120)


def test_the_cross_language_vector():
    assert ta.sign(_key(), TENANT_A, VECTOR_SLUG, now=NOW) == VECTOR_HEADER
    assert ta.verify(VECTOR_HEADER, _key(), now=NOW).tenant_id == TENANT_A


def test_the_key_is_derived_not_the_edge_value():
    assert _key() != EDGE.encode() and len(_key()) == 32
    assert ta.derive_key(EDGE + "x") != _key()


def test_a_forged_signature_is_refused():
    value = ta.sign(ta.derive_key("someone-else"), TENANT_A, "quillmere-voyages", now=NOW)
    with pytest.raises(ta.AssertionRefused) as e:
        ta.verify(value, _key(), now=NOW)
    assert e.value.reason == "signature"


def test_an_altered_payload_is_refused():
    head, _, sig = ta.sign(_key(), TENANT_A, "quillmere-voyages", now=NOW).split(".")
    other = _b64({"exp": NOW + 120, "iat": NOW, "slug": "quillmere-voyages", "tid": str(uuid.uuid4())})
    with pytest.raises(ta.AssertionRefused) as e:
        ta.verify(f"{head}.{other}.{sig}", _key(), now=NOW)
    assert e.value.reason == "signature"


@pytest.mark.parametrize("now, reason", [
    (NOW + 120, "expired"), (NOW + 5000, "expired"), (NOW - 31, "not_yet_valid"),
])
def test_time_claims(now, reason):
    value = ta.sign(_key(), TENANT_A, "quillmere-voyages", now=NOW)
    with pytest.raises(ta.AssertionRefused) as e:
        ta.verify(value, _key(), now=now)
    assert e.value.reason == reason


def test_a_lifetime_over_the_cap_is_refused_even_when_signed():
    value = ta.sign(_key(), TENANT_A, "quillmere-voyages", now=NOW, lifetime_s=config.TENANT_ASSERTION_MAX_LIFETIME_S + 1)
    with pytest.raises(ta.AssertionRefused) as e:
        ta.verify(value, _key(), now=NOW)
    assert e.value.reason == "lifetime"


@pytest.mark.parametrize("claims", [
    {"exp": NOW + 60, "iat": NOW, "slug": "quillmere-voyages", "tid": "not-a-uuid"},
    {"exp": NOW + 60, "iat": NOW, "slug": "Bad Slug", "tid": TENANT_A},
    {"exp": NOW + 60, "iat": NOW, "slug": "quillmere-voyages"},
    {"exp": NOW + 60, "iat": NOW, "slug": "quillmere-voyages", "tid": TENANT_A, "role": "admin"},
    {"exp": "later", "iat": NOW, "slug": "quillmere-voyages", "tid": TENANT_A},
    {"exp": True, "iat": NOW, "slug": "quillmere-voyages", "tid": TENANT_A},
])
def test_bad_claims_are_refused_even_when_signed(claims):
    with pytest.raises(ta.AssertionRefused) as e:
        ta.verify(_signed(_b64(claims)), _key(), now=NOW)
    assert e.value.reason == "payload"


@pytest.mark.parametrize("value", [
    "", "v1", "v1.a", "v2.abc.def", "v1.a.b.c", "v1.!!!.abc", "v1.abc.@@@", "x" * 2000,
])
def test_malformed_values_are_refused(value):
    with pytest.raises(ta.AssertionRefused):
        ta.verify(value, _key(), now=NOW)


# --- the key and the adapter ----------------------------------------------------------------------------------------

def test_no_edge_key_means_an_assertion_cannot_be_checked(monkeypatch):
    monkeypatch.setattr(config, "TRIPPLANNER_API_KEY", "")
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    with pytest.raises(ta.AssertionKeyUnavailable):
        ta.assertion_key()


def test_the_key_comes_from_the_edge_key(monkeypatch):
    monkeypatch.setattr(config, "TRIPPLANNER_API_KEY", EDGE)
    assert ta.assertion_key() == _key()


@pytest.mark.parametrize("padded", [EDGE + "\n", " " + EDGE + "\r\n", "\t" + EDGE + " "])
def test_an_edge_key_with_surrounding_whitespace_derives_the_same_key(padded):
    # The BFF derives from the raw Vercel env value, auth.py strips the Secrets Manager value: both normalise the same.
    assert ta.derive_key(padded) == _key()
    assert ta.verify(VECTOR_HEADER, ta.derive_key(padded), now=NOW).tenant_id == TENANT_A


def test_a_padded_env_key_still_verifies_the_vector(monkeypatch):
    monkeypatch.setattr(config, "TRIPPLANNER_API_KEY", EDGE + "\n")
    assert ta.verify(VECTOR_HEADER, ta.assertion_key(), now=NOW).slug == VECTOR_SLUG


def test_a_padded_secrets_manager_value_still_verifies_the_vector(monkeypatch):
    monkeypatch.setattr(config, "TRIPPLANNER_API_KEY", "")
    monkeypatch.setattr(config, "ENVIRONMENT", "test")
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "aa-tripplanner-test-assembly")
    monkeypatch.setattr(auth, "_fetch_secret", lambda name: EDGE + "\n")
    auth.reset_cache()
    try:
        assert ta.verify(VECTOR_HEADER, ta.assertion_key(), now=NOW).tenant_id == TENANT_A
    finally:
        auth.reset_cache()


def test_a_whitespace_only_edge_key_is_no_key(monkeypatch):
    monkeypatch.setattr(config, "TRIPPLANNER_API_KEY", " \n")
    monkeypatch.setattr(auth, "_usable", lambda value: True)   # past the edge gate's own placeholder check
    with pytest.raises(ta.AssertionKeyUnavailable):
        ta.assertion_key()


def test_adapter_without_a_header_keeps_the_default_tenant(monkeypatch):
    monkeypatch.setattr(config, "TRIPPLANNER_API_KEY", EDGE)
    assert handler.tenant_assertion_answer({}) == {"tenant": {}}
    assert handler.tenant_assertion_answer({config.TENANT_ASSERTION_HEADER: ""}) == {"tenant": {}}


def test_adapter_with_a_valid_header_names_the_tenant(monkeypatch):
    monkeypatch.setattr(config, "TRIPPLANNER_API_KEY", EDGE)
    value = ta.sign(_key(), TENANT_A, "quillmere-voyages")
    got = handler.tenant_assertion_answer({config.TENANT_ASSERTION_HEADER: value})
    assert got == {"tenant": {"tenant_id": TENANT_A, "tenant_source": tenant.SOURCE_ASSERTION}}


def test_adapter_refuses_a_forged_header_with_401_and_logs_the_reason_only(monkeypatch, capsys):
    monkeypatch.setattr(config, "TRIPPLANNER_API_KEY", EDGE)
    value = ta.sign(ta.derive_key("someone-else"), TENANT_A, "quillmere-voyages")
    got = handler.tenant_assertion_answer({config.TENANT_ASSERTION_HEADER: value})
    assert got["response"]["statusCode"] == 401
    assert json.loads(got["response"]["body"]) == {"error": "tenant_assertion_invalid"}
    out = capsys.readouterr().out
    assert value not in out and TENANT_A not in out
    assert any(json.loads(ln).get("reason") == "signature" for ln in out.splitlines() if ln.startswith("{"))


def test_adapter_refuses_an_expired_header_with_401(monkeypatch):
    monkeypatch.setattr(config, "TRIPPLANNER_API_KEY", EDGE)
    value = ta.sign(_key(), TENANT_A, "quillmere-voyages", now=NOW)
    got = handler.tenant_assertion_answer({config.TENANT_ASSERTION_HEADER: value})
    assert got["response"]["statusCode"] == 401


def test_adapter_answers_503_when_the_key_is_unavailable(monkeypatch):
    monkeypatch.setattr(config, "TRIPPLANNER_API_KEY", "")
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    value = ta.sign(_key(), TENANT_A, "quillmere-voyages")
    got = handler.tenant_assertion_answer({config.TENANT_ASSERTION_HEADER: value})
    assert got["response"]["statusCode"] == 503


def test_adapter_503_when_the_secret_cannot_be_read_on_lambda(monkeypatch):
    monkeypatch.setattr(config, "TRIPPLANNER_API_KEY", "")
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "aa-tripplanner-test-assembly")
    auth.reset_cache()

    def boom(name):
        raise RuntimeError("no access")

    monkeypatch.setattr(auth, "_fetch_secret", boom)
    try:
        got = handler.tenant_assertion_answer({config.TENANT_ASSERTION_HEADER: "v1.a.b"})
    finally:
        auth.reset_cache()
    assert got["response"]["statusCode"] == 503


class RecordingConn:
    def __init__(self):
        self.statements: list[tuple[str, tuple]] = []

    async def execute(self, query, *args):
        self.statements.append((query, args))
        return "OK"


async def test_serve_binds_the_asserted_tenant_first(monkeypatch):
    conn = RecordingConn()

    @asynccontextmanager
    async def acquire():
        yield conn

    async def fake_route(method, path, body, *, conn, guest_token, caller):
        await conn.execute("SELECT 1")
        return {"statusCode": 200, "headers": {}, "body": "{}"}

    monkeypatch.setattr(handler, "route", fake_route)
    out = await handler.serve("GET", "/trip/x", {}, acquire=acquire, tenant_id=TENANT_A,
                              tenant_source=tenant.SOURCE_ASSERTION)
    assert out["statusCode"] == 200
    assert conn.statements[0] == (tenant.BIND_SQL, (TENANT_A,))
    assert conn.statements[-1] == (tenant.UNBIND_SQL, ())


# --- the tenant lookup ----------------------------------------------------------------------------------------------

def _row(**over):
    row = {"tenant_id": uuid.UUID(TENANT_A), "slug": "quillmere-voyages", "name": "Quillmere Voyages", "kind": "agency",
           "in_service": True, "served": True, "tp_launcher": True, "tp_branding": False,
           "theme": json.dumps({"accent": "#1a2b3c", "Bad": "#000000", "ink": "red", "x" * 41: "#000000",
                                "line": "#ffffff; color: red"}),
           "locale": "en"}
    row.update(over)
    return row


class FakePool:
    def __init__(self, row=None, error: Exception | None = None):
        self.row, self.error, self.calls = row, error, []

    async def fetchrow(self, sql, *args):
        self.calls.append((sql, args))
        if self.error:
            raise self.error
        return self.row


async def test_lookup_answers_a_served_tenant_with_a_filtered_theme():
    pool = FakePool(_row())
    got = await tenant_site.lookup("quillmere-voyages", pool=pool)
    assert got == {"tenant_id": TENANT_A, "slug": "quillmere-voyages", "name": "Quillmere Voyages", "kind": "agency",
                   "launcher": True, "branding_badge": False, "theme": {"accent": "#1a2b3c"}, "locale": "en"}
    assert pool.calls == [(tenant_site.TENANT_SQL, ("quillmere-voyages",))]


@pytest.mark.parametrize("row", [None, _row(served=False), _row(in_service=False)])
async def test_lookup_hides_an_unknown_or_unserved_tenant(row):
    assert await tenant_site.lookup("quillmere-voyages", pool=FakePool(row)) is None


@pytest.mark.parametrize("slug", ["", "Quillmere", "a b", "../x", "x" * 101, "-lead", "a.b"])
async def test_lookup_runs_no_sql_for_a_bad_slug(slug):
    pool = FakePool(_row())
    assert await tenant_site.lookup(slug, pool=pool) is None
    assert pool.calls == []


class _PgError(Exception):
    def __init__(self, sqlstate):
        super().__init__("value that must not be logged")
        self.sqlstate = sqlstate


@pytest.mark.parametrize("sqlstate, name", [("42P01", "UndefinedTableError"), ("42501", "InsufficientPrivilegeError")])
async def test_lookup_degrades_to_404_when_the_view_is_absent(capsys, sqlstate, name):
    # Before CIS 379 (no view / no grant) no tenant can be served: a 404, never a 500 or a 503.
    error = type(name, (_PgError,), {})(sqlstate)
    assert await tenant_site.lookup("quillmere-voyages", pool=FakePool(error=error)) is None
    assert _lines(capsys) == [{"evt": "tenant_view_unreadable", "error": name}]


@pytest.mark.parametrize("error", [ConnectionResetError("x"), TimeoutError(), OSError("pool"),
                                   _PgError("57P01"), _PgError("53300"), RuntimeError("x")])
async def test_lookup_raises_unavailable_on_a_transient_error(capsys, error):
    with pytest.raises(tenant_site.TenantLookupUnavailable):
        await tenant_site.lookup("quillmere-voyages", pool=FakePool(error=error))
    lines = _lines(capsys)
    assert lines == [{"evt": "tenant_lookup_failed", "error": type(error).__name__}]
    assert "must not be logged" not in json.dumps(lines)


async def test_browse_route_answers_503_no_store_on_a_transient_error():
    out = await browse_handler.route("GET", "/browse/tenant/quillmere-voyages", {},
                                     pool=FakePool(error=ConnectionResetError("x")))
    assert out["statusCode"] == 503 and out["headers"]["cache-control"] == "no-store"
    assert json.loads(out["body"]) == {"error": "tenant lookup unavailable"}
    absent = await browse_handler.route("GET", "/browse/tenant/quillmere-voyages", {},
                                        pool=FakePool(error=_PgError("42P01")))
    assert absent["statusCode"] == 404


async def test_lookup_defaults_an_unknown_locale_and_a_bad_theme():
    got = await tenant_site.lookup("quillmere-voyages", pool=FakePool(_row(locale="fr", theme="not json")))
    assert got["locale"] == "en" and got["theme"] == {}


async def test_browse_route_answers_200_and_404_without_caching():
    ok = await browse_handler.route("GET", "/browse/tenant/quillmere-voyages", {}, pool=FakePool(_row()))
    assert ok["statusCode"] == 200 and json.loads(ok["body"])["tenant"]["tenant_id"] == TENANT_A
    assert ok["headers"]["cache-control"] == "no-store"
    missing = await browse_handler.route("GET", "/browse/tenant/nobody-here", {}, pool=FakePool(None))
    assert missing["statusCode"] == 404 and missing["headers"]["cache-control"] == "no-store"
    assert browse_handler.route_name("GET", "/browse/tenant/quillmere-voyages") == "tenant"
