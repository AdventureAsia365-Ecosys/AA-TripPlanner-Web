"""AA-675 follow-up — the model proposes coordinates, Mapbox confirms nearby."""
from __future__ import annotations

from backend.extraction import locate as lc


def _feat(lng, lat, iso):
    return {"center": [lng, lat], "place_type": ["place"], "context": [{"id": "country.1", "short_code": iso}]}


def test_parse_answer_keeps_known_places_and_rejects_the_rest():
    raw = 'Sure: {"Chiang Mai": [18.79, 98.98, "thailand"], "Paro": [27.43, 89.41, "Narnia"], ' \
          '"Nowhere": null, "Zero": [0, 0, "Laos"], "Bad": ["x", 1, "Laos"]}'
    out = lc.parse_answer(raw, ["Chiang Mai", "Paro", "Nowhere", "Zero", "Bad", "Missing"])
    assert out["Chiang Mai"] == (18.79, 98.98, "Thailand")
    assert all(out[k] is None for k in ["Paro", "Nowhere", "Zero", "Bad", "Missing"])


def test_parse_answer_on_garbage_is_all_none():
    assert lc.parse_answer("no json here", ["A"]) == {"A": None}


def test_confirm_takes_a_mapbox_result_only_near_the_model_point():
    near = _feat(98.99, 18.79, "th")                               # ~1 km away
    far = _feat(103.3, 16.0, "th")                                 # the Roi Et "Chiang Mai"
    assert lc.confirm([far, near], 18.79, 98.98).source == "mapbox"
    assert lc.confirm([far], 18.79, 98.98) is None


async def test_locate_many_batches_prefers_mapbox_and_falls_back_to_the_model_point(monkeypatch):
    monkeypatch.setattr(lc, "BATCH", 2)
    prompts = []

    async def generate(system, user):
        prompts.append(user)
        return '{"Delhi": [28.61, 77.21, "India"], "Laya": [28.07, 89.7, "Bhutan"], "Ghost": null}'

    async def search(name, lat, lng):
        return [_feat(77.2, 28.6, "in")] if name == "Delhi" else [_feat(80.0, 20.0, "in")]

    items = [("Delhi", "India", "t"), ("Laya", "Bhutan", "t"), ("Ghost", "Laos", "t")]
    out = await lc.locate_many(items, generate, search)
    assert len(prompts) == 2                                       # 3 names, batches of 2
    assert out["Delhi"].source == "mapbox" and out["Delhi"].country == "India"
    assert out["Laya"] == lc.Located(28.07, 89.7, "Bhutan", "llm")  # Mapbox result too far away
    assert out["Ghost"] is None


async def test_locate_many_survives_a_failed_model_call():
    async def generate(system, user):
        raise RuntimeError("throttled")

    assert await lc.locate_many([("A", "Laos", "t")], generate, None) == {"A": None}
