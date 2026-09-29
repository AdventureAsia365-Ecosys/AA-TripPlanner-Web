"""AA-675 — tour_day extraction: day parsing, rules, grounding of model output, merge, persist.

No DB, no Mapbox, no Bedrock: the model and geocoder are stubs, the DB is a recorder."""
from __future__ import annotations

import json
from contextlib import asynccontextmanager

from backend.extraction import tour_days as td

# Real shape of CIS aa_itineraries (Ladakh e-bike tour, Dev, 29/09/2026), shortened.
LADAKH = """Day 1 — Arrival in Leh & Team Briefing
Arrive in Leh and transfer to Stok. Rest and acclimatize to the altitude.

Day 2 — Old Leh, Bazaar & First E-Cycle Ride
Morning sightseeing of Old Leh and the bazaar. Afternoon ride around Stok.

Day 3 — Stok to Sakti via Thiksay—48 Kilometers
The route passes Thiksay monastery and continues to Sakti. Overnight stay in Sakti.

Day 4-5 — Nubra Valley
Two days exploring Diskit and Hunder. Stay in Hunder, with camel rides on the dunes.

Day 6 — Departure from Leh
Transfer to Leh airport for your onward flight."""


def test_parse_days_splits_blocks_and_expands_ranges():
    blocks = td.parse_days(LADAKH)
    assert [(b.day_from, b.day_to) for b in blocks] == [(1, 1), (2, 2), (3, 3), (4, 5), (6, 6)]
    assert blocks[2].title.startswith("Stok to Sakti")
    assert "Overnight stay in Sakti" in blocks[2].body


def test_parse_days_without_headers_is_empty():
    assert td.parse_days("Free text with no day headers.") == []


def test_rule_day_a_to_b_and_overnight():
    b = td.parse_days(LADAKH)[2]
    assert td.rule_day(b) == ("Stok", "Sakti", "Sakti")


def test_rule_day_stay_in_with_trailing_clause():
    b = td.parse_days(LADAKH)[3]
    assert td.rule_day(b)[2] == "Hunder"


def test_rule_day_finds_nothing_on_a_theme_title():
    b = td.parse_days(LADAKH)[1]
    assert td.rule_day(b) == (None, None, None)


def test_grounded_is_case_and_punctuation_insensitive_and_word_bounded():
    assert td.grounded("old leh", "Morning sightseeing of Old Leh and the bazaar.")
    assert not td.grounded("Le", "Morning sightseeing of Old Leh")      # no partial words
    assert not td.grounded("Kargil", "Morning sightseeing of Old Leh")
    assert not td.grounded(None, "anything")


def test_ground_llm_day_drops_places_not_in_that_days_text():
    b = td.parse_days(LADAKH)[1]
    g = td.ground_llm_day({"start": "Stok", "end": "Leh", "overnight": "Kargil",
                           "places": ["Old Leh", "Pangong Lake"]}, b)
    assert g == {"start": "Stok", "end": "Leh", "overnight": None, "places": ["Old Leh"]}


def test_merge_prefers_grounded_model_values_and_expands_a_range():
    b = td.parse_days(LADAKH)[3]
    recs = td.merge_day(b, (None, None, "Hunder"),
                        {"start": "Diskit", "end": "Hunder", "overnight": None, "places": ["Diskit"]})
    assert [r.day_index for r in recs] == [4, 5]
    assert recs[0].overnight_place == "Hunder"          # rule fills the model's gap
    assert recs[0].start_place == "Diskit" and recs[0].places == []  # start not repeated in places
    assert recs[0].extracted_by == "rule+llm"


def test_needs_model_ignores_the_last_day_without_overnight():
    blocks = td.parse_days("Day 1 — A to B\nOvernight in B.\n\nDay 2 — Departure\nFly home.")
    assert not td.needs_model(blocks, [td.rule_day(b) for b in blocks])


async def test_extract_tour_uses_the_model_only_for_what_is_grounded():
    calls = []

    async def fake_generate(system, user):
        calls.append(user)
        return json.dumps({"days": [
            {"day": 1, "start": "Leh", "end": "Stok", "overnight": "Stok", "places": []},
            {"day": 2, "start": "Stok", "end": "Stok", "overnight": "Stok", "places": ["Old Leh"]},
            {"day": 3, "start": "Stok", "end": "Sakti", "overnight": "Sakti", "places": ["Thiksay"]},
            {"day": 4, "start": "Diskit", "end": "Hunder", "overnight": "Hunder", "places": []},
            {"day": 6, "start": "Leh", "end": None, "overnight": "Delhi", "places": []},  # Delhi: invented
        ]})

    recs = await td.extract_tour({"tour_id": "t1", "aa_name": "Ladakh", "aa_itineraries": LADAKH},
                                 fake_generate)
    assert len(calls) == 1                                 # one model call per tour
    by_day = {r.day_index: r for r in recs}
    assert sorted(by_day) == [1, 2, 3, 4, 5, 6]
    assert by_day[1].overnight_place == "Stok" and by_day[1].extracted_by == "llm"
    assert by_day[2].overnight_place == "Stok"
    assert by_day[3].overnight_place == "Sakti" and by_day[3].extracted_by == "rule+llm"
    assert by_day[5].overnight_place == "Hunder"
    assert by_day[6].overnight_place is None               # "Delhi" is not in day 6's text


async def test_extract_tour_falls_back_to_rules_when_the_model_fails():
    async def broken(system, user):
        return "not json"

    recs = await td.extract_tour({"tour_id": "t1", "aa_name": "", "aa_itineraries": LADAKH}, broken)
    by_day = {r.day_index: r for r in recs}
    assert by_day[3].overnight_place == "Sakti" and by_day[3].extracted_by == "rule"
    assert by_day[1].overnight_place is None


class _Db:
    def __init__(self):
        self.sql = []

    async def execute(self, sql, *args):
        self.sql.append((" ".join(sql.split()), args))
        return "OK"


async def test_persist_geocodes_each_overnight_once_and_deletes_stale_days():
    db = _Db()
    geocoded = []

    async def geocode(place, country):
        geocoded.append(place)
        return f"dest-{place}"

    recs = [td.DayRecord(1, "A", overnight_place="Stok"), td.DayRecord(2, "B", overnight_place="Stok"),
            td.DayRecord(3, "C", overnight_place="Sakti")]
    stats = await td.persist_tour(db, "t1", "h", recs, "India", geocode)
    assert geocoded == ["Stok", "Sakti"]
    inserts = [a for s, a in db.sql if s.startswith("INSERT INTO tripplanner.tour_day")]
    assert [a[6] for a in inserts] == ["dest-Stok", "dest-Stok", "dest-Sakti"]
    assert db.sql[-1][0].startswith("DELETE FROM tripplanner.tour_day") and db.sql[-1][1] == ("t1", 3)
    assert stats["linked"] == 2 and stats["overnight"] == 3


async def test_persist_keeps_the_day_when_geocoding_fails():
    db = _Db()

    async def geocode(place, country):
        raise RuntimeError("no result")

    stats = await td.persist_tour(db, "t1", "h", [td.DayRecord(1, "A", overnight_place="Nowhere")],
                                  "Laos", geocode)
    inserts = [a for s, a in db.sql if s.startswith("INSERT")]
    assert inserts[0][5] == "Nowhere" and inserts[0][6] is None
    assert stats["geocode_failed"] == 1


def test_source_hash_changes_with_the_text():
    assert td.source_hash("a") != td.source_hash("b") and td.source_hash("a") == td.source_hash("a")


class _ListDb:
    def __init__(self, tours, hashes):
        self.tours, self.hashes = tours, hashes
        self.tenant = None
        self.executed = []

    @asynccontextmanager
    async def transaction(self):
        yield

    async def execute(self, sql, *args):
        if "set_config('app.tenant_id'" in sql:
            self.tenant = args[0]
        self.executed.append((" ".join(sql.split()), args))
        return "DELETE 0"

    async def fetch(self, sql, *args):
        # published_tours / raw_tours are tenant RLS: nothing is visible without app.tenant_id
        return self.tours if self.tenant else []

    async def fetchval(self, sql, tour_id):
        return self.hashes.get(tour_id)


async def test_list_tours_marks_changed_or_missing_itineraries_stale():
    tours = [{"tour_id": "a", "country": "Laos", "aa_name": "A", "aa_itineraries": "x"},
             {"tour_id": "b", "country": "Laos", "aa_name": "B", "aa_itineraries": "y"},
             {"tour_id": "c", "country": "Laos", "aa_name": "C", "aa_itineraries": "z"}]
    db = _ListDb(tours, {"a": td.source_hash("x"), "b": td.source_hash("old")})
    stale = {t["tour_id"]: t["stale"] for t in await td.list_tours(db)}
    assert stale == {"a": False, "b": True, "c": True}


async def test_catalog_reads_set_the_aa_tenant_for_rls():
    db = _ListDb([{"tour_id": "a", "country": "Laos", "aa_name": "A", "aa_itineraries": "x"}], {})
    assert len(await td.list_tours(db)) == 1
    assert db.tenant == "00000000-0000-0000-0000-000000000001"


async def test_prune_deletes_only_rows_outside_the_active_set():
    db = _ListDb([{"tour_id": "a", "country": "Laos", "aa_name": "A", "aa_itineraries": "x"}], {})
    out = await td.prune(db)
    delete = [e for e in db.executed if e[0].startswith("DELETE")]
    assert out["active_tours"] == 1
    assert delete == [("DELETE FROM tripplanner.tour_day WHERE NOT (source_tour_id = ANY($1::text[]))", (["a"],))]


async def test_prune_refuses_when_no_active_tour_is_visible():
    db = _ListDb([], {})
    assert "error" in await td.prune(db, include_components=True)
    assert not [e for e in db.executed if e[0].startswith("DELETE")]


def test_is_transit_spots_nights_on_the_move():
    assert td.is_transit("overnight bullet train") and td.is_transit("Sleeper bus to Pokhara")
    assert not td.is_transit("Alleppey houseboat") and not td.is_transit("Thimphu") and not td.is_transit(None)


async def test_persist_does_not_geocode_a_transit_night():
    db = _Db()
    called = []

    async def geocode(place, country):
        called.append(place)
        return "dest"

    stats = await td.persist_tour(db, "t1", "h", [td.DayRecord(1, "A", overnight_place="overnight bullet train"),
                                                   td.DayRecord(2, "B", overnight_place="Xi'an")], "China", geocode)
    assert called == ["Xi'an"] and stats["geocode_failed"] == 0


class _RegeoDb:
    def __init__(self, rows):
        self.rows, self.updates = rows, []

    async def fetch(self, sql, *args):
        return self.rows

    async def execute(self, sql, *args):
        self.updates.append((" ".join(sql.split())[:40], args))


async def test_regeocode_moves_relabels_and_unlinks():
    rows = [{"id": "a", "name": "Jeju hotel", "country": "South Korea", "lat": 16.43, "lng": 120.6},
            {"id": "b", "name": "Siem Reap", "country": "Thailand", "lat": 13.36, "lng": 103.86},
            {"id": "c", "name": "overnight bullet train", "country": "China", "lat": 27.3, "lng": 128.5},
            {"id": "d", "name": "Nowhere Lodge", "country": "Laos", "lat": 1.0, "lng": 1.0},
            {"id": "e", "name": "Thimphu", "country": "Bhutan", "lat": 27.47, "lng": 89.64}]
    answers = {"Jeju hotel": (33.5, 126.5, "South Korea"), "Siem Reap": (13.36, 103.86, "Cambodia"),
               "Thimphu": (27.47, 89.64, "Bhutan")}

    async def forward(name, country):
        if name not in answers:
            raise RuntimeError("no result")
        return answers[name]

    db = _RegeoDb(rows)
    out = await td.regeocode(db, forward, "2026-09-29")
    assert (out["checked"], out["moved"], out["relabelled"], out["unlinked"]) == (5, 1, 1, 2)
    assert out["last"] == "Thimphu"
    unlinked = [a[0] for s, a in db.updates if s.startswith("UPDATE tripplanner.tour_day")]
    assert unlinked == ["c", "d"]


def test_regeocode_sql_takes_the_date_as_text():
    # asyncpg refuses a str for a ::date parameter; the Lambda passes the date as a string
    assert "$1::text::date" in td.REGEOCODE_SQL
