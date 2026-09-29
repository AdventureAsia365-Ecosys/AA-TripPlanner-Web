"""AA-675 follow-up — geocoding stays inside the AA countries and reports the real country."""
from __future__ import annotations

import pytest

from backend.extraction import geocode as g


def _f(name, iso, relevance=1.0, lnglat=(0.0, 0.0)):
    return {"text": name, "relevance": relevance, "center": list(lnglat), "place_type": ["place"],
            "context": [{"id": "region.1", "text": "x"}, {"id": "country.9", "short_code": iso}]}


def test_pick_feature_prefers_the_tour_country_among_equally_relevant_results():
    feats = [_f("Paro", "in"), _f("Paro", "bt"), _f("Paro", "np", relevance=0.5)]
    assert g._feature_iso(g.pick_feature(feats, "bt")) == "bt"


def test_pick_feature_keeps_a_better_match_in_a_neighbour_country():
    feats = [_f("Siem Reap", "kh", 1.0), _f("Siem Reap Rd", "th", 0.6)]
    assert g._feature_iso(g.pick_feature(feats, "th")) == "kh"


def test_pick_feature_none_without_results():
    assert g.pick_feature([], "bt") is None


def test_country_codes_cover_the_sold_countries_and_not_the_philippines():
    sold = ["Bhutan", "India", "Nepal", "China", "Japan", "Sri Lanka", "Laos", "South Korea",
            "Thailand", "Mongolia", "Taiwan"]
    assert all(g.country_iso(c) for c in sold)
    assert "ph" not in g.AA_ISOS.split(",")
    assert g.ISO_COUNTRY["kh"] == "Cambodia" and g.ISO_COUNTRY["kr"] == "South Korea"


class _Resp:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


class _Http:
    def __init__(self, features):
        self.features, self.params = features, None

    async def get(self, url, params):
        self.params = params
        return _Resp({"features": self.features})


async def test_forward_searches_only_aa_countries_and_returns_the_found_country(monkeypatch):
    monkeypatch.setattr(g, "_mapbox_token", lambda: "tok")
    http = _Http([_f("Siem Reap", "kh", lnglat=(103.86, 13.36))])
    lat, lng, country = await g._mapbox_forward(http, "Siem Reap", "Thailand")
    assert (lat, lng, country) == (13.36, 103.86, "Cambodia")
    assert http.params["country"] == g.AA_ISOS and "region" not in http.params["types"].split(",")


async def test_forward_raises_when_nothing_in_the_aa_countries(monkeypatch):
    monkeypatch.setattr(g, "_mapbox_token", lambda: "tok")
    with pytest.raises(g.GeocodeError):
        await g._mapbox_forward(_Http([]), "Kensington Hotel on Yoido", "South Korea")
