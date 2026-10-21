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


# The live AA tours (extraction checks). AA-847: read from catalog.v_tour (CIS 225: live = master active, not
# trashed, source active), not silver_aa_internal.raw_tours: the column grant on raw_tours(tour_id, country) (CIS
# 179) is revoked by CIS 254, and backend/tests/test_extraction_ops.py fails if a module reads raw_tours or
# acp_contract.tour_atoms again. Same column names as before. The view needs no tenant (fetch_catalog sets it
# anyway, harmless).
ACTIVE_TOURS_SQL = """
    SELECT tour_id::text AS tour_id, title AS aa_name, itinerary_text AS aa_itineraries, country
    FROM catalog.v_tour
"""
