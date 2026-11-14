"""AA-1038 R5 slice 2 (root ADR 0019 d10): which tenant a /{slug} path is, for the BFF.

GET /browse/tenant/{slug} reads the CIS contract view catalog.v_tenant_tripplanner_v1 (CIS 379, tenant.entitlements.v1
1.4, SELECT to role tripplanner): one row per tenant with a TripPlanner subscription. The BFF calls it server-side
(behind the edge key) before it signs a tenant assertion (backend/shared/tenant_assertion.py), and the /{slug} pages
call it to decide between the planner and a 404.

Only a SERVED tenant is answered (in service and tp.hosted live); an unknown slug, a tenant without the add-on, a
suspended tenant and a slug of the wrong shape are all the same 404, so the answer says nothing about which one it was.
The view does not exist or is not granted (42P01 / 42501: CIS 379 not applied yet): no tenant can be served, so every
slug is a 404 and one log line says why; the bare path keeps the default tenant, so nothing that works today depends
on this view. Any other read error (pool, connection, timeout) is TenantLookupUnavailable, which the route answers 503
no-store: a transient failure must never look like "no such tenant" (the BFF caches a 404 for 30 s, never a 503).

AA-1090 R8: GET /browse/tenant-host/{host} answers the same served tenant for an ACTIVE custom host of its site (an
agency's own domain, e.g. trip.<agency domain>): CIS 428 appends custom_domains (text[]) to the view, filled by the
CIS job that attached the host to the TripPlanner agency CloudFront distribution. The web proxy calls it for a request whose
Host is not one of our own hosts. Before CIS 428 the column is absent (42703): every host is a 404, as for the view.

The answer carries what the planner needs to brand the site (name, theme, locale, the BO-8 badge flag) and the tenant
id the BFF signs. It holds no personal data. The theme is passed through filtered: only names and values of the shape
the CIS CHECK allows (a second check, so a bad row can never reach a CSS variable).
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from backend.shared.tenant_assertion import SLUG_RE

VIEW = "catalog.v_tenant_tripplanner_v1"
# Column names, never * (the CIS test test_aa1038_migration_379.py runs this exact statement as tripplanner).
TENANT_SQL = (f"SELECT tenant_id, slug, name, kind, in_service, served, tp_launcher, tp_branding, theme, locale "
              f"FROM {VIEW} WHERE slug = $1")

# AA-1090: the same columns for a request Host (CIS 428 custom_domains; a host is unique across tenants in CIS).
HOST_SQL = (f"SELECT tenant_id, slug, name, kind, in_service, served, tp_launcher, tp_branding, theme, locale "
            f"FROM {VIEW} WHERE $1 = ANY (custom_domains)")
# A lower-case host name (CIS 427 tenant_tripplanner_domain_domain_chk), at most 253 characters.
HOST_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]([a-z0-9-]{0,61}[a-z0-9])?$")

_TOKEN_RE = re.compile(r"^[a-z][a-z0-9-]{0,39}$")
_COLOUR_RE = re.compile(r"^#[0-9a-f]{6}$")
LOCALES = ("en", "vi")
# The SQLSTATEs that mean "the view is not there for this role" (a deploy before CIS 379): a 404, not a 503.
VIEW_ABSENT_SQLSTATES = frozenset({"42P01", "42501"})
# AA-1090: and "the custom_domains column is not there yet" (a deploy before CIS 428).
HOST_ABSENT_SQLSTATES = VIEW_ABSENT_SQLSTATES | {"42703"}


class TenantLookupUnavailable(Exception):
    """The lookup could not run (database or pool error): the answer is unknown, not "no such tenant"."""


def _theme(raw: Any) -> dict[str, str]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items()
            if isinstance(k, str) and _TOKEN_RE.match(k) and isinstance(v, str) and _COLOUR_RE.match(v)}


def shape(row: Any) -> dict:
    """The JSON answer of a served tenant row."""
    locale = row["locale"] if row["locale"] in LOCALES else "en"
    return {
        "tenant_id": str(row["tenant_id"]),
        "slug": row["slug"],
        "name": row["name"],
        "kind": row["kind"],
        "launcher": bool(row["tp_launcher"]),
        "branding_badge": bool(row["tp_branding"]),
        "theme": _theme(row["theme"]),
        "locale": locale,
    }


async def lookup(slug: str, *, pool: Any) -> Optional[dict]:
    """The served tenant of `slug`, or None (unknown, not served, wrong shape, or the view absent before CIS 379).
    Raises TenantLookupUnavailable on any other read error."""
    if not isinstance(slug, str) or not SLUG_RE.match(slug):
        return None
    try:
        row = await pool.fetchrow(TENANT_SQL, slug)
    except Exception as exc:  # noqa: BLE001 — only the type is logged, never str(exc) (asyncpg echoes values)
        if getattr(exc, "sqlstate", None) in VIEW_ABSENT_SQLSTATES:
            print(json.dumps({"evt": "tenant_view_unreadable", "error": type(exc).__name__}))
            return None
        print(json.dumps({"evt": "tenant_lookup_failed", "error": type(exc).__name__}))
        raise TenantLookupUnavailable(type(exc).__name__) from None
    if row is None or not row["served"] or not row["in_service"]:
        return None
    return shape(row)


def normalise_host(host: str) -> str:
    """The host as CIS stores it: lower-case, without a port or a trailing root dot."""
    host = host.strip().lower()
    if host.startswith("["):          # an IPv6 literal is never a tenant host
        return ""
    return host.split(":", 1)[0].rstrip(".")


async def lookup_host(host: str, *, pool: Any) -> Optional[dict]:
    """The served tenant whose site has ``host`` as an active custom domain, or None (unknown, not served, a bad
    shape, or CIS 379 / 428 not applied yet). Raises TenantLookupUnavailable on any other read error."""
    if not isinstance(host, str):
        return None
    host = normalise_host(host)
    if not HOST_RE.match(host):
        return None
    try:
        row = await pool.fetchrow(HOST_SQL, host)
    except Exception as exc:  # noqa: BLE001 — only the type is logged, never str(exc) (asyncpg echoes values)
        if getattr(exc, "sqlstate", None) in HOST_ABSENT_SQLSTATES:
            print(json.dumps({"evt": "tenant_host_unreadable", "error": type(exc).__name__}))
            return None
        print(json.dumps({"evt": "tenant_host_lookup_failed", "error": type(exc).__name__}))
        raise TenantLookupUnavailable(type(exc).__name__) from None
    if row is None or not row["served"] or not row["in_service"]:
        return None
    return shape(row)
