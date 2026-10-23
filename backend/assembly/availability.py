"""AA-986 L6 (root ADR 0015 decision 5, TripPlanner): why a saved-trip stop is no longer served.

A trip is folded from its event log, but the components are read from ``catalog.v_tour_component``, which serves
live tours only. So a stop whose tour CIS retired or paused simply drops out of the itinerary. This module says
why, per tour key, so ``GET /trip/{id}`` can list those stops as ``available: false`` with a reason and the
hand-off to AA-Booking can carry the same flag.

Sources (CIS contract views only, contract ``catalog.tour.v1.3``):

- ``catalog.v_tour`` — the served tours (``source_id`` = the catalog key);
- ``catalog.v_tour_retired`` (CIS 274) — the tombstone: one row per key whose state is
  ``retired``, ``paused`` or ``restored`` (CIS 274), with ``state`` and ``reason``.

Reasons, in the order they are decided:

- ``stop_removed`` — the tour is still served, but this stop is no longer part of it (CIS rebuilt its places);
- ``retired``      — the tombstone says ``retired`` (ADR 0015: deleted at the source, Master trashed, or an orphan
  supersede) — "no longer offered";
- ``paused``       — any other tombstone state (Master inactive, or a supersede waiting for the new Master) —
  "temporarily unavailable";
- ``not_served``   — anything else: no tombstone row, a stop saved before the key was recorded, or the tombstone
  view cannot be read.

Degrades safely: each catalog read runs in its own savepoint and an error (view missing before CIS 274 is
applied, grant missing, column renamed) only turns the answer into ``not_served``; it never fails the trip read or
the hand-off. One ``{"evt": "tour_availability_unreadable"}`` line names the view and the error type.
"""
from __future__ import annotations

import json
from typing import Any, Iterable, Optional, Protocol

REASONS = ("stop_removed", "retired", "paused", "not_served")

TOMBSTONE_SQL = """
    SELECT source_id, state, reason
    FROM catalog.v_tour_retired
    WHERE source_system = 'cis' AND source_id = ANY($1::text[])
"""
SERVED_SQL = """
    SELECT source_id FROM catalog.v_tour WHERE source_id = ANY($1::text[])
"""


class _Conn(Protocol):
    async def fetch(self, query: str, *args: Any) -> Any: ...


async def _read(conn: _Conn, sql: str, keys: list[str], view: str) -> Optional[list]:
    """Rows of one catalog read, or None when it cannot be read. Inside a savepoint when the connection has
    transactions, so a failed read never aborts the caller's transaction (the hand-off holds one)."""
    try:
        if hasattr(conn, "transaction"):
            async with conn.transaction():
                return list(await conn.fetch(sql, keys))
        return list(await conn.fetch(sql, keys))
    except Exception as exc:  # noqa: BLE001 — a saved trip and a hand-off never fail over the catalog
        print(json.dumps({"evt": "tour_availability_unreadable", "view": view, "error": type(exc).__name__}))
        return None


async def tour_reasons(conn: _Conn, keys: Iterable[Optional[str]]) -> dict[str, dict]:
    """{tour key: {"served": bool, "reason": str | None, "reason_detail": str | None, "tombstone": bool}} for each
    distinct non-empty key. ``reason`` is None for a served key (the caller turns a missing stop of a served tour
    into ``stop_removed``); ``reason_detail`` is the CIS reason (``source_deleted``, ``master_inactive``…);
    ``tombstone`` says the key is a known catalog key that left (so the hand-off can name it)."""
    wanted = sorted({str(k) for k in keys if k})
    if not wanted:
        return {}
    served_rows = await _read(conn, SERVED_SQL, wanted, "catalog.v_tour")
    served = {r["source_id"] for r in served_rows or []}
    tomb_rows = await _read(conn, TOMBSTONE_SQL, [k for k in wanted if k not in served], "catalog.v_tour_retired") \
        if len(served) < len(wanted) else []
    tomb = {r["source_id"]: r for r in tomb_rows or []}
    out: dict[str, dict] = {}
    for k in wanted:
        if k in served:
            out[k] = {"served": True, "reason": None, "reason_detail": None, "tombstone": False}
        elif k in tomb:
            state = tomb[k]["state"]
            out[k] = {"served": False, "reason": "retired" if state == "retired" else "paused",
                      "reason_detail": tomb[k]["reason"], "tombstone": True}
        else:
            out[k] = {"served": False, "reason": "not_served", "reason_detail": None, "tombstone": False}
    return out


def stop_reason(key: Optional[str], reasons: dict[str, dict]) -> tuple[str, Optional[str]]:
    """(reason, reason_detail) for a stop that is not in the served itinerary."""
    r = reasons.get(str(key)) if key else None
    if r is None:
        return "not_served", None
    if r["served"]:
        return "stop_removed", None
    return r["reason"], r["reason_detail"]
