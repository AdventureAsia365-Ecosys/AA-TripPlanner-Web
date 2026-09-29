"""AA-674 step 1 — itinerary_components rebuilt from the atoms of an active tour."""
from __future__ import annotations

from contextlib import asynccontextmanager

from backend.extraction import components as cp

ATOMS = [
    {"atom_id": 1, "itinerary_day": 1, "place": "Paro", "activity_type": "culture", "text": "Paro — visit Rinpung Dzong"},
    {"atom_id": 2, "itinerary_day": 1, "place": "Zhiwaling Hotel", "activity_type": "stay", "text": "Overnight at the hotel"},
    {"atom_id": 3, "itinerary_day": 2, "place": "Tiger's Nest", "activity_type": "trek", "text": "Hike to Tiger's Nest"},
    {"atom_id": 4, "itinerary_day": 3, "place": "Thimphu", "activity_type": "transit", "text": "Drive to Thimphu"},
    {"atom_id": 5, "itinerary_day": 3, "place": "overnight train", "activity_type": "other", "text": "train"},
    {"atom_id": 6, "itinerary_day": 3, "place": "Ghost Valley", "activity_type": "culture", "text": "Ghost Valley walk"},
]


def test_build_components_maps_pinnable_atoms_and_skips_lodging_transit():
    comps, skipped = cp.build_components(ATOMS)
    assert [(c["day_index"], c["name"], c["activity"]) for c in comps] == [
        (1, "Paro", "cultural_heritage"), (2, "Tiger's Nest", "trekking"), (3, "Ghost Valley", "cultural_heritage")]
    assert skipped == 3                                            # hotel, transit atom, train


class _Db:
    def __init__(self, atoms):
        self.atoms, self.log, self.in_tx = atoms, [], False

    async def fetch(self, sql, *args):
        return self.atoms

    @asynccontextmanager
    async def transaction(self):
        self.in_tx = True
        yield
        self.in_tx = False

    async def execute(self, sql, *args):
        self.log.append(("execute", " ".join(sql.split()), args, self.in_tx))

    async def executemany(self, sql, rows):
        self.log.append(("executemany", " ".join(sql.split()), list(rows), self.in_tx))


async def test_rebuild_tour_links_embeds_in_batches_and_replaces_in_one_transaction(monkeypatch):
    monkeypatch.setattr(cp, "EMBED_BATCH", 2)
    embed_calls = []

    async def link(places, country, tour_name):
        assert (country, tour_name) == ("Bhutan", "Bhutan Highlights")
        return {"Paro": "d-paro", "Tiger's Nest": "d-tn"}        # Ghost Valley not located

    async def embed(texts):
        embed_calls.append(len(texts))
        return [[0.5, 0.25] for _ in texts]

    db = _Db(ATOMS)
    out = await cp.rebuild_tour(db, {"tour_id": "t1", "country": "Bhutan", "aa_name": "Bhutan Highlights"},
                                link, embed)
    assert out == {"atoms": 6, "components": 2, "skipped": 3, "places": 3, "linked_places": 2, "embedded": 2}
    assert embed_calls == [2]
    assert all(entry[3] for entry in db.log)
    assert db.log[0][1] == "DELETE FROM tripplanner.itinerary_components WHERE source_tour_id = $1"
    rows = db.log[1][2]
    assert [r[2] for r in rows] == ["d-paro", "d-tn"] and rows[0][9] == "[0.5,0.25]"


async def test_rebuild_tour_without_pinnable_atoms_still_clears_the_tour():
    db = _Db([ATOMS[1]])
    out = await cp.rebuild_tour(db, {"tour_id": "t1", "country": "Bhutan", "aa_name": ""}, None, None)
    assert out["components"] == 0
    assert [e[0] for e in db.log] == ["execute"]                   # delete only
