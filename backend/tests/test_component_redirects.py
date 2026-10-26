"""AA-990 (D1, CIS 285): saved pins follow component-id chains across tripplanner.component_id_map and
catalog.v_component_redirect (backend/shared/component_ids.id_map). Offline: a fake answering per id."""
from __future__ import annotations

import json

from backend.assembly import events
from backend.shared import component_ids


class _ChainConn:
    """Answers MAP_SQL / REDIRECT_SQL from dicts, for the ids asked only; counts the queries."""

    def __init__(self, id_map=None, redirects=None, components=()):
        self.id_map, self.redirects, self.components = id_map or {}, redirects or {}, list(components)
        self.calls: list[tuple[str, list]] = []
        self.events: list[dict] = []

    async def fetch(self, query, *args):
        if query == component_ids.MAP_SQL:
            self.calls.append(("map", list(args[0])))
            return [{"old_id": i, "new_id": self.id_map[i]} for i in args[0] if i in self.id_map]
        if query == component_ids.REDIRECT_SQL:
            self.calls.append(("redirect", list(args[0])))
            return [{"old_id": i, "new_id": self.redirects[i]} for i in args[0] if i in self.redirects]
        if "FROM tripplanner.trip_events" in query:
            return self.events
        if "FROM catalog.v_tour_component" in query:
            return [c for c in self.components if c["id"] in args[0]]
        raise AssertionError(query)


def _u(n: int) -> str:
    return f"{n:08d}-0000-4000-8000-000000000000"


A, B, C, D, E, F, G = (_u(i) for i in range(1, 8))


async def test_a_redirect_chain_is_followed_to_the_end():
    conn = _ChainConn(redirects={A: B, B: C})
    assert await component_ids.id_map(conn, [A, D]) == {A: C}


async def test_the_id_map_and_the_redirects_chain_together_and_the_map_wins_on_the_same_id():
    # pre-flip id A → (map) B; B dropped by a rebuild → (CIS redirect) C; the backfill's insert C → D wins over CIS's
    conn = _ChainConn(id_map={A: B, C: D}, redirects={B: C, C: E})
    assert await component_ids.id_map(conn, [A]) == {A: D}


async def test_at_most_five_hops():
    chain = [_u(10 + i) for i in range(8)]
    conn = _ChainConn(redirects={chain[i]: chain[i + 1] for i in range(7)})
    assert await component_ids.id_map(conn, [chain[0]]) == {chain[0]: chain[component_ids.REDIRECT_HOPS]}
    assert len(conn.calls) == 2 * component_ids.REDIRECT_HOPS


async def test_a_cycle_stops_before_the_id_repeats():
    conn = _ChainConn(redirects={A: B, B: C, C: A})
    assert await component_ids.id_map(conn, [A]) == {A: C}
    conn = _ChainConn(redirects={A: A})
    assert await component_ids.id_map(conn, [A]) == {}


async def test_ids_no_source_knows_cost_one_hop_and_none_for_no_ids():
    conn = _ChainConn()
    assert await component_ids.id_map(conn, [A, B, None, ""]) == {}
    assert conn.calls == [("redirect", [A, B]), ("map", [A, B])]
    conn = _ChainConn()
    assert await component_ids.id_map(conn, [None]) == {} and conn.calls == []


async def test_resolve_collapses_old_and_new_ids_of_one_component():
    conn = _ChainConn(redirects={A: B, B: C})
    assert await component_ids.resolve(conn, [A, C, D]) == [C, D]


async def test_trip_fold_serves_a_pin_whose_id_two_rebuilds_dropped():
    conn = _ChainConn(redirects={A: B, B: C}, components=[
        {"id": C, "name": "Hoi An Ancient Town", "activity": "cultural_heritage", "duration_hint": "full_day",
         "text_extract": "x", "source_tour_id": G, "source_day_index": 2, "destination_id": F,
         "destination_name": "Hoi An", "lat": 15.88, "lng": 108.33, "located_by": "geoapify", "precision": "point"}])
    conn.events = [{"event_type": "add_component", "payload": json.dumps({"component_id": A})}]
    served, unavailable = await events._components_and_missing(conn, "trip")  # noqa: SLF001
    assert [c["id"] for c in served] == [C] and unavailable == []


class _BrokenRedirectConn(_ChainConn):
    """The CIS view is missing / unreadable (before 285, after its rollback, a grant drift); records savepoints."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.savepoints = 0

    def transaction(self):
        outer = self

        class _Tx:
            async def __aenter__(self_inner):
                outer.savepoints += 1
                return self_inner

            async def __aexit__(self_inner, *exc):
                return False
        return _Tx()

    async def fetch(self, query, *args):
        if query == component_ids.REDIRECT_SQL:
            self.calls.append(("redirect", list(args[0])))
            raise RuntimeError('relation "catalog.v_component_redirect" does not exist')
        return await super().fetch(query, *args)


async def test_an_unreadable_redirect_view_falls_back_to_the_id_map(capsys):
    conn = _BrokenRedirectConn(id_map={A: B, B: C})
    assert await component_ids.id_map(conn, [A, D]) == {A: C}
    # read once inside a savepoint, then the rest of the call is id-map only; one log line, no error
    assert [k for k, _ in conn.calls] == ["redirect", "map", "map", "map"] and conn.savepoints == 1
    logged = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    assert logged == [{"evt": "component_redirect_unreadable", "view": "catalog.v_component_redirect",
                       "error": "RuntimeError"}]


async def test_custom_days_follow_the_stop_they_were_added_after():
    # add_component A, add_day(after A); two rebuilds later A → B → C: the extra night sits after C, the served stop
    conn = _ChainConn(redirects={A: B, B: C})
    conn.events = [{"event_type": "add_day", "payload": json.dumps(
                        {"day_id": "d1", "kind": "extra_night", "after_component_id": A})},
                   {"event_type": "add_day", "payload": {"day_id": "d2", "kind": "free_day"}}]
    days = await events.current_custom_days(conn, "trip")
    assert [(d["day_id"], d["after_component_id"]) for d in days] == [("d1", C), ("d2", None)]
    conn.events = [{"event_type": "add_component", "payload": json.dumps({"component_id": A})}]
    conn.components = [{"id": C, "name": "Hoi An Ancient Town", "activity": "cultural_heritage",
                        "duration_hint": "full_day", "text_extract": "x", "source_tour_id": G, "source_day_index": 2,
                        "destination_id": F, "destination_name": "Hoi An", "lat": 15.88, "lng": 108.33,
                        "located_by": "geoapify", "precision": "point"}]
    served, _ = await events._components_and_missing(conn, "trip")  # noqa: SLF001
    assert [c["id"] for c in served] == [days[0]["after_component_id"]]
