"""Component ids across the PLACES_SOURCE flip (AA-847, AA-712 slice 6c, tp-017).

The readers serve catalog component ids (``catalog.v_tour_component.component_id``). Ids from before the flip
(22/10/2026) — every ``add_component`` / ``remove_component`` / ``reorder`` event of an older draft, or an id
posted from a page loaded before it — are ids of the former ``tripplanner.itinerary_components`` table (dropped by
AA-712 X4). ``tripplanner.component_id_map`` (tp-017, filled right before the flip) maps them to catalog ids and
outlives that table: trip_events keep the old ids for ever.

AA-990 (D1, CIS 285): a CIS place rebuild can drop a component id (the place was respelt, retyped, moved or merged).
CIS records where it went in geo.component_redirect, served as ``catalog.v_component_redirect``
(component_id → new_component_id). A later rebuild that drops the target adds target → newer id, and the one-time
AA-CIS-Infra backfill (aa990_component_id_backfill.py) only INSERTs into component_id_map (tp-017: insert-only), so a
pin can sit at the start of a chain that crosses both sources. ``id_map`` follows it: at each hop the id map first
(TripPlanner's own rows), else the CIS redirect; at most REDIRECT_HOPS hops; a cycle stops at the last id before it
repeats.

Degrades safely: the redirect read runs in its own savepoint (the hand-off holds a transaction) and an error (view
missing before CIS 285 is applied or after its rollback, a grant drift) logs one ``{"evt":
"component_redirect_unreadable"}`` line and resolves that call through the id map only, as before AA-990.

The one rule every assembly reader follows: resolve the component ids it was given through this module before it
reads or compares them. An id neither source knows is kept as it is (it is a catalog id already, or an old id
with no catalog match, which then simply finds no component).
"""
from __future__ import annotations

import json
from typing import Any, Iterable, Optional, Protocol

MAP_SQL = """
    SELECT old_id::text AS old_id, new_id::text AS new_id
    FROM tripplanner.component_id_map
    WHERE old_id = ANY($1::uuid[])
"""
# AA-990: CIS 285 (catalog contract view, SELECT to tripplanner).
REDIRECT_SQL = """
    SELECT component_id::text AS old_id, new_component_id::text AS new_id
    FROM catalog.v_component_redirect
    WHERE component_id = ANY($1::uuid[])
"""
REDIRECT_HOPS = 5


class _Conn(Protocol):
    async def fetch(self, query: str, *args: Any) -> Any: ...


async def _redirect_rows(conn: _Conn, frontier: list[str]) -> Optional[list]:
    """Rows of the CIS redirect view, or None when it cannot be read. Inside a savepoint when the connection has
    transactions, so a failed read never aborts the caller's transaction (as availability._read)."""
    try:
        if hasattr(conn, "transaction"):
            async with conn.transaction():
                return list(await conn.fetch(REDIRECT_SQL, frontier))
        return list(await conn.fetch(REDIRECT_SQL, frontier))
    except Exception as exc:  # noqa: BLE001 — a saved trip and a hand-off never fail over the catalog
        print(json.dumps({"evt": "component_redirect_unreadable", "view": "catalog.v_component_redirect",
                          "error": type(exc).__name__}))
        return None


async def _next_hop(conn: _Conn, frontier: list[str], state: dict) -> dict[str, str]:
    """{id: next id} for the frontier: the id map wins over the CIS redirect of the same id. ``state["redirects"]``
    turns False after the first unreadable redirect read: the rest of the call uses the id map only."""
    want = set(frontier)
    hop: dict[str, str] = {}
    if state["redirects"]:
        rows = await _redirect_rows(conn, frontier)
        if rows is None:
            state["redirects"] = False
        else:
            hop = {r["old_id"]: r["new_id"] for r in rows if r["old_id"] in want}
    hop.update({r["old_id"]: r["new_id"] for r in await conn.fetch(MAP_SQL, frontier) if r["old_id"] in want})
    return {k: v for k, v in hop.items() if v and v != k}


async def id_map(conn: _Conn, ids: Iterable[Optional[str]]) -> dict[str, str]:
    """{old id: current id} for the given ids that the id map or the CIS redirects move (chains followed, at most
    REDIRECT_HOPS hops, cycle-safe). Two queries per hop; ids no source knows end after the first. An unreadable
    redirect view degrades the call to the id map alone (one log line), never an error."""
    wanted = sorted({str(i) for i in ids if i})
    if not wanted:
        return {}
    current = {w: w for w in wanted}          # raw id → where its chain is now
    seen = {w: {w} for w in wanted}
    open_ = set(wanted)
    state = {"redirects": True}
    for _ in range(REDIRECT_HOPS):
        frontier = sorted({current[w] for w in open_})
        if not frontier:
            break
        hop = await _next_hop(conn, frontier, state)
        still = set()
        for w in open_:
            nxt = hop.get(current[w])
            if nxt is None or nxt in seen[w]:
                continue                       # end of the chain, or a cycle: stop before it repeats
            current[w] = nxt
            seen[w].add(nxt)
            still.add(w)
        open_ = still
    return {w: c for w, c in current.items() if c != w}


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
