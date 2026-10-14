"""Narration cache (AA-805) — reuse a narration for an identical itinerary.

Table tripplanner.narration_cache (migration tp-010). The key hashes only what the narration is
built from: the mode, the prompt version and, per day, the component id, name, text and source
tour day. No traveller data goes in, so one entry serves every trip with the same itinerary.

Best-effort both ways: if the table is missing (tp-010 not applied) or unreadable, `get` returns
None and `put` does nothing, so narration still works — it just calls the model.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Optional, Protocol

from backend import config

# Bump when the narration prompts (agent.py) or grounding change, so old text is not reused.
PROMPT_VERSION = "2026-10-14.1"

CACHEABLE_SOURCES = ("llm", "mixed")


class _Conn(Protocol):
    async def execute(self, query: str, *args: Any) -> Any: ...
    async def fetchrow(self, query: str, *args: Any) -> Any: ...


def itinerary_hash(itinerary: list[dict], mode: str) -> str:
    days = [
        [e.get("day"), str(e.get("component_id")), e.get("name") or "", e.get("text_extract") or "",
         str(e.get("source_tour_id") or ""), e.get("source_day_index")]
        for e in sorted(itinerary, key=lambda e: (e.get("day") or 0, str(e.get("component_id"))))
    ]
    raw = json.dumps({"v": PROMPT_VERSION, "mode": mode, "days": days}, sort_keys=True,
                     separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def get(conn: _Conn, key: str) -> Optional[dict]:
    """The cached {narration, source} for `key`, if fresh; counts the hit."""
    try:
        row = await conn.fetchrow(
            "UPDATE tripplanner.narration_cache SET hits = hits + 1, last_hit_at = now() "
            "WHERE itinerary_hash = $1 AND created_at > now() - make_interval(hours => $2::int) "
            "RETURNING narration, source",
            key, config.NARRATION_CACHE_TTL_HOURS,
        )
    except Exception as e:  # noqa: BLE001 — tp-010 not applied, permission, ...
        print(f"[narration_cache] read skipped: {e}")
        return None
    return {"narration": row["narration"], "source": row["source"]} if row else None


async def put(conn: _Conn, key: str, mode: str, narration: str, source: str, days: int) -> None:
    """Store a narration that used the model. Master-only text is never cached (it is cheap and
    must follow CIS edits at once)."""
    if source not in CACHEABLE_SOURCES or not narration:
        return
    try:
        await conn.execute(
            "INSERT INTO tripplanner.narration_cache (itinerary_hash, mode, narration, source, days) "
            "VALUES ($1, $2, $3, $4, $5) "
            "ON CONFLICT (itinerary_hash) DO UPDATE SET narration = EXCLUDED.narration, "
            "source = EXCLUDED.source, days = EXCLUDED.days, created_at = now(), hits = 0, "
            "last_hit_at = NULL",
            key, mode, narration, source, days,
        )
    except Exception as e:  # noqa: BLE001
        print(f"[narration_cache] write skipped: {e}")
