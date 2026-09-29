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
        self.calls.append(("taste", args))
        return self.taste_rows


def _cand(i, name, lat, lng, **kw):
    return {"id": i, "name": name, "lat": lat, "lng": lng, "country": "Bhutan", "component_count": 2, **kw}


async def _suggest(monkeypatch, conn):
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
    assert [c[0] for c in conn.calls] == ["route", "taste"]
    assert conn.calls[1][1][2] == ["Bhutan"]
    assert out["suggestions"][0]["tour_count"] == 0
