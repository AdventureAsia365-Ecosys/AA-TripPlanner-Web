"""Tenant-scoped reads of the CIS catalog tables (published_tours, raw_tours, the atom view).

Those tables enforce tenant RLS (tenant_id = current_setting('app.tenant_id')), so without the AA
tenant they return no rows. Readers (browse search/routes, assembly coverage) and the read-only
extraction checks share these helpers. Moved here from extraction/tour_days.py when the place
writers were removed (AA-712 slice 6a, ADR 0007).
"""
from __future__ import annotations

from backend import config


async def set_catalog_tenant(db) -> None:
    """SET LOCAL app.tenant_id to the AA tenant (CIS catalog RLS). Call inside a transaction."""
    await db.execute("SELECT set_config('app.tenant_id', $1, true)", config.MASTER_CONTENT_TENANT_ID)


async def fetch_catalog(db, sql: str, *args) -> list:
    """Runs one catalog read with the AA tenant set. SET LOCAL inside a transaction, as
    master_content.py does, so it never leaks to other queries on a pooled connection."""
    async with db.transaction():
        await set_catalog_tenant(db)
        return await db.fetch(sql, *args)


ACTIVE_TOURS_SQL = """
    SELECT pt.tour_id::text AS tour_id, pt.aa_name, pt.aa_itineraries, rt.country
    FROM gold_aa_internal.published_tours pt
    JOIN silver_aa_internal.raw_tours rt ON rt.tour_id = pt.tour_id
    WHERE pt.master_status = 'active'
"""
