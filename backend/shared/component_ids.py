"""Component ids across the PLACES_SOURCE flip (AA-847, AA-712 slice 6c, tp-017).

The readers serve catalog component ids (``catalog.v_tour_component.component_id``). Ids from before the flip
(22/10/2026) — every ``add_component`` / ``remove_component`` / ``reorder`` event of an older draft, or an id
posted from a page loaded before it — are ids of the former ``tripplanner.itinerary_components`` table (dropped by
AA-712 X4). ``tripplanner.component_id_map`` (tp-017, filled right before the flip) maps them to catalog ids and
outlives that table: trip_events keep the old ids for ever.

The one rule every assembly reader follows: resolve the component ids it was given through this module before it
reads or compares them. An id the map does not know is kept as it is (it is a catalog id already, or an old id
with no catalog match, which then simply finds no component).
"""
from __future__ import annotations

from typing import Any, Iterable, Optional, Protocol

MAP_SQL = """
    SELECT old_id::text AS old_id, new_id::text AS new_id
    FROM tripplanner.component_id_map
    WHERE old_id = ANY($1::uuid[])
"""


class _Conn(Protocol):
    async def fetch(self, query: str, *args: Any) -> Any: ...


async def id_map(conn: _Conn, ids: Iterable[Optional[str]]) -> dict[str, str]:
    """{old id: catalog id} for the given ids that the map knows."""
    wanted = sorted({str(i) for i in ids if i})
    if not wanted:
        return {}
    return {r["old_id"]: r["new_id"] for r in await conn.fetch(MAP_SQL, wanted)}


def apply(ids: Iterable[Optional[str]], mapping: dict[str, str]) -> list[str]:
    """The ids mapped, in order, duplicates dropped (an old and a new id of one component collapse to one)."""
    out: list[str] = []
    for i in ids:
        if not i:
            continue
        c = mapping.get(str(i), str(i))
        if c not in out:
            out.append(c)
    return out


async def resolve(conn: _Conn, ids: Iterable[Optional[str]]) -> list[str]:
    """The ids as the current source knows them (order kept, duplicates dropped)."""
    ids = list(ids)
    return apply(ids, await id_map(conn, ids))


async def resolve_one(conn: _Conn, component_id: Optional[str]) -> Optional[str]:
    if not component_id:
        return component_id
    return (await resolve(conn, [component_id]) or [component_id])[0]
