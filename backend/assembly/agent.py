"""LLM steps for Trip Assembly: compose and renarrate (Sonnet-tier).

compose   — narration for a freshly (re-)sequenced trip on a set change.
renarrate — narration for a customer's MANUAL reorder. It receives the
            fixed order and must NEVER re-select or reorder components,
            only write connective copy for the order given.

Both run through the Model Gateway stage `tp_compose` (AA-685): the model and failover order
come from the shared gateway tables, and every successful call is appended to `calls` so the
handler can write its llm_call_log row. An `invoker` can be injected so tests don't call AWS.
"""
from __future__ import annotations

from typing import Callable, Iterator, Optional

from backend.shared import llm_gateway

STAGE = "tp_compose"

# Injectable buffered invoker (tests): (stage, body) -> Claude messages response dict.
Invoker = Callable[[str, dict], dict]

_COMPOSE_SYSTEM = (
    "You are an Adventure Asia trip narrator. You are given an ORDERED "
    "list of itinerary components (each already chosen by the traveller). "
    "Write brief, warm connective narration for each day IN THE ORDER "
    "GIVEN. You must not add, remove, or reorder components, and must not "
    "invent places, prices, or facts beyond the provided text."
)

_RENARRATE_SYSTEM = (
    "You are an Adventure Asia trip narrator. The traveller has MANUALLY "
    "set the order of the days below. Write brief connective narration "
    "that respects their exact order. Do NOT suggest a different order, "
    "and do NOT add, remove, or reorder components."
)


def _build_body(system: str, itinerary: list[dict]) -> dict:
    day_lines = []
    for e in itinerary:
        day_lines.append(
            f"Day {e['day']}: {e.get('name', '')} — {e.get('text_extract', '')}"
        )
    user = (
        "Itinerary (fixed order):\n"
        + "\n".join(day_lines)
        + "\n\nWrite one or two warm sentences of narration per day, in order.\n"
        "Format STRICTLY as one line per day, beginning with 'Day N: ' "
        "(e.g. 'Day 1: ...'). Plain text only — no markdown, no bold, no "
        "asterisks, no headings, no blank lines between days."
    )
    return {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 1200,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }


def _text_from_response(resp: dict) -> str:
    """Extract the full text from a buffered Claude messages response:
    {"content": [{"type": "text", "text": "..."}, ...], ...}."""
    parts = resp.get("content")
    if isinstance(parts, list):
        return "".join(
            p.get("text", "") for p in parts
            if isinstance(p, dict) and p.get("type") == "text"
        )
    return ""


def _run(system: str, itinerary: list[dict], invoker: Optional[Invoker],
         route: Optional[llm_gateway.Route], calls: Optional[list]) -> Iterator[str]:
    """Buffered narration. Yields the whole text as one chunk so callers can
    keep treating narration as an iterator of text (the handler joins it).

    Buffered (InvokeModel / Converse) rather than streaming: the route
    buffers the full narration into one JSON response anyway (no SSE to the
    client through the HTTP API gateway), and the satellite invoker roles
    are granted InvokeModel only.
    """
    body = _build_body(system, itinerary)
    if invoker is not None:
        text = _text_from_response(invoker(STAGE, body))
    else:
        text, call = llm_gateway.generate_text(
            route or llm_gateway.builtin_route(STAGE), body["system"],
            body["messages"][0]["content"], body["max_tokens"])
        if calls is not None:
            calls.append(call)
    if text:
        yield text


def compose(
    itinerary: list[dict],
    *,
    invoker: Optional[Invoker] = None,
    route: Optional[llm_gateway.Route] = None,
    calls: Optional[list] = None,
) -> Iterator[str]:
    """Narrate a (re-)sequenced trip (buffered)."""
    return _run(_COMPOSE_SYSTEM, itinerary, invoker, route, calls)


def renarrate(
    itinerary: list[dict],
    *,
    invoker: Optional[Invoker] = None,
    route: Optional[llm_gateway.Route] = None,
    calls: Optional[list] = None,
) -> Iterator[str]:
    """Narrate a manually reordered trip (buffered). Never reorders."""
    return _run(_RENARRATE_SYSTEM, itinerary, invoker, route, calls)
