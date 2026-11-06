"""AA-1038 R5 slice 2 (root ADR 0019 d10): which tenant a /{slug} path is, for the BFF.

GET /browse/tenant/{slug} reads the CIS contract view catalog.v_tenant_tripplanner_v1 (CIS 379, tenant.entitlements.v1
1.4, SELECT to role tripplanner): one row per tenant with a TripPlanner subscription. The BFF calls it server-side
(behind the edge key) before it signs a tenant assertion (backend/shared/tenant_assertion.py), and the /{slug} pages
call it to decide between the planner and a 404.

Only a SERVED tenant is answered (in service and tp.hosted live); an unknown slug, a tenant without the add-on, a
suspended tenant and a slug of the wrong shape are all the same 404, so the answer says nothing about which one it was.
The view cannot be read (CIS 379 not applied, grant missing): every slug is a 404 and one log line says why; the bare
path keeps the default tenant, so nothing that works today depends on this view.

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

_TOKEN_RE = re.compile(r"^[a-z][a-z0-9-]{0,39}$")
_COLOUR_RE = re.compile(r"^#[0-9a-f]{6}$")
LOCALES = ("en", "vi")


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
    """The served tenant of `slug`, or None (unknown, not served, wrong shape, or the view unreadable)."""
    if not isinstance(slug, str) or not SLUG_RE.match(slug):
        return None
    try:
        row = await pool.fetchrow(TENANT_SQL, slug)
    except Exception as exc:  # noqa: BLE001 — before CIS 379 (42P01 / 42501): every slug is a 404, never a 500
        print(json.dumps({"evt": "tenant_view_unreadable", "error": type(exc).__name__}))
        return None
    if row is None or not row["served"] or not row["in_service"]:
        return None
    return shape(row)
