"""Suggest what to pin next.

Given a trip's currently pinned components, recommend a few more
destinations the traveller is likely to want — ones that match the taste of
what they've already pinned and sit in the same countries, without repeating
a destination already in the trip.

Approach (reuses existing infra, no new services):
  - Read the pinned components' embeddings and average them into a single
    "taste" vector.
  - Rank other destinations by cosine distance of their best component to
    that taste vector (pgvector <=>), restricted to the countries the trip
    already touches, excluding destinations already pinned.

This lives in the Assembly Lambda (not Browse): it reads per-visitor trip
state, so it must never be cached across visitors.
"""
from __future__ import annotations

from typing import Any, Optional, Protocol

from backend.assembly import events as events_mod
from backend.assembly import sequencing


class _Conn(Protocol):
    async def fetch(self, query: str, *args: Any) -> Any: ...
    async def fetchrow(self, query: str, *args: Any) -> Any: ...

    def transaction(self) -> Any: ...
    async def execute(self, query: str, *args: Any) -> Any: ...


SUGGEST_LIMIT = 6

# How many taste-ranked candidates to pull before re-ranking by geography.
# A wider candidate pool lets a slightly-less-similar-but-much-closer place
# outrank a marginally-more-similar-but-far one, without abandoning taste.
CANDIDATE_MULTIPLIER = 4

# Blend weights for the final re-rank: taste similarity vs. proximity to the
# last stop on the current route. Taste stays the majority signal (we're
# recommending places the traveller will like); geography pulls a genuinely
# nearby place up so a suggestion reads as "what's next" on the route. Must
# sum to 1.0.
WEIGHT_SIMILARITY = 0.6
WEIGHT_GEOGRAPHY = 0.4

# Absolute-distance scale for the geography score (great-circle km). A stop
# within NEAR_KM of the last one scores ~0 (very "next-door"); beyond FAR_KM
# it saturates at 1 (too far to feel like the next stop). Between the two it
# ramps linearly. Absolute (not pool-relative) so "near" means the same thing
# regardless of which candidates happen to be in the pool.
NEAR_KM = 150.0
FAR_KM = 1500.0


def _rank_norm(index: int, count: int) -> float:
    """Normalise a 0-based rank into [0,1] where 0 = best. With one item,
    returns 0.0 (it's the best by definition)."""
    if count <= 1:
        return 0.0
    return index / (count - 1)


def _geo_score(distance_km: float) -> float:
    """Map an absolute great-circle distance (km) to [0,1] where 0 = right
    next to the last stop and 1 = too far to be "next". Linear ramp between
    NEAR_KM and FAR_KM; clamped outside. Absolute so it doesn't depend on the
    other candidates in the pool."""
    if distance_km <= NEAR_KM:
        return 0.0
    if distance_km >= FAR_KM:
        return 1.0
    return (distance_km - NEAR_KM) / (FAR_KM - NEAR_KM)


def _last_stop_coord(components: list[dict]) -> Optional[tuple[float, float]]:
    """Coordinate of the final stop on the current route.

    Sequences the pinned components the same way the itinerary is built
    (deterministic nearest-neighbor), then takes the last one's lat/lng.
    This is the point a "what's next" suggestion should sit near. Returns
    None when no component carries usable coordinates."""
    usable = [
        c
        for c in components
        if isinstance(c.get("lat"), (int, float))
        and isinstance(c.get("lng"), (int, float))
    ]
    if not usable:
        return None
    ordered = sequencing.sequence(usable)
    last = ordered[-1]
    return (float(last["lat"]), float(last["lng"]))


def _parse_vector(raw: Any) -> list[float]:
    """pgvector comes back from asyncpg as a text literal '[0.1,0.2,...]'
    (no registered codec). Parse it into floats. Accepts an already-parsed
    list too."""
    if isinstance(raw, (list, tuple)):
        return [float(x) for x in raw]
    if isinstance(raw, str):
        s = raw.strip().lstrip("[").rstrip("]")
        if not s:
            return []
        return [float(p) for p in s.split(",")]
    return []


def _avg_vector_literal(vectors: list[list[float]]) -> Optional[str]:
    """Average a list of equal-length float vectors into a pgvector literal
    '[a,b,...]', or None if there's nothing to average."""
    vecs = [v for v in vectors if v]
    if not vecs:
        return None
    dim = len(vecs[0])
    acc = [0.0] * dim
    n = 0
    for v in vecs:
        if len(v) != dim:
            continue
        for i, x in enumerate(v):
            acc[i] += float(x)
        n += 1
    if n == 0:
        return None
    return "[" + ",".join(repr(x / n) for x in acc) + "]"


async def suggest(conn: _Conn, trip_id: str, limit: int = SUGGEST_LIMIT) -> dict:
    """Return {suggestions: [{id, name, lat, lng, component_count, why}]}.

    Empty list when the trip has no pinned components with embeddings (we
    have nothing to base a taste vector on)."""
    components = await events_mod._current_components(conn, trip_id)  # noqa: SLF001
    if not components:
        return {"suggestions": []}

    pinned_dest_ids = {str(c["destination_id"]) for c in components if c.get("destination_id")}
    pinned_component_ids = [str(c["id"]) for c in components]

    # Pull the embeddings of the pinned components to build the taste vector,
    # plus the set of countries the trip already touches (for a same-region
    # bias). Both come from itinerary_components + destinations.
    rows = await conn.fetch(
        """
        SELECT c.embedding, d.country
        FROM tripplanner.itinerary_components c
        JOIN shared.destinations d ON d.id = c.destination_id
        WHERE c.id = ANY($1::uuid[])
        """,
        pinned_component_ids,
    )
    vectors = [
        _parse_vector(r["embedding"]) for r in rows if r["embedding"] is not None
    ]
    vectors = [v for v in vectors if v]
    countries = sorted({r["country"] for r in rows if r["country"]})
    taste = _avg_vector_literal(vectors)
    if taste is None:
        # No embeddings to reason about — nothing to suggest.
        return {"suggestions": []}

    # Rank destinations (excluding already-pinned) by their best component's
    # cosine distance to the taste vector, biased to the trip's countries.
    # If the trip touches no known country, fall back to global ranking. Pull
    # a WIDER pool than we return so the geography re-rank below has room to
    # promote a nearby-but-slightly-less-similar place.
    dest_exclude = list(pinned_dest_ids) or ["00000000-0000-0000-0000-000000000000"]
    country_filter = "AND d.country = ANY($3::text[])" if countries else ""
    params: list[Any] = [taste, dest_exclude]
    if countries:
        params.append(countries)
    candidate_limit = int(limit) * CANDIDATE_MULTIPLIER
    sql = f"""
        SELECT d.id, d.name, d.lat, d.lng, d.country,
               COUNT(c.id) AS component_count,
               MIN(c.embedding <=> $1::vector) AS best_distance
        FROM shared.destinations d
        JOIN tripplanner.itinerary_components c ON c.destination_id = d.id
        WHERE c.embedding IS NOT NULL
          AND d.id <> ALL($2::uuid[])
          {country_filter}
        GROUP BY d.id, d.name, d.lat, d.lng, d.country
        ORDER BY best_distance ASC
        LIMIT {candidate_limit}
    """
    candidates = [dict(r) for r in await conn.fetch(sql, *params)]
    if not candidates:
        return {"suggestions": []}

    ranked = _rerank_by_route(candidates, _last_stop_coord(components), int(limit))
    return {"suggestions": ranked}


def _rerank_by_route(
    candidates: list[dict],
    last_stop: Optional[tuple[float, float]],
    limit: int,
) -> list[dict]:
    """Blend taste similarity (candidate order, already sorted best-first) with
    proximity to the last stop, then return the top `limit` as suggestion dicts.

    Without a last-stop anchor (no usable coordinates) this degrades to pure
    taste order — identical to the previous behaviour."""
    count = len(candidates)

    if last_stop is not None:
        dists: list[Optional[float]] = [
            sequencing._haversine(  # noqa: SLF001 — internal reuse within the package
                last_stop[0], last_stop[1], float(c["lat"]), float(c["lng"])
            )
            if isinstance(c.get("lat"), (int, float))
            and isinstance(c.get("lng"), (int, float))
            else None
            for c in candidates
        ]
        # Missing coords can't be judged on proximity -> treat as "far" (1.0).
        geo_all = [_geo_score(d) if d is not None else 1.0 for d in dists]
    else:
        dists = [None] * count
        geo_all = [0.0] * count

    scored = []
    for i, c in enumerate(candidates):
        sim = _rank_norm(i, count)  # taste rank, 0 = most similar
        geo = geo_all[i]  # 0 = closest to last stop
        blended = WEIGHT_SIMILARITY * sim + WEIGHT_GEOGRAPHY * geo
        scored.append((blended, i, c, dists[i]))

    # sort by blended score asc (lower = better); original index breaks ties
    # so the result stays deterministic for a given candidate set.
    scored.sort(key=lambda t: (t[0], t[1]))

    out: list[dict] = []
    for _, _, c, dist in scored[:limit]:
        why = "Similar to places you've pinned"
        if last_stop is not None and dist is not None and dist != float("inf"):
            why = "Similar to your trip — and close to your last stop"
        out.append(
            {
                "id": str(c["id"]),
                "name": c["name"],
                "lat": c["lat"],
                "lng": c["lng"],
                "country": c["country"],
                "component_count": c["component_count"],
                "why": why,
            }
        )
    return out
