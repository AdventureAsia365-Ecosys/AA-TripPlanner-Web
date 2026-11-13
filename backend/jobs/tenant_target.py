"""AA-1093 — which tenant an erasure, backup or restore drill job is about, and what CIS says about it.

* :func:`resolve` turns a job payload's ``tenant_id`` (a uuid) or ``tenant_slug`` (resolved through
  ``catalog.v_tenant_tripplanner_v1``, CIS 379, SELECT to tripplanner) into the canonical tenant id. Exactly one of
  the two; anything else is a :class:`~backend.jobs.runner.PermanentError` with a fixed message (no value echoed).
* :func:`markers` reads whether CIS marks the tenant as the pilot or as synthetic (an E2E / canary tenant), from the
  first readable source, feature-detected by column so the code ships before CIS does:
    1. ``catalog.v_tenant_erasure_v1`` (contract tenant.erasure.v1, the saga's request view; CIS lane of AA-1093)
       when it has ``is_pilot`` and ``is_synthetic``;
    2. ``catalog.v_tenant_account_v1`` (CIS 401 ``is_synthetic``; today granted to booking only) when this role may
       read it.
  None when neither answers: an erasure apply then refuses (``tenant_markers_unknown``, plan decision D8), a DRY RUN
  and a backup go on (they delete nothing).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from backend.jobs.runner import PermanentError
from backend.shared import tenant

FEED_VIEW = "catalog.v_tenant_erasure_v1"
ACCOUNT_VIEW = "catalog.v_tenant_account_v1"
SLUG_VIEW = "catalog.v_tenant_tripplanner_v1"
MARKER_COLUMNS = ("is_pilot", "is_synthetic")

# Readable columns of a relation, or none when it does not exist or this role may not SELECT it. to_regclass gives NULL
# for a missing view, so the query never raises on a CIS migration that is not applied yet.
COLUMNS_SQL = """
    SELECT a.attname::text AS name
      FROM pg_attribute a
     WHERE a.attrelid = to_regclass($1) AND a.attnum > 0 AND NOT a.attisdropped
       AND has_table_privilege(to_regclass($1), 'SELECT')
     ORDER BY a.attnum
"""
SLUG_SQL = f"SELECT tenant_id::text AS tenant_id FROM {SLUG_VIEW} WHERE slug = $1"


@dataclass(frozen=True)
class Markers:
    is_pilot: bool
    is_synthetic: bool
    source: str


async def columns(conn: Any, relation: str) -> list[str]:
    return [r["name"] for r in await conn.fetch(COLUMNS_SQL, relation)]


async def resolve(conn: Any, payload: dict[str, Any]) -> str:
    has_id, has_slug = payload.get("tenant_id") is not None, payload.get("tenant_slug") is not None
    if has_id == has_slug:
        raise PermanentError("exactly one of tenant_id or tenant_slug is required")
    if has_id:
        try:
            return tenant.canonical(payload["tenant_id"])
        except tenant.InvalidTenant:
            raise PermanentError("tenant_id must be a uuid") from None
    slug = payload["tenant_slug"]
    if not isinstance(slug, str) or not slug.strip():
        raise PermanentError("tenant_slug must be a non-empty string")
    if "slug" not in await columns(conn, SLUG_VIEW):
        raise PermanentError(f"{SLUG_VIEW} is not readable: pass tenant_id")
    found = await conn.fetchval(SLUG_SQL, slug.strip())
    if found is None:
        raise PermanentError("no tenant has that slug")
    return tenant.canonical(found)


async def markers(conn: Any, tenant_id: str) -> Optional[Markers]:
    for relation in (FEED_VIEW, ACCOUNT_VIEW):
        cols = await columns(conn, relation)
        if all(c in cols for c in (*MARKER_COLUMNS, "tenant_id")):
            row = await conn.fetchrow(
                f"SELECT bool_or(is_pilot) AS is_pilot, bool_and(is_synthetic) AS is_synthetic "
                f"FROM {relation} WHERE tenant_id = $1::uuid", tenant_id)
            if row is not None and row["is_pilot"] is not None and row["is_synthetic"] is not None:
                return Markers(bool(row["is_pilot"]), bool(row["is_synthetic"]), relation)
    return None
