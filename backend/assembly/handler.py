"""Lambda B entrypoint — Trip Assembly.

Routes:
  GET    /trip/{trip_id}                          current itinerary + added days
  GET    /trip/{trip_id}/suggestions              next-to-pin suggestions
  GET    /trip/{trip_id}/coverage                 AA tour leg chain
  GET    /trip/{trip_id}/stops/{dest}/activities  AA activities at one stop
  POST   /trip/{trip_id}/components               add_component
  DELETE /trip/{trip_id}/components/{component_id} remove_component
  PATCH  /trip/{trip_id}/reorder                  reorder
  POST   /trip/{trip_id}/send-to-advisor          sent (+ registration)
  POST   /trip/{trip_id}/narrate                  compose/renarrate (LLM)

Every mutating route runs _guard (AA-827) right after its required-field check: the guest token
from the BFF must match the session (guest_session.py), and the session must own the trip.
session_id is an identifier, never a credential. Every trip read GET runs _read_guard (AA-909): the
token must match the session that owns the trip, else the same 404 as an unknown route.

Stateful per-visitor; never cached. The framework-agnostic route()
coroutine takes injectable conn + sender and is unit-tested directly.
Narration (compose/renarrate) is triggered by the frontend via a separate
streaming call after the debounce window; these mutation endpoints return
the updated projection immediately.
"""
from __future__ import annotations

import functools
import json
import os
import re
import traceback
import uuid
from typing import Any, Optional

from backend import config
from backend.assembly import agent as agent_mod
from backend.assembly import events as events_mod
from backend.assembly import grounding as grounding_mod
from backend.assembly import guest_session
from backend.assembly import handoff as handoff_mod
from backend.assembly import master_content
from backend.assembly import narration_cache
from backend.assembly import notify as notify_mod
from backend.assembly import registration as reg_mod
from backend.assembly import activities as activities_mod
from backend.assembly import coverage as coverage_mod
from backend.assembly import suggestions as suggest_mod
from backend.shared import component_ids, llm_gateway

_COMPONENTS_RE = re.compile(r"^/trip/([^/]+)/components$")
_COMPONENT_ITEM_RE = re.compile(r"^/trip/([^/]+)/components/([^/]+)$")
_REORDER_RE = re.compile(r"^/trip/([^/]+)/reorder$")
_SEND_RE = re.compile(r"^/trip/([^/]+)/send-to-advisor$")
_NARRATE_RE = re.compile(r"^/trip/([^/]+)/narrate$")
_SUGGEST_RE = re.compile(r"^/trip/([^/]+)/suggestions$")
_COVERAGE_RE = re.compile(r"^/trip/([^/]+)/coverage$")
_DAYS_RE = re.compile(r"^/trip/([^/]+)/days$")
_APPLY_ROUTE_RE = re.compile(r"^/trip/([^/]+)/apply-route$")
_DAY_ITEM_RE = re.compile(r"^/trip/([^/]+)/days/([^/]+)$")
_STOP_ACTIVITIES_RE = re.compile(r"^/trip/([^/]+)/stops/([^/]+)/activities$")
_TRIP_RE = re.compile(r"^/trip/([^/]+)$")
_TRIP_PREFIX_RE = re.compile(r"^/trip/([^/]+)")

# (method, path pattern, route name) — the name is what logs carry, never ids.
_ROUTE_NAMES = (
    ("GET", _TRIP_RE, "get_trip"),
    ("POST", _COMPONENTS_RE, "add"),
    ("DELETE", _COMPONENT_ITEM_RE, "remove"),
    ("PATCH", _REORDER_RE, "reorder"),
    ("POST", _SEND_RE, "send"),
    ("POST", _NARRATE_RE, "narrate"),
    ("GET", _SUGGEST_RE, "suggestions"),
    ("POST", _APPLY_ROUTE_RE, "apply_route"),
    ("POST", _DAYS_RE, "add_day"),
    ("DELETE", _DAY_ITEM_RE, "remove_day"),
    ("GET", _STOP_ACTIVITIES_RE, "activities"),
    ("GET", _COVERAGE_RE, "coverage"),
)

_HEADERS = {"content-type": "application/json", "cache-control": "no-store"}

# Narration sources whose text is AI-written or AI-assisted, reported as `ai_generated`. AA master
# text is the CIS writer model's output (AA-819), so 'master' and 'master_basic' count; only
# 'basic' (neutral name lines, no master day) has no AI text. Cache eligibility is separate
# (narration_cache.CACHEABLE_SOURCES).
_AI_SOURCES = ("llm", "mixed", "master", "master_basic")


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
    guest_token: Optional[str] = None,
) -> dict:
    sender = sender or notify_mod.LoggingSender()

    # AA-827: ids are UUIDs (DB columns are uuid). Reject anything else before any SQL, without
    # echoing the value (a malformed id would otherwise reach an asyncpg error message).
    m = _TRIP_PREFIX_RE.match(path)
    if m and not _is_uuid(m.group(1)):
        return _resp(400, {"error": "invalid trip_id"})
    if body.get("session_id") not in (None, "") and not _is_uuid(body.get("session_id")):
        return _resp(400, {"error": "invalid session_id"})

    # Read the current itinerary for a trip (used to restore a guest's trip
    # after a page reload — the trip_id is persisted client-side).
    m = _TRIP_RE.match(path)
    if m and method == "GET":
        trip_id = m.group(1)
        if denied := await _read_guard(conn, trip_id, guest_token, "get_trip"):
            return denied
        itinerary = await events_mod.current_itinerary(conn, trip_id)
        custom_days = await events_mod.current_custom_days(conn, trip_id)
        return _resp(200, {"trip_id": trip_id, "itinerary": itinerary, "custom_days": custom_days})

    m = _COMPONENTS_RE.match(path)
    if m and method == "POST":
        trip_id = m.group(1)
        session_id = body.get("session_id")
        component_id = body.get("component_id")
        if not session_id or not component_id:
            return _resp(400, {"error": "session_id and component_id required"})
        if denied := await _guard(conn, trip_id, session_id, guest_token, "add"):
            return denied
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
        if denied := await _guard(conn, trip_id, session_id, guest_token, "remove"):
            return denied
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
        if denied := await _guard(conn, trip_id, session_id, guest_token, "reorder"):
            return denied
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
        if denied := await _guard(conn, trip_id, session_id, guest_token, "send"):
            return denied
        customer = body.get("customer") or {}

        # AA-731: idempotent hand-off. One transaction, serialized per trip by an advisory lock;
        # if the trip's latest event is already `sent` (no edit since), this is a double click or
        # a retry after a lost response: answer with that hand-off, no second event or email.
        # Sending again after a real edit is a new hand-off by design. The email goes out inside
        # the transaction, so a failed send rolls the `sent` event back and the retry resends.
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"tp_send:{trip_id}")
            last = await _last_event(conn, trip_id)
            if last and last["event_type"] == "sent":
                itinerary = await events_mod.current_itinerary(conn, trip_id)
                return _resp(200, {"trip_id": trip_id, "status": "sent", "handoff_id": last["id"],
                                   "already_sent": True, "itinerary": itinerary})

            registered = await _is_registered(conn, session_id)
            submitted = None
            if config.REQUIRE_REGISTRATION_BEFORE_HANDOFF and not registered:
                if not (customer.get("name") and (customer.get("phone") or customer.get("email"))):
                    return _resp(
                        422,
                        {"error": "registration_required",
                         "detail": "name and phone or email required before sending"},
                    )
                claimed = await reg_mod.register_and_claim(
                    conn, session_id, customer.get("name", ""),
                    customer.get("phone", ""), customer.get("email", ""),
                    consent=customer.get("consent") is True,
                )
                if claimed is None:
                    # S235: the contact matched an existing customer. Unverified, so the session is
                    # not linked to it; hand off exactly what this request submitted instead.
                    submitted = handoff_mod.submitted_contact(
                        name=customer.get("name", ""), phone=customer.get("phone"),
                        email=customer.get("email"), consent=customer.get("consent") is True,
                    )

            itinerary = await events_mod.append_event(
                conn, trip_id, session_id, "sent", {"session_id": session_id},
            )
            handoff = await _last_event(conn, trip_id)
            # AA-676: the inquiry.submitted outbox row commits with the `sent` event (same transaction).
            await handoff_mod.write_inquiry(
                conn, trip_id=trip_id, handoff_id=handoff["id"], session_id=session_id,
                itinerary=itinerary, custom_days=await events_mod.current_custom_days(conn, trip_id),
                submitted=submitted,
            )
            event_log = await _event_log(conn, trip_id)
            await notify_mod.notify_advisor(trip_id, event_log, sender=sender)
        return _resp(200, {"trip_id": trip_id, "status": "sent", "handoff_id": handoff["id"],
                           "already_sent": False, "itinerary": itinerary})

    m = _NARRATE_RE.match(path)
    if m and method == "POST":
        trip_id = m.group(1)
        session_id = body.get("session_id")
        if not session_id:
            return _resp(400, {"error": "session_id required"})
        # AA-805 / AA-827: narration costs model spend — only the session that created the trip
        # may ask (owner check in _guard).
        if denied := await _guard(conn, trip_id, session_id, guest_token, "narrate"):
            return denied
        # mode selects the prompt: "renarrate" respects a manual order and
        # never re-sequences; "compose" (default) narrates a fresh sequence.
        mode = body.get("mode") or "compose"
        if mode not in ("compose", "renarrate"):
            return _resp(400, {"error": "mode must be 'compose' or 'renarrate'"})

        itinerary = await events_mod.current_itinerary(conn, trip_id)
        if not itinerary:
            return _resp(400, {"error": "empty_trip",
                               "detail": "pin at least one component before narrating"})

        # AA-805: an identical itinerary reuses its narration (no model call, no log row). The
        # master day text is part of the key, so a CIS edit to a tour never serves stale text.
        day_texts = await _load_master_texts(conn, itinerary)
        cache_key = narration_cache.itinerary_hash(itinerary, mode, day_texts)
        cached = await narration_cache.get(conn, cache_key)
        if cached is not None:
            return _resp(200, {"trip_id": trip_id, "mode": mode, "source": cached["source"],
                               "ai_generated": cached["source"] in _AI_SOURCES, "cached": True,
                               "narration": cached["narration"], "itinerary": itinerary})

        # AA-805: daily spend hard stop. Exhausted -> no model call; days without AA text get a
        # neutral line built from the components' names.
        budget = await llm_gateway.budget_status(conn)
        calls: list = []
        narrate_fn = None if budget["exhausted"] else await _resolve_narrator(mode, narrator, conn, calls)
        try:
            narration, source, ungrounded = await _narrate(conn, itinerary, narrate_fn,
                                                           day_texts=day_texts)
        except Exception as e:  # noqa: BLE001 — surface Bedrock failure as 502
            await _record_calls(conn, calls, {"mode": mode, "days": len(itinerary)})
            return _resp(502, {"error": "narration_failed", "detail": str(e)})
        signal = {"mode": mode, "source": source, "days": len(itinerary),
                  "output_len_chars": len(narration)}
        if ungrounded:  # AA-731: invented figures removed before the customer saw them
            signal["ungrounded_lines"] = len(ungrounded)
        await _record_calls(conn, calls, signal)  # AA-685: one llm_call_log row per model call
        await narration_cache.put(conn, cache_key, mode, narration, source, len(itinerary))

        out = {"trip_id": trip_id, "mode": mode, "source": source,
               "ai_generated": source in _AI_SOURCES, "cached": False,
               "narration": narration, "itinerary": itinerary}
        if budget["exhausted"]:
            out["ai_budget_exhausted"] = True
        return _resp(200, out)

    m = _SUGGEST_RE.match(path)
    if m and method == "GET":
        trip_id = m.group(1)
        if denied := await _read_guard(conn, trip_id, guest_token, "suggestions"):
            return denied
        filters = {k: body.get(k) for k in ("activity", "intensity_level") if body.get(k)}
        result = await suggest_mod.suggest(conn, trip_id, filters=filters)
        return _resp(200, {"trip_id": trip_id, **result})

    m = _APPLY_ROUTE_RE.match(path)
    if m and method == "POST":
        # PR-11 point 1: start from a proposed AA route — pin its components in its day order.
        trip_id = m.group(1)
        session_id, ids = body.get("session_id"), body.get("component_ids")
        if not session_id or not isinstance(ids, list) or not ids or len(ids) > 60:
            return _resp(400, {"error": "session_id and component_ids (1-60) required"})
        if denied := await _guard(conn, trip_id, session_id, guest_token, "apply_route"):
            return denied
        # AA-847: a route from a browse answer cached before the PLACES_SOURCE flip carries frozen ids
        ids = await component_ids.resolve(conn, (str(i) for i in ids))
        present = {str(c["id"]) for c in await events_mod._current_components(conn, trip_id)}  # noqa: SLF001
        for cid in ids:
            if cid not in present:
                await events_mod.append_event(conn, trip_id, session_id, "add_component", {"component_id": cid})
        others = [c for c in present if c not in ids]
        itinerary = await events_mod.append_event(conn, trip_id, session_id, "reorder",
                                                  {"ordered_component_ids": ids + others})
        return _resp(200, {"trip_id": trip_id, "itinerary": itinerary})

    m = _DAYS_RE.match(path)
    if m and method == "POST":
        trip_id = m.group(1)
        session_id, kind = body.get("session_id"), body.get("kind")
        if not session_id or kind not in events_mod.DAY_KINDS:
            return _resp(400, {"error": "session_id and kind required",
                               "kinds": sorted(events_mod.DAY_KINDS)})
        if denied := await _guard(conn, trip_id, session_id, guest_token, "add_day"):
            return denied
        after = await component_ids.resolve_one(conn, body.get("after_component_id"))  # AA-847
        if kind == "extra_night":
            pinned = {str(c["id"]) for c in await events_mod._current_components(conn, trip_id)}  # noqa: SLF001
            if not after or str(after) not in pinned:
                return _resp(400, {"error": "extra_night needs after_component_id, a pinned stop"})
        day_id = str(uuid.uuid4())
        await events_mod.append_event(conn, trip_id, session_id, "add_day",
                                      {"day_id": day_id, "kind": kind, "after_component_id": after,
                                       "note": (body.get("note") or "")[:500] or None})
        return _resp(200, {"trip_id": trip_id, "day_id": day_id,
                           "custom_days": await events_mod.current_custom_days(conn, trip_id)})

    m = _DAY_ITEM_RE.match(path)
    if m and method == "DELETE":
        trip_id, day_id = m.group(1), m.group(2)
        session_id = body.get("session_id")
        if not session_id:
            return _resp(400, {"error": "session_id required"})
        if denied := await _guard(conn, trip_id, session_id, guest_token, "remove_day"):
            return denied
        if day_id not in {d["day_id"] for d in await events_mod.current_custom_days(conn, trip_id)}:
            return _resp(404, {"error": "no such added day"})
        await events_mod.append_event(conn, trip_id, session_id, "remove_day", {"day_id": day_id})
        return _resp(200, {"trip_id": trip_id,
                           "custom_days": await events_mod.current_custom_days(conn, trip_id)})

    m = _STOP_ACTIVITIES_RE.match(path)
    if m and method == "GET":
        trip_id, destination_id = m.group(1), m.group(2)
        if denied := await _read_guard(conn, trip_id, guest_token, "activities"):
            return denied
        return _resp(200, {"trip_id": trip_id, **await activities_mod.at_stop(conn, trip_id, destination_id)})

    m = _COVERAGE_RE.match(path)
    if m and method == "GET":
        trip_id = m.group(1)
        if denied := await _read_guard(conn, trip_id, guest_token, "coverage"):
            return denied
        return _resp(200, {"trip_id": trip_id, **await coverage_mod.coverage(conn, trip_id)})

    return _not_found()


def _not_found() -> dict:
    """The one 404 body: an unknown route and every refused trip read answer exactly this."""
    return _resp(404, {"error": "not found"})


def _is_uuid(value: Any) -> bool:
    """A canonical (lowercase, hyphenated) UUID string — what the browser's crypto.randomUUID makes
    and what asyncpg returns, so the owner check compares like with like."""
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def _route_name(method: str, path: str) -> str:
    for verb, rx, name in _ROUTE_NAMES:
        if method == verb and rx.match(path):
            return name
    return "unknown"


async def _guard(conn, trip_id: str, session_id: str, guest_token: Optional[str],
                 route_name: str) -> Optional[dict]:
    """AA-827: the request's guest token must match the session (401), and the session must own
    the trip (403; a trip with no draft yet has no owner). Returns the denial response, or None.
    Creates the sessions row on first use, so trip_events' FK fails loudly if a route skips it."""
    outcome = await guest_session.authorize(conn, session_id, guest_token)
    if outcome in guest_session.DENIED:
        guest_session.emit(outcome, route_name)
        return _resp(401, {"error": "guest_session_invalid"})
    owner = await _trip_owner(conn, trip_id)
    if owner is not None and str(owner) != str(session_id):
        guest_session.emit(guest_session.FORBIDDEN_OWNER, route_name)
        return _resp(403, {"error": "forbidden", "detail": "this trip belongs to another session"})
    guest_session.emit(outcome, route_name)
    return None


async def _read_guard(conn, trip_id: str, guest_token: Optional[str],
                      route_name: str) -> Optional[dict]:
    """AA-909: a trip read needs the token bound to the session that owns the trip. Any refusal
    (no token, malformed, wrong, another trip's, no such trip, no draft yet) is the unknown-route
    404, so existence never leaks. Returns the denial response, or None."""
    outcome = await guest_session.authorize_read(conn, trip_id, guest_token)
    guest_session.emit(outcome, route_name)
    return None if outcome == guest_session.READ_OK else _not_found()


async def _resolve_narrator(mode: str, narrator: Optional[Any], conn, calls: list):
    """Pick the narration function. `narrator`, when supplied (tests), is a
    callable(itinerary)->iterator[str] used for BOTH modes; otherwise the
    real agent.compose / agent.renarrate run on the `tp_compose` gateway
    route, appending each model call to `calls`."""
    if narrator is not None:
        return narrator
    route = await llm_gateway.load_route(conn, agent_mod.STAGE)
    fn = agent_mod.renarrate if mode == "renarrate" else agent_mod.compose
    return functools.partial(fn, route=route, calls=calls)


async def _trip_owner(conn, trip_id: str) -> Optional[str]:
    """The session that created the trip (trip_drafts.session_id is never changed by the upsert),
    or None when the trip has no draft yet."""
    row = await conn.fetchrow("SELECT session_id FROM tripplanner.trip_drafts WHERE id = $1", trip_id)
    return row["session_id"] if row else None


async def _record_calls(conn, calls: list, signal: dict) -> None:
    """One llm_call_log row per model call attempt, failed attempts included (AA-805)."""
    for call in calls:
        await llm_gateway.record(conn, call, signal)


def _basic_narration(entries: list[dict]) -> str:
    """Neutral 'Day N: <names>.' lines — no model, no invented facts (budget fallback)."""
    by_day: dict[int, list[str]] = {}
    for e in entries:
        by_day.setdefault(int(e["day"]), []).append(e.get("name") or "")
    return "\n".join(
        f"Day {d}: {', '.join(n for n in names if n)}." if any(names) else f"Day {d}: free day."
        for d, names in sorted(by_day.items())
    )


async def _load_master_texts(conn, itinerary: list[dict]) -> dict[tuple[str, int], str]:
    """AA's authored per-day text for the itinerary's source tours; {} when unreadable."""
    tour_ids = [e.get("source_tour_id") for e in itinerary if e.get("source_tour_id")]
    try:
        return await master_content.load_for_tours(conn, tour_ids)
    except Exception:  # noqa: BLE001
        # Reading authored itinerary text is a best-effort optimisation
        # (it lives in a shared schema this role may not be granted). If it
        # fails for any reason, fall back to LLM narration rather than 500.
        return {}


async def _narrate(conn, itinerary: list[dict], narrate_fn,
                   day_texts: dict | None = None) -> tuple[str, str, list[dict]]:
    """Build day-by-day narration, preferring AA's authored master itinerary
    text over an LLM rewrite.

    Strategy:
      1. Look up authored per-day text from published_tours.aa_itineraries
         (via source_tour_id + source_day_index).
      2. Days with authored text use it directly (no LLM call).
      3. Only the remaining days (unstructured itineraries, etc.) are sent
         to the LLM, then merged back in trip-day order.

    Returns (narration, source, ungrounded) where source is 'master' (all days authored),
    'llm' (none authored), or 'mixed', and ungrounded lists the LLM lines removed or replaced
    because they stated a figure no component text supports (AA-731, grounding.py).

    narrate_fn None (AA-805 daily spend cap reached): the remaining days get neutral name lines.
    Source is 'master_basic' when some days are AA master text (AI-assisted, AA-819: the frontend
    labels it), 'basic' when none is: no AI text at all.
    """
    if day_texts is None:
        day_texts = await _load_master_texts(conn, itinerary)
    master_narr, missing = master_content.build_narration(itinerary, day_texts)

    # All days resolved from authored content — no Bedrock call at all.
    if not missing:
        return master_narr, "master", []

    if narrate_fn is None:
        basic = _basic_narration(missing)
        if master_narr:
            return _merge_by_day(master_narr, basic), "master_basic", []
        return basic, "basic", []

    # Some (or all) days need the LLM. Narrate only the missing days, then
    # merge with any authored lines, preserving trip-day order.
    llm_text, ungrounded = grounding_mod.ground_narration("".join(narrate_fn(missing)), missing)
    if not master_narr:
        return llm_text, "llm", ungrounded

    merged = _merge_by_day(master_narr, llm_text)
    return merged, "mixed", ungrounded


def _merge_by_day(master_narr: str, llm_narr: str) -> str:
    """Merge two 'Day N: ...' narrations into one, ordered by day number.
    Master blocks win if a day appears in both. A block runs from its 'Day N:' line to the next
    one: a master day is its title line plus prose lines, all kept (AA-819; only the title line
    used to survive the merge)."""

    def to_map(text: str) -> dict[int, str]:
        blocks: dict[int, list[str]] = {}
        current: Optional[list[str]] = None  # None: outside a block, or a repeated day (ignored)
        for line in text.splitlines():
            m = re.match(r"\s*Day\s*(\d+)\s*:", line, re.IGNORECASE)
            if m:
                day = int(m.group(1))
                current = None if day in blocks else blocks.setdefault(day, [line.strip()])
            elif current is not None and line.strip():
                current.append(line.strip())
        return {d: "\n".join(block) for d, block in blocks.items()}

    days = to_map(llm_narr)
    days.update(to_map(master_narr))  # master overrides
    return "\n".join(days[d] for d in sorted(days))


async def _is_registered(conn, session_id: str) -> bool:
    row = await conn.fetchrow(
        "SELECT customer_id FROM tripplanner.sessions WHERE id = $1", session_id
    )
    return bool(row and row["customer_id"])


async def _last_event(conn, trip_id: str):
    return await conn.fetchrow(
        "SELECT id, event_type FROM tripplanner.trip_events "
        "WHERE trip_id = $1 ORDER BY id DESC LIMIT 1",
        trip_id,
    )


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

async def serve(method: str, path: str, body: dict, *, guest_token: Optional[str] = None,
                acquire=None) -> dict:
    """route() on a pooled connection, behind a catch-all (AA-827): an unexpected error answers 500
    and logs only its type, where it was raised and the route name — never str(e), which can carry
    request values (asyncpg echoes bad input). `acquire` is injectable for tests."""
    try:
        if acquire is None:
            from backend.shared.db import get_pool
            acquire = (await get_pool()).acquire
        async with acquire() as conn:
            return await route(method, path, body, conn=conn, guest_token=guest_token)
    except Exception as e:  # noqa: BLE001
        frames = traceback.extract_tb(e.__traceback__)
        at = f"{os.path.basename(frames[-1].filename)}:{frames[-1].lineno}" if frames else ""
        print(json.dumps({"evt": "assembly_error", "route": _route_name(method, path),
                          "error": type(e).__name__, "at": at}), flush=True)
        return _resp(500, {"error": "internal"})


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
    if not isinstance(body, dict):
        body = {}
    # GET routes read their options from the query string (e.g. suggestion filters).
    for k, v in (event.get("queryStringParameters") or {}).items():
        body.setdefault(k, v)
    return method, path, body


def handler(event, context):  # pragma: no cover - thin AWS adapter
    import asyncio

    from backend.shared import auth

    # AA-675: operator-only extraction checks, reached by a direct `lambda invoke` (IAM-authorised).
    # Read-only since AA-712 slice 6a (writer ops answer 410). API Gateway events always carry
    # requestContext, so this branch is not reachable over HTTP.
    if "extraction" in event and "requestContext" not in event:
        from backend.extraction.lambda_entry import run_extraction_event
        return asyncio.get_event_loop().run_until_complete(run_extraction_event(event["extraction"]))

    denied = auth.check_event(event)
    if denied is not None:
        return denied

    method, path, body = _extract_request(event)
    # AA-827: the BFF's guest token (raw; guest_session rejects a malformed one before any SQL).
    guest_token = auth._headers_lower(event).get(config.GUEST_TOKEN_HEADER)  # noqa: SLF001

    return asyncio.get_event_loop().run_until_complete(
        serve(method, path, body, guest_token=guest_token))
