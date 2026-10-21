"""AA-674 property test (AA-847): random pin sets on both PLACES_SOURCE paths, on a real Postgres.

Runs only with ``pytest --pg-dsn postgresql://postgres@127.0.0.1:<port>/postgres`` against a throwaway LOCAL
Postgres, on the stand-in of test_places_source_pg.py (two Laos tours, one Vietnam tour; the frozen tables and the
catalog views hold the same components, the id-map links them). A seeded RNG draws 20 pin sets of 1–4 components.
For each set, on each path:

  * coverage answers a leg chain that holds every pin exactly once, each leg on one tour with day_from <= day_to,
    and ``coverable`` is true exactly when there is no gap; every gap carries a reason;
  * both paths give the same verdict for the same places (the id-map makes the pins the same components);
  * a route-mode suggestion (what real tours do next from the last stop) never turns a coverable set into an
    uncoverable one when it is pinned.

Deterministic (seeded); a failure prints the seed and the set.
"""
from __future__ import annotations

import json
import random

import pytest

asyncpg = pytest.importorskip("asyncpg")

from backend import config  # noqa: E402
from backend.assembly import coverage as coverage_mod  # noqa: E402
from backend.assembly import suggestions as suggest_mod  # noqa: E402
from backend.tests.test_places_source_pg import D, pg_dsn, pool  # noqa: E402,F401  (fixtures + stand-in)

SEED = 674
SETS = 20


async def _components(pool):  # noqa: F811
    """(old id, new id, destination) of every component, in a stable order."""
    return [tuple(r) for r in await pool.fetch(
        "SELECT m.old_id::text, m.new_id::text, c.destination_id::text, c.source_tour_id "
        "FROM tripplanner.component_id_map m "
        "JOIN tripplanner.itinerary_components c ON c.id = m.old_id ORDER BY c.source_tour_id, c.source_day_index, "
        "c.name")]


async def _pin(pool, trip, ids):  # noqa: F811
    for cid in ids:
        await pool.execute("INSERT INTO tripplanner.trip_events (trip_id, event_type, payload) VALUES ($1, "
                           "'add_component', $2::jsonb)", trip, json.dumps({"component_id": cid}))


def _check_chain(cov, n_pins, ctx):
    pins = [p for leg in cov["legs"] for p in leg["pins"]]
    assert len(pins) == len(set(pins)) == n_pins, ctx
    for leg in cov["legs"]:
        assert leg["day_from"] <= leg["day_to"] and leg["days"] == leg["day_to"] - leg["day_from"] + 1, ctx
    assert cov["coverable"] == (not cov["gaps"]), ctx
    assert all(g.get("reason") for g in cov["gaps"]), ctx


async def test_random_pin_sets_are_covered_or_blocked_with_a_reason(pool, monkeypatch):  # noqa: F811
    comps = await _components(pool)
    rng = random.Random(SEED)
    verdicts = {"coverable": 0, "blocked": 0, "suggested_and_kept": 0}
    for i in range(SETS):
        # every other set from one tour's components (mostly coverable), the rest from the whole catalog
        pool_ = comps if i % 2 else [c for c in comps if c[3] == rng.choice(comps)[3]]
        picked = rng.sample(pool_, min(len(pool_), rng.randint(1, 4)))
        verdict = {}
        for mode, idx in (("legacy", 0), ("cis", 1)):
            monkeypatch.setattr(config, "PLACES_SOURCE", mode)
            trip = f"prop-{SEED}-{i}-{mode}"
            # cis gets the OLD ids for every third set: a draft from before the flip
            ids = [c[0] if (mode == "cis" and i % 3 == 0) else c[idx] for c in picked]
            await _pin(pool, trip, ids)
            cov = await coverage_mod.coverage(pool, trip)
            ctx = {"seed": SEED, "set": i, "mode": mode, "pins": ids, "coverage": cov}
            _check_chain(cov, len(picked), ctx)
            verdict[mode] = cov["coverable"]
            if mode == "cis":
                sug = await suggest_mod.suggest(pool, trip)
                if cov["coverable"] and sug.get("mode") == "route" and sug["suggestions"]:
                    dest = sug["suggestions"][0]["id"]
                    nxt = next(c[1] for c in comps if c[2] == dest)
                    await _pin(pool, trip, [nxt])
                    after = await coverage_mod.coverage(pool, trip)
                    assert after["coverable"], {**ctx, "suggested": dest, "after": after}
                    verdicts["suggested_and_kept"] += 1
        assert verdict["legacy"] == verdict["cis"], {"seed": SEED, "set": i, "picked": picked, "verdict": verdict}
        verdicts["coverable" if verdict["cis"] else "blocked"] += 1
    # the draw exercises both outcomes and the suggestion check (seeded: stable)
    assert verdicts["coverable"] and verdicts["blocked"] and verdicts["suggested_and_kept"], verdicts
    print("AA-674 property:", verdicts)
