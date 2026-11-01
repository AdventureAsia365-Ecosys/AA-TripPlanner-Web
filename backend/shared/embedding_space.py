"""AA-1026 D5: the embedding space of the column TripPlanner searches (CIS migration 329,
``catalog.v_embedding_space_v1``).

Browse search compares a query vector with ``geo.tour_place.embedding`` (through ``catalog.v_component_embedding``).
Vectors of two embedding spaces (another model, or another dimension) are meaningless to compare, so the query must be
embedded in the space the column is in, one space per query. CIS records that space in its registry
(``shared.embedding_column``) and changes it only by a re-embed + swap; this module reads it.

- ``load_search_space(db)``: the column's space (cached 60 s), ``None`` while the view is missing or not granted (CIS
  329 not applied yet: today's single space, the behaviour before this module) or has no row. Any other read error
  raises ``llm_gateway.AiPaused("space_unreadable")``: search then falls back to keyword ranking (fail closed).
- ``restrict(route, space)``: the route's models of that space only (a fallback model can never answer in another
  space), or ``None`` when TripPlanner cannot embed in it: no route model is in the space, or the dimension is not
  ``config.EMBED_DIM`` (``embed_texts`` only builds the Cohere request at that dimension). The search then answers
  with keyword ranking (reason ``embedding_space_mismatch``) instead of comparing two spaces.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import Any, Optional

from backend import config
from backend.shared import llm_gateway, observability

VIEW = "catalog.v_embedding_space_v1"
SEARCH_COLUMN = ("geo", "tour_place", "embedding")
REASON_MISMATCH = "embedding_space_mismatch"
REASON_UNREADABLE = "space_unreadable"
CACHE_TTL_SECONDS = 60.0

SPACE_SQL = (
    f"SELECT space_key, model_keys, dimension, query_input_type FROM {VIEW} "
    "WHERE table_schema = $1 AND table_name = $2 AND column_name = $3"
)


@dataclass(frozen=True)
class SearchSpace:
    space_key: str
    model_keys: tuple
    dimension: int
    query_input_type: Optional[str]


_cache: dict[str, tuple[Optional[SearchSpace], float]] = {}


def invalidate() -> None:
    _cache.clear()


async def load_search_space(db: Any) -> Optional[SearchSpace]:
    hit = _cache.get(VIEW)
    now = time.monotonic()
    if hit and now - hit[1] < CACHE_TTL_SECONDS:
        return hit[0]
    try:
        rows = await db.fetch(SPACE_SQL, *SEARCH_COLUMN)
    except Exception as e:  # noqa: BLE001 — a missing view is "not migrated yet"; anything else fails closed
        if llm_gateway._is_missing_object(e):
            _cache[VIEW] = (None, now)
            return None
        observability.emit("llm_gateway_error", what=REASON_UNREADABLE, error=type(e).__name__)
        raise llm_gateway.AiPaused(REASON_UNREADABLE, str(e)[:200]) from None
    space = None
    if rows:
        r = rows[0]
        space = SearchSpace(str(r["space_key"]), tuple(r["model_keys"] or ()), int(r["dimension"]),
                            r["query_input_type"])
    _cache[VIEW] = (space, now)
    return space


def restrict(route: "llm_gateway.Route", space: Optional[SearchSpace]) -> Optional["llm_gateway.Route"]:
    """The route limited to ``space`` (module docstring); the route unchanged when the space is unknown."""
    if space is None:
        return route
    if space.dimension != config.EMBED_DIM:
        return None
    models = tuple(m for m in route.models if m.key in space.model_keys)
    if not models:
        return None
    return route if models == route.models else replace(route, models=models)


def ledger_signal(space: Optional[SearchSpace]) -> dict:
    """The quality_signal field of a search embed ledger row: the space of the query vector."""
    return {"embedding_space": space.space_key} if space is not None else {}
