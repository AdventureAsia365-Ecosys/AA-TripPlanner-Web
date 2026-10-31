"""Narration cache (AA-805) — reuse a narration for an identical itinerary.

Table tripplanner.narration_cache (migration tp-010). The key hashes only what the narration is
built from: the mode, the prompt version and, per day, the component id, name, text, source
tour day and the AA master day text merged in for that day (so CIS edits miss the cache). No traveller data goes in, so one entry serves every trip with the same itinerary.

Best-effort both ways: if the table is missing (tp-010 not applied) or unreadable, `get` returns
None and `put` does nothing, so narration still works — it just calls the model.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Optional, Protocol

from backend import config
from backend.assembly import master_content
from backend.shared import observability

# Bump when the narration prompts (agent.py), grounding or the master-day choice
# (master_content.resolve_days) change, so old text is not reused.
# 2026-10-16.1 (AA-819): a tour day that does not visit the stop moves to a free day of the same
# tour that does; multi-line master days survive the merge with AI-written days.
PROMPT_VERSION = "2026-10-16.1"

CACHEABLE_SOURCES = ("llm", "mixed")


class _Conn(Protocol):
    async def execute(self, query: str, *args: Any) -> Any: ...
    async def fetchrow(self, query: str, *args: Any) -> Any: ...


def itinerary_hash(itinerary: list[dict], mode: str,
                   master_texts: Optional[dict[tuple[str, int], str]] = None) -> str:
    """Key for one itinerary's narration. `master_texts` is the AA master day text map
    (master_content.load_for_tours): the authored text each entry would use goes into the key,
    so a CIS edit to a tour's itinerary misses the cache instead of serving a stale 'mixed'
    narration until the TTL ends (AA-805 review)."""
    # The day text the narration really uses for each trip day, which may be another day of the
    # same tour when the entry's own day does not visit the stop (AA-819, resolve_days).
    resolved = master_content.resolve_days(itinerary, master_texts or {})
    days = [
        [e.get("day"), str(e.get("component_id")), e.get("name") or "", e.get("text_extract") or "",
         str(e.get("source_tour_id") or ""), e.get("source_day_index"),
         resolved.get(e.get("day")) or ""]
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
        observability.emit("narration_cache_skipped", op="read", detail=str(e)[:500])
        return None
    return {"narration": row["narration"], "source": row["source"]} if row else None


async def put(conn: _Conn, key: str, mode: str, narration: str, source: str, days: int) -> None:
    """Store a narration that used the model. Master-only text is never cached: the handler answers it
    before the cache lookup (AA-1015, S262), since it is built in memory from the day texts the key needs
    anyway, so a cache row would save nothing (and the tp-010 CHECK allows only 'llm' / 'mixed')."""
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
        observability.emit("narration_cache_skipped", op="write", detail=str(e)[:500])
