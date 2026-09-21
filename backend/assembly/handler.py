"""Lambda B entrypoint — Trip Assembly.

Routes:
  POST   /trip/{trip_id}/components               add_component
  DELETE /trip/{trip_id}/components/{component_id} remove_component
  PATCH  /trip/{trip_id}/reorder                  reorder
  POST   /trip/{trip_id}/send-to-advisor          sent (+ registration)
  POST   /trip/{trip_id}/narrate                  compose/renarrate (LLM)

Stateful per-visitor; never cached. The framework-agnostic route()
coroutine takes injectable conn + sender and is unit-tested directly.
Narration (compose/renarrate) is triggered by the frontend via a separate
streaming call after the debounce window; these mutation endpoints return
the updated projection immediately.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from backend import config
from backend.assembly import agent as agent_mod
from backend.assembly import events as events_mod
from backend.assembly import master_content
from backend.assembly import notify as notify_mod
from backend.assembly import registration as reg_mod
from backend.assembly import suggestions as suggest_mod

_COMPONENTS_RE = re.compile(r"^/trip/([^/]+)/components$")
_COMPONENT_ITEM_RE = re.compile(r"^/trip/([^/]+)/components/([^/]+)$")
_REORDER_RE = re.compile(r"^/trip/([^/]+)/reorder$")
_SEND_RE = re.compile(r"^/trip/([^/]+)/send-to-advisor$")
_NARRATE_RE = re.compile(r"^/trip/([^/]+)/narrate$")
_SUGGEST_RE = re.compile(r"^/trip/([^/]+)/suggestions$")
_TRIP_RE = re.compile(r"^/trip/([^/]+)$")

_HEADERS = {"content-type": "application/json", "cache-control": "no-store"}


def _resp(status: int, body: dict) -> dict:
    # default=str so asyncpg-returned UUID/date/datetime values in the
    # itinerary projection serialize cleanly (they're not JSON-native).
    return {"statusCode": status, "headers": _HEADERS, "body": json.dumps(body, default=str)}


async def route(
    method: str,
    path: str,
    body: dict,
    *,
    conn,
    sender: Optional[notify_mod.Sender] = None,
    narrator: Optional[Any] = None,
) -> dict:
    sender = sender or notify_mod.LoggingSender()

    # Read the current itinerary for a trip (used to restore a guest's trip
    # after a page reload — the trip_id is persisted client-side).
    m = _TRIP_RE.match(path)
    if m and method == "GET":
        trip_id = m.group(1)
        itinerary = await events_mod.current_itinerary(conn, trip_id)
        return _resp(200, {"trip_id": trip_id, "itinerary": itinerary})

    m = _COMPONENTS_RE.match(path)
    if m and method == "POST":
        trip_id = m.group(1)
        session_id = body.get("session_id")
        component_id = body.get("component_id")
        if not session_id or not component_id:
            return _resp(400, {"error": "session_id and component_id required"})
        itinerary = await events_mod.append_event(
            conn, trip_id, session_id, "add_component",
            {"component_id": component_id},
        )
        return _resp(200, {"trip_id": trip_id, "itinerary": itinerary})

    m = _COMPONENT_ITEM_RE.match(path)
    if m and method == "DELETE":
        trip_id, component_id = m.group(1), m.group(2)
        session_id = body.get("session_id")
        if not session_id:
            return _resp(400, {"error": "session_id required"})
        itinerary = await events_mod.append_event(
            conn, trip_id, session_id, "remove_component",
            {"component_id": component_id},
        )
        return _resp(200, {"trip_id": trip_id, "itinerary": itinerary})

    m = _REORDER_RE.match(path)
    if m and method == "PATCH":
        trip_id = m.group(1)
        session_id = body.get("session_id")
        ordered = body.get("ordered_component_ids")
        if not session_id or not isinstance(ordered, list):
            return _resp(400, {"error": "session_id and ordered_component_ids required"})
        itinerary = await events_mod.append_event(
            conn, trip_id, session_id, "reorder",
            {"ordered_component_ids": ordered},
        )
        return _resp(200, {"trip_id": trip_id, "itinerary": itinerary})

    m = _SEND_RE.match(path)
    if m and method == "POST":
        trip_id = m.group(1)
        session_id = body.get("session_id")
        if not session_id:
            return _resp(400, {"error": "session_id required"})
        customer = body.get("customer") or {}
        registered = await _is_registered(conn, session_id)

        if config.REQUIRE_REGISTRATION_BEFORE_HANDOFF and not registered:
            if not (customer.get("name") and (customer.get("phone") or customer.get("email"))):
                return _resp(
                    422,
                    {"error": "registration_required",
                     "detail": "name and phone or email required before sending"},
                )
            await reg_mod.register_and_claim(
                conn, session_id, customer.get("name", ""),
                customer.get("phone", ""), customer.get("email", ""),
            )

        itinerary = await events_mod.append_event(
            conn, trip_id, session_id, "sent", {"session_id": session_id},
        )
        event_log = await _event_log(conn, trip_id)
        await notify_mod.notify_advisor(trip_id, event_log, sender=sender)
        return _resp(200, {"trip_id": trip_id, "status": "sent", "itinerary": itinerary})

    m = _NARRATE_RE.match(path)
    if m and method == "POST":
        trip_id = m.group(1)
        session_id = body.get("session_id")
        if not session_id:
            return _resp(400, {"error": "session_id required"})
        # mode selects the prompt: "renarrate" respects a manual order and
        # never re-sequences; "compose" (default) narrates a fresh sequence.
        mode = body.get("mode") or "compose"
        if mode not in ("compose", "renarrate"):
            return _resp(400, {"error": "mode must be 'compose' or 'renarrate'"})

        itinerary = await events_mod.current_itinerary(conn, trip_id)
        if not itinerary:
            return _resp(400, {"error": "empty_trip",
                               "detail": "pin at least one component before narrating"})

        narrate_fn = _resolve_narrator(mode, narrator)
        try:
            narration, source = await _narrate(conn, itinerary, narrate_fn)
        except Exception as e:  # noqa: BLE001 — surface Bedrock failure as 502
            return _resp(502, {"error": "narration_failed", "detail": str(e)})

        return _resp(200, {"trip_id": trip_id, "mode": mode, "source": source,
                           "narration": narration, "itinerary": itinerary})

    m = _SUGGEST_RE.match(path)
    if m and method == "GET":
        trip_id = m.group(1)
        result = await suggest_mod.suggest(conn, trip_id)
        return _resp(200, {"trip_id": trip_id, **result})

    return _resp(404, {"error": "not found"})


def _resolve_narrator(mode: str, narrator: Optional[Any]):
    """Pick the narration function. `narrator`, when supplied (tests), is a
    callable(itinerary)->iterator[str] used for BOTH modes; otherwise the
    real agent.compose / agent.renarrate are used."""
    if narrator is not None:
        return narrator
    return agent_mod.renarrate if mode == "renarrate" else agent_mod.compose


async def _narrate(conn, itinerary: list[dict], narrate_fn) -> tuple[str, str]:
    """Build day-by-day narration, preferring AA's authored master itinerary
    text over an LLM rewrite.

    Strategy:
      1. Look up authored per-day text from published_tours.aa_itineraries
         (via source_tour_id + source_day_index).
      2. Days with authored text use it directly (no LLM call).
      3. Only the remaining days (unstructured itineraries, etc.) are sent
         to the LLM, then merged back in trip-day order.

    Returns (narration, source) where source is 'master' (all days authored),
    'llm' (none authored), or 'mixed'.
    """
    tour_ids = [e.get("source_tour_id") for e in itinerary if e.get("source_tour_id")]
    try:
        day_texts = await master_content.load_for_tours(conn, tour_ids)
    except Exception:  # noqa: BLE001
        # Reading authored itinerary text is a best-effort optimisation
        # (it lives in a shared schema this role may not be granted). If it
        # fails for any reason, fall back to LLM narration rather than 500.
        day_texts = {}
    master_narr, missing = master_content.build_narration(itinerary, day_texts)

    # All days resolved from authored content — no Bedrock call at all.
    if not missing:
        return master_narr, "master"

    # Some (or all) days need the LLM. Narrate only the missing days, then
    # merge with any authored lines, preserving trip-day order.
    llm_text = "".join(narrate_fn(missing))
    if not master_narr:
        return llm_text, "llm"

    merged = _merge_by_day(master_narr, llm_text)
    return merged, "mixed"


def _merge_by_day(master_narr: str, llm_narr: str) -> str:
    """Merge two 'Day N: ...' blocks into one, ordered by day number.
    Master lines win if a day appears in both."""
    import re

    def to_map(text: str) -> dict[int, str]:
        out: dict[int, str] = {}
        for line in text.splitlines():
            m = re.match(r"\s*Day\s*(\d+)\s*:", line, re.IGNORECASE)
            if m:
                out.setdefault(int(m.group(1)), line.strip())
        return out

    days = to_map(llm_narr)
    days.update(to_map(master_narr))  # master overrides
    return "\n".join(days[d] for d in sorted(days))


async def _is_registered(conn, session_id: str) -> bool:
    row = await conn.fetchrow(
        "SELECT customer_id FROM tripplanner.sessions WHERE id = $1", session_id
    )
    return bool(row and row["customer_id"])


async def _event_log(conn, trip_id: str) -> list[dict]:
    rows = await conn.fetch(
        "SELECT event_type, payload FROM tripplanner.trip_events "
        "WHERE trip_id = $1 ORDER BY id ASC",
        trip_id,
    )
    out = []
    for r in rows:
        payload = r["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        out.append({"event_type": r["event_type"], "payload": payload})
    return out


# --- Lambda adapter ---------------------------------------------------------

def _extract_request(event: dict) -> tuple[str, str, dict]:
    ctx = event.get("requestContext", {})
    http = ctx.get("http", {})
    method = http.get("method") or event.get("httpMethod") or "GET"
    path = http.get("path") or event.get("rawPath") or event.get("path") or "/"
    raw_body = event.get("body") or "{}"
    try:
        body = json.loads(raw_body) if isinstance(raw_body, str) else raw_body
    except json.JSONDecodeError:
        body = {}
    return method, path, body


def handler(event, context):  # pragma: no cover - thin AWS adapter
    import asyncio

    from backend.shared import auth
    from backend.shared.db import get_pool

    denied = auth.check_event(event)
    if denied is not None:
        return denied

    method, path, body = _extract_request(event)

    async def _run():
        pool = await get_pool()
        async with pool.acquire() as conn:
            return await route(method, path, body, conn=conn)

    return asyncio.get_event_loop().run_until_complete(_run())
