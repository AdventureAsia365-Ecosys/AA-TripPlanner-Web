"""Suggest what to pin next.

Given a trip's currently pinned components, recommend a few more
destinations the traveller is likely to want — ones that match the taste of
what they've already pinned, without repeating a destination already in the trip.

Route-constrained (AA-674, PR-11): every trip should stay coverable by tours we
actually sell, possibly several chained. So the candidates are what real tours do
NEXT from where the trip ends:
  - take the Tour Graph stops (tripplanner.tour_stop, AA-673) within JUNCTION_KM
    of the trip's last stop (the same place, or a transfer to another tour);
  - for each tour stopping there, the components of the days that follow, up to
    its next stop somewhere else, are the candidates.
Then the ranking is unchanged: taste (average embedding of the pinned
components, pgvector <=>) blended with proximity to the last stop.

When no tour passes near the last stop (the graph does not cover it yet), it
falls back to the previous behaviour: taste within the trip's countries.

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

# A tour stop this close to the trip's last stop can continue the trip (same place
# or a transfer). The Tour Graph builds its junctions with the same default.
JUNCTION_KM = 150.0

_KM_SQL = ("2 * 6371 * asin(sqrt(power(sin(radians(n.lat - $2) / 2), 2) + cos(radians($2)) "
           "* cos(radians(n.lat)) * power(sin(radians(n.lng - $3) / 2), 2)))")

# $1 taste vector, $2/$3 last stop lat/lng, $4 junction km, $5 pinned destination ids, $6 limit.
ROUTE_CANDIDATES_SQL = f"""
    WITH near AS (
        SELECT n.destination_id, {_KM_SQL} AS transfer_km
        FROM tripplanner.tour_graph_node n
        WHERE {_KM_SQL} <= $4
    ), here AS (
        SELECT s.source_tour_id, s.day_index, s.destination_id, nr.transfer_km
        FROM tripplanner.tour_stop s JOIN near nr ON nr.destination_id = s.destination_id
    ), nxt AS (
        -- the tour's next stop somewhere else (a stay of several nights is skipped over)
        SELECT h.source_tour_id, h.day_index AS from_day, h.transfer_km,
               (SELECT min(s2.day_index) FROM tripplanner.tour_stop s2
                WHERE s2.source_tour_id = h.source_tour_id AND s2.day_index > h.day_index
                  AND s2.destination_id <> h.destination_id) AS to_day
        FROM here h
    )
    SELECT d.id, d.name, d.lat, d.lng, d.country,
           COUNT(DISTINCT c.id) AS component_count,
           MIN(c.embedding <=> $1::vector) AS best_distance,
           MIN(nx.transfer_km) AS transfer_km,
           COUNT(DISTINCT nx.source_tour_id) AS tour_count
    FROM nxt nx
    JOIN tripplanner.itinerary_components c
      ON c.source_tour_id = nx.source_tour_id
     AND c.source_day_index > nx.from_day AND c.source_day_index <= nx.to_day
    JOIN shared.destinations d ON d.id = c.destination_id
    WHERE nx.to_day IS NOT NULL AND c.embedding IS NOT NULL AND d.id <> ALL($5::uuid[])
    GROUP BY d.id, d.name, d.lat, d.lng, d.country
    ORDER BY best_distance ASC
    LIMIT $6
"""


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

    dest_exclude = list(pinned_dest_ids) or ["00000000-0000-0000-0000-000000000000"]
    candidate_limit = int(limit) * CANDIDATE_MULTIPLIER
    last_stop = _last_stop_coord(components)

    # Route-constrained first: what real tours do next from here (AA-674).
    if last_stop is not None:
        route = [dict(r) for r in await conn.fetch(
            ROUTE_CANDIDATES_SQL, taste, last_stop[0], last_stop[1], JUNCTION_KM,
            dest_exclude, candidate_limit)]
        if route:
            return {"suggestions": _rerank_by_route(route, last_stop, int(limit)), "mode": "route"}

    # Fallback: rank destinations (excluding already-pinned) by their best component's
    # cosine distance to the taste vector, biased to the trip's countries.
    # If the trip touches no known country, fall back to global ranking. Pull
    # a WIDER pool than we return so the geography re-rank below has room to
    # promote a nearby-but-slightly-less-similar place.
    country_filter = "AND d.country = ANY($3::text[])" if countries else ""
    params: list[Any] = [taste, dest_exclude]
    if countries:
        params.append(countries)
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
        return {"suggestions": [], "mode": "taste"}

    ranked = _rerank_by_route(candidates, last_stop, int(limit))
    return {"suggestions": ranked, "mode": "taste"}


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
        if c.get("tour_count"):
            tours = int(c["tour_count"])
            why = f"Next on {tours} Adventure Asia tour{'s' if tours > 1 else ''} from your last stop"
            if (c.get("transfer_km") or 0) > 1:
                why += f" (a {round(float(c['transfer_km']))} km transfer)"
        elif last_stop is not None and dist is not None and dist != float("inf"):
            why = "Similar to your trip — and close to your last stop"
        out.append(
            {
                "id": str(c["id"]),
                "name": c["name"],
                "lat": c["lat"],
                "lng": c["lng"],
                "country": c["country"],
                "component_count": c["component_count"],
                "tour_count": int(c.get("tour_count") or 0),
                "why": why,
            }
        )
    return out
