"""AA-986 L6 (root ADR 0015 decision 5): a saved trip whose tour AA no longer serves.

catalog.v_tour_component serves live tours only, so a pinned component of a retired or paused tour is no longer
returned. GET /trip lists such stops apart (available: false + reason) instead of dropping them without a word,
the projection keeps their name and tour key across later edits, the traveller can remove them, and the hand-off
still goes with the flag. The tombstone view (catalog.v_tour_retired, CIS 274) may be missing: the answer then
degrades to `not_served`, never to an error.
"""
from __future__ import annotations

import json

import jsonschema
import pytest

from backend import config
from backend.assembly import availability, events, notify
from backend.tests.test_assembly import CATALOG, S1, T1, FakeConn, _session, call

GONE = "src-gone"          # retired tour key
PAUSED = "src-paused"      # Master inactive
LIVE = "src-live"          # still served

SCHEMA = json.loads(
    (__import__("pathlib").Path(__file__).parent / "contracts" / "inquiry.submitted.v1.4.json").read_text())


def _norm(q: str) -> str:
    return " ".join(q.split())


class CatalogConn(FakeConn):
    """FakeConn + the catalog availability reads and the stored projection read.

    `hidden` = component ids the catalog no longer serves (v_tour_component leaves them out);
    `served` = keys of catalog.v_tour; `tombstone` = {key: (state, reason)}; `tombstone_error` raises on its read."""

    def __init__(self, *, hidden=(), served=(LIVE,), tombstone=None, tombstone_error=None):
        super().__init__()
        self.hidden = set(hidden)
        self.served = set(served)
        self.tombstone = tombstone or {}
        self.tombstone_error = tombstone_error

    async def fetch(self, query, *args):
        q = _norm(query)
        if q == _norm(availability.SERVED_SQL):
            return [{"source_id": k} for k in args[0] if k in self.served]
        if q == _norm(availability.TOMBSTONE_SQL):
            if self.tombstone_error:
                raise self.tombstone_error
            return [{"source_id": k, "state": s, "reason": r}
                    for k, (s, r) in self.tombstone.items() if k in args[0]]
        if q.startswith("SELECT c.component_id AS id, c.name, c.activity"):
            return [dict(CATALOG[i]) for i in args[0] if i in CATALOG and i not in self.hidden]
        return await super().fetch(query, *args)

    async def fetchrow(self, query, *args):
        if _norm(query) == _norm(events.PROJECTION_SQL):
            d = self.drafts.get(args[0])
            return {"itinerary": d["itinerary"]} if d else None
        return await super().fetchrow(query, *args)


@pytest.fixture(autouse=True)
def _tour_keys(monkeypatch):
    """c1 + c2 belong to a tour that will be retired, c3 to a live one."""
    monkeypatch.setitem(CATALOG, "c1", {**CATALOG["c1"], "source_tour_id": GONE, "source_day_index": 1,
                                        "destination_name": "Hanoi"})
    monkeypatch.setitem(CATALOG, "c2", {**CATALOG["c2"], "source_tour_id": GONE, "source_day_index": 2,
                                        "destination_name": "Sapa"})
    monkeypatch.setitem(CATALOG, "c3", {**CATALOG["c3"], "source_tour_id": LIVE, "source_day_index": 1,
                                        "destination_name": "Hoi An"})


async def _trip(conn, ids=("c1", "c2", "c3")):
    conn.sessions[S1] = _session(S1)
    for cid in ids:
        await events.append_event(conn, T1, S1, "add_component", {"component_id": cid})


async def _get(conn):
    res = await call("GET", f"/trip/{T1}", {}, conn=conn)
    assert res["statusCode"] == 200
    return json.loads(res["body"])


async def test_every_stop_served_lists_none_unavailable():
    conn = CatalogConn()
    await _trip(conn)
    body = await _get(conn)
    assert [e["available"] for e in body["itinerary"]] == [True, True, True]
    assert all(e["reason"] is None for e in body["itinerary"])
    assert body["unavailable_stops"] == []
    # nothing missing → the availability views are not read at all
    assert not any("v_tour_retired" in q for q in conn.sql)


async def test_retired_tour_stops_are_listed_apart_with_reason():
    conn = CatalogConn()
    await _trip(conn)
    conn.hidden = {"c1", "c2"}
    conn.tombstone = {GONE: ("retired", "source_deleted")}
    body = await _get(conn)
    assert [e["component_id"] for e in body["itinerary"]] == ["c3"]
    stops = body["unavailable_stops"]
    assert [s["component_id"] for s in stops] == ["c1", "c2"]
    assert stops[0] | {} == {
        "component_id": "c1", "day": stops[0]["day"], "name": "Hanoi", "source_tour_id": GONE,
        "source_day_index": 1, "destination_id": None, "destination_name": "Hanoi", "lat": 21.0, "lng": 105.8,
        "source_system": "cis", "source_id": GONE, "available": False, "reason": "retired",
        "reason_detail": "source_deleted"}


@pytest.mark.parametrize("tombstone,served,reason,detail,known", [
    ({GONE: ("paused", "master_inactive")}, {LIVE}, "paused", "master_inactive", True),
    ({GONE: ("superseded", "superseded_pending")}, {LIVE}, "paused", "superseded_pending", True),
    ({}, {LIVE, GONE}, "stop_removed", None, True),          # the tour is served, the stop is not
    ({}, {LIVE}, "not_served", None, False),                 # no tombstone row
], ids=["paused", "superseded-pending", "stop-removed", "no-tombstone"])
async def test_reason_per_tour_state(tombstone, served, reason, detail, known):
    conn = CatalogConn()
    await _trip(conn)
    conn.hidden, conn.tombstone, conn.served = {"c1", "c2"}, tombstone, served
    (s1, _s2) = (await _get(conn))["unavailable_stops"]
    assert (s1["reason"], s1["reason_detail"]) == (reason, detail)
    assert (s1["source_system"], s1["source_id"]) == (("cis", GONE) if known else (None, None))


async def test_missing_tombstone_view_degrades_to_not_served(capsys):
    conn = CatalogConn(tombstone_error=RuntimeError('relation "catalog.v_tour_retired" does not exist'))
    await _trip(conn)
    conn.hidden = {"c1"}
    body = await _get(conn)
    assert [(s["component_id"], s["reason"]) for s in body["unavailable_stops"]] == [("c1", "not_served")]
    assert '"evt": "tour_availability_unreadable"' in capsys.readouterr().out


async def test_stop_keeps_its_name_across_later_edits_and_can_be_removed():
    conn = CatalogConn(tombstone={GONE: ("retired", "master_trashed")})
    await _trip(conn, ("c1", "c3"))
    conn.hidden = {"c1"}
    # a later edit rewrites the projection: c1 is carried over (available: false), not forgotten
    await events.append_event(conn, T1, S1, "add_component", {"component_id": "c2"})
    stored = {e["component_id"]: e for e in json.loads(conn.drafts[T1]["itinerary"])}
    assert stored["c1"]["available"] is False and stored["c1"]["name"] == "Hanoi"
    assert stored["c3"]["available"] is True and stored["c3"]["source_tour_id"] == LIVE
    await events.append_event(conn, T1, S1, "add_component", {"component_id": "c2"})  # twice: still carried
    (stop,) = (await _get(conn))["unavailable_stops"]
    assert (stop["component_id"], stop["name"], stop["reason"]) == ("c1", "Hanoi", "retired")
    # the traveller removes it through the usual route
    res = await call("DELETE", f"/trip/{T1}/components/c1", {"session_id": S1}, conn=conn)
    assert res["statusCode"] == 200
    assert (await _get(conn))["unavailable_stops"] == []
    assert "c1" not in {e["component_id"] for e in json.loads(conn.drafts[T1]["itinerary"])}


async def test_restored_tour_comes_back_as_available():
    conn = CatalogConn(tombstone={GONE: ("paused", "master_inactive")})
    await _trip(conn)
    conn.hidden = {"c1", "c2"}
    assert len((await _get(conn))["unavailable_stops"]) == 2
    conn.hidden, conn.tombstone = set(), {}
    body = await _get(conn)
    assert body["unavailable_stops"] == [] and len(body["itinerary"]) == 3


async def test_stop_saved_before_l6_has_no_snapshot_but_is_still_listed():
    conn = CatalogConn()
    await _trip(conn, ("c1", "c3"))
    # a projection written before L6: no tour key in it
    conn.drafts[T1]["itinerary"] = json.dumps([{"day": 1, "component_id": "c1", "name": "Hanoi", "rationale": None}])
    conn.hidden = {"c1"}
    (stop,) = (await _get(conn))["unavailable_stops"]
    assert (stop["name"], stop["source_tour_id"], stop["reason"]) == ("Hanoi", None, "not_served")


async def test_send_still_hands_off_with_the_flag(monkeypatch):
    monkeypatch.setattr(config, "REQUIRE_REGISTRATION_BEFORE_HANDOFF", True)
    conn = CatalogConn(tombstone={GONE: ("retired", "source_deleted")})
    await _trip(conn)
    conn.hidden = {"c1", "c2"}
    req = {"session_id": S1, "customer": {"name": "Jo", "email": "jo@x.com", "consent": True}}
    res = await call("POST", f"/trip/{T1}/send-to-advisor", req, conn=conn, sender=notify.LoggingSender())
    assert res["statusCode"] == 200
    (row,) = conn.outbox
    p = row["payload"]
    jsonschema.validate(p, SCHEMA)
    days = p["itinerary"]["days"]
    assert [(d["component_id"], d["available"], d["reason"]) for d in days] == [
        ("c3", True, None), ("c1", False, "retired"), ("c2", False, "retired")]
    legs = p["itinerary"]["legs"]
    assert [(leg["source_tour_id"], leg["available"]) for leg in legs] == [(LIVE, True), (GONE, False)]
    gone = legs[1]
    assert (gone["source_system"], gone["source_id"], gone["reason_detail"]) == ("cis", GONE, "source_deleted")
    assert (gone["day_from"], gone["day_to"]) == (1, 2)
