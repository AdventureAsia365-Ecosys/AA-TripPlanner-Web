"""AA-1115 (S282 audit 6.3, "tp_compose: does it pass scrub_text"): the compose / renarrate prompt is built from
catalogue fields only, so it never needs the scrubber the planning chat applies to free text.

The narration prompt (stage ``tp_compose``, backend/assembly/agent.py) reads, per itinerary entry, the day number and
the stop's name and text extract, and nothing else. Those come from the CIS catalogue views (events.COMPONENTS_SQL:
catalog.v_tour_component joined to catalog.v_destination); the itinerary entry the handler passes carries catalogue
and position fields only. These guards fail when either side grows: a new field read into the prompt, a new key on
the itinerary entry, or a column of the component read that is not a catalogue view's.

Offline: an injected invoker captures the body; invented names and places only.
"""
from __future__ import annotations

import ast
import inspect
import re

from backend.assembly import agent, events

# The fields of an itinerary entry the narration prompt may read (catalogue text and the day position).
PROMPT_FIELDS = {"day", "name", "text_extract"}

# Every key current_itinerary() may put on an entry: ids, positions and catalogue / geography fields. A traveller's
# own words (custom day notes, lead answers, chat text) and anything personal must never be one of them.
ITINERARY_KEYS = {
    "day", "component_id", "name", "text_extract", "source_tour_id", "source_day_index", "destination_id",
    "destination_name", "lat", "lng", "located_by", "precision", *events.SEGMENT_KEYS,
}

SENTINELS = {
    "customer_email": "ivo.tarrant@example.invalid",
    "customer_name": "Ivo Tarrant",
    "phone": "+15550100999",
    "notes": "call me after six, my passport number is X0000000",
    "message": "plan a trip for my mother Wenna Ostrowe",
    "session_id": "00000000-0000-4000-8000-0000000000aa",
    "guest_token": "G" * 43,
    "lead": {"name": "Ivo Tarrant"},
    "lat": 11.2345,
    "destination_name": "Lower Quillmere Harbour",
}


def _entry(day: int, name: str, text: str) -> dict:
    return {"day": day, "component_id": f"00000000-0000-4000-8000-00000000000{day}", "name": name,
            "text_extract": text, **SENTINELS}


ITINERARY = [
    _entry(1, "Ottervale lantern walk", "An evening walk past the invented lantern bridges of Ottervale."),
    _entry(2, "Brackenmoor tea terraces", "A morning among the made-up tea terraces of Brackenmoor."),
]


def _captured_body(fn) -> dict:
    seen: list[dict] = []

    def invoker(stage: str, body: dict) -> dict:
        assert stage == agent.STAGE
        seen.append(body)
        return {"content": [{"type": "text", "text": "Day 1: ok\nDay 2: ok"}]}

    assert "".join(fn(ITINERARY, invoker=invoker)) == "Day 1: ok\nDay 2: ok"
    assert len(seen) == 1
    return seen[0]


def _strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, (list, tuple)):
        return [s for v in value for s in _strings(v)]
    return [str(value)]


def test_the_prompt_carries_the_catalogue_text_and_nothing_of_the_traveller():
    for fn in (agent.compose, agent.renarrate):
        body = _captured_body(fn)
        assert set(body) == {"anthropic_version", "max_tokens", "system", "messages"}
        assert [m["role"] for m in body["messages"]] == ["user"]
        text = "\n".join(_strings(body))
        for e in ITINERARY:
            assert f"Day {e['day']}: {e['name']} — {e['text_extract']}" in text
        for key, value in SENTINELS.items():
            for s in _strings(value):
                assert s not in text, f"{key} reached the {fn.__name__} prompt"
        # The day lines are exactly the catalogue lines, in order: nothing else between them.
        user = body["messages"][0]["content"]
        lines = [ln for ln in user.splitlines() if re.match(r"^Day \d+: ", ln)]
        assert lines == [f"Day {e['day']}: {e['name']} — {e['text_extract']}" for e in ITINERARY]


def _keys_read(fn) -> set[str]:
    """Every constant key the function reads from a mapping: x['k'] and x.get('k')."""
    tree = ast.parse(inspect.getsource(fn))
    keys: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
            keys.add(node.slice.value)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get"
                and node.args and isinstance(node.args[0], ast.Constant)):
            keys.add(node.args[0].value)
    return keys


def test_the_prompt_builder_reads_only_the_catalogue_fields():
    # A new field read into the prompt must be a catalogue field and be added here on purpose.
    assert _keys_read(agent._build_body) == PROMPT_FIELDS
    # The prompt has one input: the itinerary (no lead, chat or traveller argument).
    for fn in (agent.compose, agent.renarrate, agent._run, agent._build_body):
        params = set(inspect.signature(fn).parameters)
        assert params <= {"system", "itinerary", "invoker", "route", "calls"}, fn.__name__


def test_the_itinerary_entry_carries_catalogue_and_position_fields_only():
    tree = ast.parse(inspect.getsource(events.current_itinerary_and_missing))
    dicts = [n for n in ast.walk(tree) if isinstance(n, ast.Dict)]
    assert len(dicts) == 1, "one entry shape"
    literal = {k.value for k in dicts[0].keys if isinstance(k, ast.Constant)}
    spread = [v for k, v in zip(dicts[0].keys, dicts[0].values) if k is None]
    assert literal | set(events.SEGMENT_KEYS) <= ITINERARY_KEYS
    assert literal == ITINERARY_KEYS - set(events.SEGMENT_KEYS)
    # The only spread is the line-segment keys (`**{k: e.get(k) for k in SEGMENT_KEYS}`).
    assert len(spread) == 1 and "SEGMENT_KEYS" in ast.unparse(spread[0])


def test_the_component_read_selects_catalogue_views_only():
    sql = " ".join(events.COMPONENTS_SQL.split())
    tables = set(re.findall(r"\b(?:FROM|JOIN)\s+([a-z_]+\.[a-z_0-9]+)", sql, re.IGNORECASE))
    assert tables == {"catalog.v_tour_component", "catalog.v_destination"}
    assert "tripplanner." not in sql and "booking." not in sql and "crm." not in sql
