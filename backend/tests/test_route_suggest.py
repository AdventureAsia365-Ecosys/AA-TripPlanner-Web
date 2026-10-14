"""AA-674 step 2 — "suggest next" follows real tours from the trip's last stop."""
from __future__ import annotations

from backend.assembly import suggestions as sg

PINNED = [
    {"id": "11111111-1111-1111-1111-111111111111", "destination_id": "d-paro", "lat": 27.43, "lng": 89.41},
    {"id": "22222222-2222-2222-2222-222222222222", "destination_id": "d-thimphu", "lat": 27.47, "lng": 89.64},
]


class _Conn:
    def __init__(self, route_rows, taste_rows):
        self.route_rows, self.taste_rows, self.calls = route_rows, taste_rows, []

    async def fetch(self, sql, *args):
        if "SELECT c.embedding, d.country" in sql:
            return [{"embedding": "[1.0,0.0]", "country": "Bhutan"}, {"embedding": "[0.0,1.0]", "country": "Bhutan"}]
        if "tripplanner.tour_stop" in sql:
            self.calls.append(("route", args))
            return self.route_rows
        # AA-712: index-served top-k passes, then the exact aggregate when they come up short
        self.calls.append(("topk" if "WITH scan AS" in sql else "exact", args))
        return self.taste_rows

    async def execute(self, sql, *args):
        self.calls.append(("execute", args))

    async def fetchval(self, sql, *args):
        return "0.7.4"  # pgvector before iterative scans

    def transaction(self):
        return _Txn()


class _Txn:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _cand(i, name, lat, lng, **kw):
    return {"id": i, "name": name, "lat": lat, "lng": lng, "country": "Bhutan", "component_count": 2, **kw}


async def _suggest(monkeypatch, conn):
    monkeypatch.setattr(sg.vector_topk, "_iterative", None)

    async def comps(_conn, _trip):
        return PINNED
    monkeypatch.setattr(sg.events_mod, "_current_components", comps)
    return await sg.suggest(conn, "trip-1", limit=3)


async def test_route_candidates_come_from_tours_passing_the_last_stop(monkeypatch):
    conn = _Conn([_cand("d-punakha", "Punakha", 27.59, 89.87, tour_count=3, transfer_km=0.0),
                  _cand("d-haa", "Haa", 27.37, 89.29, tour_count=1, transfer_km=22.4)], [])
    out = await _suggest(monkeypatch, conn)
    assert out["mode"] == "route"
    assert [c[0] for c in conn.calls] == ["route"]               # no fallback query
    _, args = conn.calls[0]
    assert (args[1], args[2], args[3]) == (27.47, 89.64, sg.JUNCTION_KM)   # last stop = Thimphu (sequenced)
    assert sorted(args[4]) == ["d-paro", "d-thimphu"]             # pinned places excluded
    by_id = {s["id"]: s for s in out["suggestions"]}
    assert by_id["d-punakha"]["why"] == "Next on 3 Adventure Asia tours from your last stop"
    assert by_id["d-haa"]["why"] == "Next on 1 Adventure Asia tour from your last stop (a 22 km transfer)"
    assert by_id["d-punakha"]["tour_count"] == 3


async def test_falls_back_to_taste_in_the_trip_countries_when_no_tour_passes(monkeypatch):
    conn = _Conn([], [_cand("d-bumthang", "Bumthang", 27.55, 90.75)])
    out = await _suggest(monkeypatch, conn)
    assert out["mode"] == "taste"
    # AA-712 review: inside the trip's countries the exact aggregate runs directly — one query,
    # no top-k passes that a small country (12-row pool here) would never fill
    assert [c[0] for c in conn.calls] == ["route", "exact"]
    assert conn.calls[1][1][2] == ["Bhutan"]
    assert out["suggestions"][0]["tour_count"] == 0


async def test_taste_fallback_without_trip_countries_uses_the_index_passes(monkeypatch):
    conn = _Conn([], [_cand("d-bumthang", "Bumthang", 27.55, 90.75)])

    async def no_country_fetch(sql, *args, _orig=conn.fetch):
        if "SELECT c.embedding, d.country" in sql:
            return [{"embedding": "[1.0,0.0]", "country": None}]
        return await _orig(sql, *args)
    conn.fetch = no_country_fetch
    out = await _suggest(monkeypatch, conn)
    assert out["mode"] == "taste"
    # global ranking: both top-k passes come up short (1 < 12), then the exact aggregate
    assert [c[0] for c in conn.calls] == ["route", "execute", "topk", "execute", "topk", "exact"]
    assert [c[1] for c in conn.calls if c[0] == "execute"] == [("400",), ("1000",)]  # ef_search
    assert conn.calls[2][1][-1] == 200 and conn.calls[4][1][-1] == 1000            # k
