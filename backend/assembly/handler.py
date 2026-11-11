"""Lambda B entrypoint — Trip Assembly.

Routes:
  GET    /trip/{trip_id}                          current itinerary + added days + stops AA no longer serves
  GET    /trip/{trip_id}/suggestions              next-to-pin suggestions
  GET    /trip/{trip_id}/coverage                 AA tour leg chain
  GET    /trip/{trip_id}/stops/{dest}/activities  AA activities at one stop
  POST   /trip/{trip_id}/components               add_component
  DELETE /trip/{trip_id}/components/{component_id} remove_component
  PATCH  /trip/{trip_id}/reorder                  reorder
  POST   /trip/{trip_id}/send-to-advisor          sent (+ registration)
  POST   /trip/{trip_id}/narrate                  compose/renarrate (LLM)
  POST   /trip/{trip_id}/chat                     planning chat bound to AA tours (LLM, AA-1041 slice 2)
  POST   /me/claim                                sign-in: link this browser's guest sessions to the account
  GET    /me/trips                                My Trips: the signed-in traveller's trips

Every mutating route runs _guard (AA-827) right after its required-field check: the guest token
from the BFF must match the session (guest_session.py), and the session must own the trip.
session_id is an identifier, never a credential. Every trip read GET runs _read_guard (AA-909): the
token must match the session that owns the trip, else the same 404 as an unknown route.

AA-908 (root ADR 0006 Phase 4): a signed-in traveller's BFF also sends the EXTERNAL access token
(`x-tripplanner-access`, account.py). Both guards then also let through the account that owns the
session (a second device has its own guest cookie), and a signed-in mutation links its session to the
account. The /me routes need that token (401 sign_in_required without it). Without a valid token
every answer is exactly the guest one.

Stateful per-visitor; never cached. The framework-agnostic route()
coroutine takes injectable conn + sender and is unit-tested directly.
Narration (compose/renarrate) is triggered by the frontend via a separate
streaming call after the debounce window; these mutation endpoints return
the updated projection immediately.
"""
from __future__ import annotations

import dataclasses
import functools
import json
import os
import re
import traceback
import uuid
from typing import Any, Optional

from backend import config
from backend.assembly import agent as agent_mod
from backend.assembly import chat_tools
from backend.assembly import plan_chat
from backend.assembly import events as events_mod
from backend.assembly import grounding as grounding_mod
from backend.assembly import guest_session
from backend.assembly import leads as leads_mod
from backend.assembly import handoff as handoff_mod
from backend.assembly import master_content
from backend.assembly import narration_cache
from backend.assembly import notify as notify_mod
from backend.assembly import registration as reg_mod
from backend.assembly import account as account_mod
from backend.assembly import activities as activities_mod
from backend.assembly import coverage as coverage_mod
from backend.assembly import suggestions as suggest_mod
from backend.shared import component_ids, llm_gateway, m2m_auth, observability, ratelimit, tenant, tenant_assertion
from backend.shared import monthly_spend, tenant_budget

_COMPONENTS_RE = re.compile(r"^/trip/([^/]+)/components$")
_COMPONENT_ITEM_RE = re.compile(r"^/trip/([^/]+)/components/([^/]+)$")
_REORDER_RE = re.compile(r"^/trip/([^/]+)/reorder$")
_SEND_RE = re.compile(r"^/trip/([^/]+)/send-to-advisor$")
# AA-1034 R0: the answer of send-to-advisor while config.ADVISOR_HANDOFF_DEMO is on (the planner shows its demo notice).
B2B_ONLY_DETAIL = (
    "This planner is a demo: Adventure Asia works with travel agencies, so trips are not sent to an advisor here."
)
_NARRATE_RE = re.compile(r"^/trip/([^/]+)/narrate$")
_CHAT_RE = re.compile(r"^/trip/([^/]+)/chat$")
_SUGGEST_RE = re.compile(r"^/trip/([^/]+)/suggestions$")
_COVERAGE_RE = re.compile(r"^/trip/([^/]+)/coverage$")
_DAYS_RE = re.compile(r"^/trip/([^/]+)/days$")
_APPLY_ROUTE_RE = re.compile(r"^/trip/([^/]+)/apply-route$")
_DAY_ITEM_RE = re.compile(r"^/trip/([^/]+)/days/([^/]+)$")
_STOP_ACTIVITIES_RE = re.compile(r"^/trip/([^/]+)/stops/([^/]+)/activities$")
_TRIP_RE = re.compile(r"^/trip/([^/]+)$")
_TRIP_PREFIX_RE = re.compile(r"^/trip/([^/]+)")
_ME_CLAIM = "/me/claim"
_ME_TRIPS = "/me/trips"

# (method, path pattern, route name) — the name is what logs carry, never ids.
_ROUTE_NAMES = (
    ("GET", _TRIP_RE, "get_trip"),
    ("POST", _COMPONENTS_RE, "add"),
    ("DELETE", _COMPONENT_ITEM_RE, "remove"),
    ("PATCH", _REORDER_RE, "reorder"),
    ("POST", _SEND_RE, "send"),
    ("POST", _NARRATE_RE, "narrate"),
    ("POST", _CHAT_RE, "chat"),
    ("GET", _SUGGEST_RE, "suggestions"),
    ("POST", _APPLY_ROUTE_RE, "apply_route"),
    ("POST", _DAYS_RE, "add_day"),
    ("DELETE", _DAY_ITEM_RE, "remove_day"),
    ("GET", _STOP_ACTIVITIES_RE, "activities"),
    ("GET", _COVERAGE_RE, "coverage"),
    ("POST", re.compile(r"^/me/claim$"), "me_claim"),
    ("GET", re.compile(r"^/me/trips$"), "me_trips"),
    ("GET", re.compile(r"^/trip/m2m/v1/tenants/[^/]+/leads$"), "m2m_leads"),
    ("POST", re.compile(r"^/trip/m2m/v1/tenants/[^/]+/leads/[^/]+/ack$"), "m2m_lead_ack"),
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
    caller: account_mod.Caller = account_mod.GUEST,
    client: ratelimit.Client = ratelimit.UNKNOWN_CLIENT,
) -> dict:
    sender = sender or notify_mod.LoggingSender()

    # AA-908: the signed-in traveller's own routes.
    if path in (_ME_CLAIM, _ME_TRIPS):
        return await _me(method, path, conn=conn, guest_token=guest_token, caller=caller)

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
        if denied := await _read_guard(conn, trip_id, guest_token, "get_trip", caller):
            return denied
        itinerary, missing = await events_mod.current_itinerary_and_missing(conn, trip_id)
        custom_days = await events_mod.current_custom_days(conn, trip_id)
        # AA-986 L6 (ADR 0015 decision 5): every served stop says available: true; the stops whose tour AA no
        # longer serves are listed apart, available: false with a reason, so the planner can show a banner and
        # grey them without changing the map, the legs or the narration (which use the served stops only).
        unavailable = await events_mod.unavailable_stops(conn, trip_id, missing)
        return _resp(200, {"trip_id": trip_id,
                           "itinerary": [{**e, "available": True, "reason": None} for e in itinerary],
                           "unavailable_stops": unavailable, "custom_days": custom_days})

    m = _COMPONENTS_RE.match(path)
    if m and method == "POST":
        trip_id = m.group(1)
        session_id = body.get("session_id")
        component_id = body.get("component_id")
        if not session_id or not component_id:
            return _resp(400, {"error": "session_id and component_id required"})
        if denied := await _guard(conn, trip_id, session_id, guest_token, "add", caller):
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
        if denied := await _guard(conn, trip_id, session_id, guest_token, "remove", caller):
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
        if denied := await _guard(conn, trip_id, session_id, guest_token, "reorder", caller):
            return denied
        itinerary = await events_mod.append_event(
            conn, trip_id, session_id, "reorder",
            {"ordered_component_ids": ordered},
        )
        return _resp(200, {"trip_id": trip_id, "itinerary": itinerary})

    m = _SEND_RE.match(path)
    if m and method == "POST":
        # AA-1038 R5 slice 3 (root ADR 0019 d11): on an agency tenant's planner (a verified tenant assertion, never the
        # bare path or the AA demo tenant) the request becomes a lead.submitted.v1 for THAT tenant (leads.py).
        cur = tenant.current()
        if cur is not None and cur.source == tenant.SOURCE_ASSERTION and not tenant_budget.exempt(cur.tenant_id):
            return await _send_lead(conn, m.group(1), body, guest_token, caller, cur.tenant_id, client=client)
        # AA-1034 R0 (root ADR 0019): B2B-only, so no lead is stored and nothing goes to Booking. Before the guard and
        # any SQL: the answer is the same for every caller and reveals nothing about the trip.
        if config.ADVISOR_HANDOFF_DEMO:
            return _resp(410, {"error": "b2b_only", "detail": B2B_ONLY_DETAIL})
        trip_id = m.group(1)
        session_id = body.get("session_id")
        if not session_id:
            return _resp(400, {"error": "session_id required"})
        if denied := await _guard(conn, trip_id, session_id, guest_token, "send", caller):
            return denied
        customer = body.get("customer") or {}
        if not isinstance(customer, dict):
            customer = {}

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
            traveller = None
            if caller.signed_in:
                # AA-908: a signed-in send hands off the token's verified email and identity (inquiry.submitted
                # v1.4). The name, phone and consent come from this request, else from the session's earlier
                # registration. No tripplanner.customers row is written or matched: the identity is the link.
                stored = await handoff_mod.load_customer(conn, session_id) or {}
                name = str(customer.get("name") or stored.get("name") or "").strip()
                if config.REQUIRE_REGISTRATION_BEFORE_HANDOFF and not name:
                    return _resp(422, {"error": "registration_required", "detail": "name required before sending"})
                consent = customer.get("consent") is True if customer else bool(stored.get("consent"))
                traveller = handoff_mod.signed_in_contact(
                    caller.principal, name=name, phone=customer.get("phone") or stored.get("phone"),
                    consent=consent)
            elif config.REQUIRE_REGISTRATION_BEFORE_HANDOFF and not registered:
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
            # AA-986 L6: a stop whose tour AA no longer serves does not stop the hand-off; it goes along flagged
            # (available: false + reason), so staff see why that leg cannot be matched.
            await handoff_mod.write_inquiry(
                conn, trip_id=trip_id, handoff_id=handoff["id"], session_id=session_id,
                itinerary=itinerary, custom_days=await events_mod.current_custom_days(conn, trip_id),
                submitted=submitted, unavailable=await events_mod.unavailable_stops(conn, trip_id),
                traveller=traveller,
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
        if denied := await _guard(conn, trip_id, session_id, guest_token, "narrate", caller):
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

        day_texts = await _load_master_texts(conn, itinerary)
        # AA-1015 (S262): every day has AA's own master text -> answer it at once. No model call can happen, so the
        # spend counter, the route config, the per-session limit and the narration cache are not touched (the cache
        # would save nothing: building this text is in memory from the day texts just loaded, which the cache key
        # needs anyway). Such an answer is never `cached` and never flagged `ai_budget_exhausted` / `ai_paused`:
        # nothing was degraded, and the planner must not show "days without Adventure Asia's own text" notices.
        master_narr, missing = master_content.build_narration(itinerary, day_texts)
        if not missing:
            return _resp(200, {"trip_id": trip_id, "mode": mode, "source": "master",
                               "ai_generated": "master" in _AI_SOURCES, "cached": False,
                               "narration": master_narr, "itinerary": itinerary})

        # AA-805: an identical itinerary reuses its narration (no model call, no log row). The
        # master day text is part of the key, so a CIS edit to a tour never serves stale text.
        cache_key = narration_cache.itinerary_hash(itinerary, mode, day_texts)
        cached = await narration_cache.get(conn, cache_key)
        if cached is not None:
            return _resp(200, {"trip_id": trip_id, "mode": mode, "source": cached["source"],
                               "ai_generated": cached["source"] in _AI_SOURCES, "cached": True,
                               "narration": cached["narration"], "itinerary": itinerary})

        # AA-805: daily spend hard stop. Exhausted -> no model call; days without AA text get a
        # neutral line built from the components' names.
        # AA-642 S0 (root ADR 0009 decisions 5 and 6): the same degrade mode when AI is paused: the spend
        # counter or the route config cannot be read (fail closed, no model call), or every model of the
        # route failed (provider outage). The answer is 200 basic / master_basic with `ai_paused`, never 502.
        budget = await llm_gateway.budget_status(conn, agent_mod.STAGE)  # AA-1015: + the compose stage cap
        calls: list = []
        paused: Optional[str] = budget.get("reason") if budget.get("reason") != llm_gateway.REASON_CAP else None
        narrate_fn = None
        # AA-1025 H1b: the route (and with it the app profile's AI switch, `load_route`) is resolved before the limit,
        # so a request AI-off or route-unreadable refuses is degraded and never counted.
        if not budget["exhausted"]:
            try:
                narrate_fn = await _resolve_narrator(mode, narrator, conn, calls)
            except llm_gateway.AiPaused as e:
                paused = e.reason
        # AA-1015: 10 model narrations per hour per session (twice that signed in). Counted only when this request
        # would call the model: a cached answer, an all-AA-text trip (answered above), a stopped budget or paused AI
        # costs nothing.
        if narrate_fn is not None:
            decision = await ratelimit.hit(conn, ratelimit.SCOPE_NARRATE, ratelimit.KIND_SESSION, session_id,
                                           signed_in=caller.signed_in)
            if not decision.allowed:
                return {"statusCode": 429, "headers": ratelimit.limited_headers(decision, _HEADERS),
                        "body": json.dumps(ratelimit.limited_body(decision))}
        try:
            narration, source, ungrounded = await _narrate(conn, itinerary, narrate_fn,
                                                           day_texts=day_texts)
        except llm_gateway.BedrockError as e:
            # Every attempt failed (each is in `calls` as a failed row), or the route deadline passed (its 'deadline'
            # row too) or every reply was cut at its token limit ('truncated' rows, AA-642 S263 E1): degrade, do not
            # cache.
            await _record_calls(conn, calls, {"mode": mode, "days": len(itinerary), "degraded": True})
            calls = []
            paused = getattr(e, "reason", None) or "provider_unavailable"
            narration, source, ungrounded = await _narrate(conn, itinerary, None, day_texts=day_texts)
        except Exception as e:  # noqa: BLE001 — anything else is a defect: surface it as 502
            await _record_calls(conn, calls, {"mode": mode, "days": len(itinerary)})
            return _resp(502, {"error": "narration_failed", "detail": str(e)})
        signal = {"mode": mode, "source": source, "days": len(itinerary),
                  "output_len_chars": len(narration)}
        if ungrounded:  # AA-731: invented figures removed before the customer saw them
            signal["ungrounded_lines"] = len(ungrounded)
        await _record_calls(conn, calls, signal)  # AA-685: one llm_call_log row per model call
        # A degraded answer is basic / master_basic, never cached (narration_cache.CACHEABLE_SOURCES).
        await narration_cache.put(conn, cache_key, mode, narration, source, len(itinerary))

        out = {"trip_id": trip_id, "mode": mode, "source": source,
               "ai_generated": source in _AI_SOURCES, "cached": False,
               "narration": narration, "itinerary": itinerary}
        if budget["exhausted"] and not paused:
            out["ai_budget_exhausted"] = True
        if paused and source in ("basic", "master_basic"):
            out["ai_paused"] = True
            observability.emit("narration_degraded", reason=paused, source=source, days=len(itinerary))
        return _resp(200, out)

    m = _CHAT_RE.match(path)
    if m and method == "POST":
        return await _chat(conn, m.group(1), body, guest_token, caller, client)

    m = _SUGGEST_RE.match(path)
    if m and method == "GET":
        trip_id = m.group(1)
        if denied := await _read_guard(conn, trip_id, guest_token, "suggestions", caller):
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
        if denied := await _guard(conn, trip_id, session_id, guest_token, "apply_route", caller):
            return denied
        # AA-847: a route from a page loaded before the PLACES_SOURCE flip carries old component ids
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
        if denied := await _guard(conn, trip_id, session_id, guest_token, "add_day", caller):
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
        if denied := await _guard(conn, trip_id, session_id, guest_token, "remove_day", caller):
            return denied
        if day_id not in {d["day_id"] for d in await events_mod.current_custom_days(conn, trip_id)}:
            return _resp(404, {"error": "no such added day"})
        await events_mod.append_event(conn, trip_id, session_id, "remove_day", {"day_id": day_id})
        return _resp(200, {"trip_id": trip_id,
                           "custom_days": await events_mod.current_custom_days(conn, trip_id)})

    m = _STOP_ACTIVITIES_RE.match(path)
    if m and method == "GET":
        trip_id, destination_id = m.group(1), m.group(2)
        if denied := await _read_guard(conn, trip_id, guest_token, "activities", caller):
            return denied
        return _resp(200, {"trip_id": trip_id, **await activities_mod.at_stop(conn, trip_id, destination_id)})

    m = _COVERAGE_RE.match(path)
    if m and method == "GET":
        trip_id = m.group(1)
        if denied := await _read_guard(conn, trip_id, guest_token, "coverage", caller):
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
                 route_name: str, caller: account_mod.Caller = account_mod.GUEST) -> Optional[dict]:
    """AA-827: the request's guest token must match the session (401), and the session must own
    the trip (403; a trip with no draft yet has no owner). Returns the denial response, or None.
    Creates the sessions row on first use, so trip_events' FK fails loudly if a route skips it.

    AA-908: when the token does not prove the session, a signed-in caller whose account owns the
    session passes instead (ACCOUNT_OK: My Trips opened on another device). When the token does prove
    it, a signed-in caller's account claims the session if no account owns it yet."""
    outcome = await guest_session.authorize(conn, session_id, guest_token)
    if outcome in guest_session.DENIED and caller.signed_in \
            and await account_mod.owns_session(conn, caller.principal, session_id):
        outcome = guest_session.ACCOUNT_OK
    if outcome in guest_session.DENIED:
        guest_session.emit(outcome, route_name)
        return _resp(401, {"error": "guest_session_invalid"})
    owner = await _trip_owner(conn, trip_id)
    if owner is not None and str(owner) != str(session_id):
        guest_session.emit(guest_session.FORBIDDEN_OWNER, route_name)
        return _resp(403, {"error": "forbidden", "detail": "this trip belongs to another session"})
    if caller.signed_in and outcome in (guest_session.TOKEN_OK, guest_session.TOKEN_NEW):
        await account_mod.link_session(conn, caller.principal, session_id)
    guest_session.emit(outcome, route_name)
    return None


async def _read_guard(conn, trip_id: str, guest_token: Optional[str],
                      route_name: str, caller: account_mod.Caller = account_mod.GUEST) -> Optional[dict]:
    """AA-909: a trip read needs the token bound to the session that owns the trip. Any refusal
    (no token, malformed, wrong, another trip's, no such trip, no draft yet) is the unknown-route
    404, so existence never leaks. Returns the denial response, or None.

    AA-908: a signed-in caller whose account owns that session may read too (ACCOUNT_READ_OK).
    Another account, or a bad access token, gets the same 404."""
    outcome = await guest_session.authorize_read(conn, trip_id, guest_token)
    if outcome != guest_session.READ_OK and caller.signed_in \
            and await account_mod.owns_trip(conn, caller.principal, trip_id):
        outcome = guest_session.ACCOUNT_READ_OK
    guest_session.emit(outcome, route_name)
    return None if outcome in (guest_session.READ_OK, guest_session.ACCOUNT_READ_OK) else _not_found()


async def _me(method: str, path: str, *, conn, guest_token: Optional[str],
              caller: account_mod.Caller) -> dict:
    """AA-908: POST /me/claim (at sign-in) and GET /me/trips (My Trips). Both need a valid access
    token of the EXTERNAL tripplanner client: 401 sign_in_required otherwise (503 when no key set
    could ever be loaded). Neither ever reveals another account's data."""
    route_name = "me_claim" if path == _ME_CLAIM else "me_trips"
    if method != ("POST" if route_name == "me_claim" else "GET"):
        return _not_found()
    if caller.state == account_mod.UNAVAILABLE:
        return _resp(503, {"error": "auth_unavailable"})
    if not caller.signed_in:
        observability.emit("traveller_account", route=route_name, outcome="sign_in_required")
        return _resp(401, {"error": "sign_in_required"})
    if route_name == "me_trips":
        trips = await account_mod.list_trips(conn, caller.principal)
        observability.emit("traveller_account", route=route_name, outcome="ok", trips=len(trips))
        return _resp(200, {"trips": trips})
    # A malformed guest token claims nothing (never sent to SQL); the account row is still upserted.
    token_hash = guest_session.hash_token(guest_token) if guest_session.is_token(guest_token) else None
    async with conn.transaction():
        claimed = await account_mod.claim(conn, caller.principal, token_hash)
    observability.emit("traveller_account", route=route_name, outcome="ok", claimed=claimed)
    return _resp(200, {"claimed": claimed})


# AA-1041 slice 2: the planning chat. The reasons a turn is not run, by what the planner tells the traveller.
_CHAT_BUDGET_REASONS = frozenset({monthly_spend.REASON_APP_CAP, monthly_spend.REASON_TENANT_CAP,
                                  llm_gateway.REASON_CAP, tenant_budget.REASON_CAP})


def _chat_paused(trip_id: str, reason: Optional[str]) -> dict:
    """200 `status: paused`: the chat does not answer now. `reason` is "budget" (a money cap is reached: the planner
    says the chat is paused until next month / later) or "unavailable" (AI paused, the provider down, a spend read
    failed); the detail code goes to the log line only."""
    kind = "budget" if reason in _CHAT_BUDGET_REASONS else "unavailable"
    observability.emit("plan_chat_paused", reason=kind, detail=reason)
    return _resp(200, {"trip_id": trip_id, "status": "paused", "reason": kind})


async def _chat_gate(conn) -> Optional[str]:
    """None when a chat model call may run now, else why not: the chat's monthly caps (app, then tenant), then the
    daily app caps and the tenant's AI budget (`budget_status`). Fails closed."""
    tid = tenant.current_id()
    reason = await monthly_spend.check(conn, plan_chat.STAGE, tid,
                                       app_cap_usd=config.TP_PLAN_CHAT_MONTHLY_CAP_USD,
                                       tenant_cap_usd=config.TP_PLAN_CHAT_TENANT_MONTHLY_CAP_USD)
    if reason is not None:
        return reason
    budget = await llm_gateway.budget_status(conn, plan_chat.STAGE)
    if budget.get("exhausted"):
        return budget.get("tenant_reason") or budget.get("reason") or llm_gateway.REASON_CAP
    return None


async def _chat(conn, trip_id: str, body: dict, guest_token: Optional[str], caller: account_mod.Caller,
                client: ratelimit.Client) -> dict:
    """POST /trip/{id}/chat {session_id, message, history?} (backend/assembly/plan_chat.py). The session must own the
    trip (_guard: a chat turn costs model spend). Order, so that nothing that cannot reach a model is counted: the
    money gates (monthly, daily, tenant budget), the route (AI switch), then the per-session and per-IP limits (429),
    then the turn. The chat writes no trip state: the planner applies a proposal through the existing mutations."""
    session_id, message = body.get("session_id"), body.get("message")
    if not session_id or not isinstance(message, str) or not message.strip():
        return _resp(400, {"error": "session_id and message required"})
    if len(message.strip()) > plan_chat.MAX_MESSAGE_CHARS:
        return _resp(400, {"error": "message_too_long", "max_chars": plan_chat.MAX_MESSAGE_CHARS})
    if denied := await _guard(conn, trip_id, session_id, guest_token, "chat", caller):
        return denied
    reason = await _chat_gate(conn)
    if reason is not None:
        return _chat_paused(trip_id, reason)
    try:
        route_ = await llm_gateway.load_route(conn, plan_chat.STAGE, borrow_from=plan_chat.LENDER_STAGE)
    except llm_gateway.AiPaused as e:
        return _chat_paused(trip_id, e.reason)
    limits = [(ratelimit.SCOPE_CHAT, ratelimit.KIND_SESSION, session_id)]
    if client.ip:
        limits.append((ratelimit.SCOPE_CHAT_IP, ratelimit.KIND_IP, client.ip))
    for scope, kind, value in limits:
        decision = await ratelimit.hit(conn, scope, kind, value, signed_in=caller.signed_in or client.signed_in)
        if not decision.allowed:
            return {"statusCode": 429, "headers": ratelimit.limited_headers(decision, _HEADERS),
                    "body": json.dumps(ratelimit.limited_body(decision))}

    itinerary = await events_mod.current_itinerary(conn, trip_id)
    offered = await chat_tools.countries(conn)
    calls: list = []
    started = plan_chat.clock()

    def generate(system: str, user: str) -> str:
        # The turn's wall time bounds every call: its route deadline is the time left (the HTTP API stops at 30 s).
        left = plan_chat.TURN_DEADLINE_S - (plan_chat.clock() - started)
        if left < plan_chat.MIN_CALL_S:
            raise plan_chat.OutOfTime()
        bounded = route_ if route_.deadline_s and route_.deadline_s <= left else dataclasses.replace(
            route_, deadline_s=int(left))
        text, call = llm_gateway.generate_text(bounded, system, user, plan_chat.MAX_TOKENS, failures=calls)
        calls.append(call)
        return text

    gated_once = False

    async def before_call() -> Optional[str]:
        nonlocal gated_once
        if not gated_once:            # the gate above already passed for the first call
            gated_once = True
            return None
        return await _chat_gate(conn)

    tid = tenant.current_id()
    try:
        result = await plan_chat.run_turn(conn, message=message, history=plan_chat.clean_history(body.get("history")),
                                          itinerary=itinerary, offered=offered, generate=generate,
                                          before_call=before_call)
    except llm_gateway.BedrockError as e:
        # Every attempt failed, the route deadline passed or a circuit is open: each attempt is a ledger row, billed
        # ones count against the month; the traveller gets the "unavailable" pause.
        await _record_calls(conn, calls, {"feature": "plan_chat", "degraded": True})
        await monthly_spend.charge(conn, plan_chat.STAGE, tid, *llm_gateway.billed_totals(calls))
        return _chat_paused(trip_id, getattr(e, "reason", None) or "provider_unavailable")
    await _record_calls(conn, calls, result.signal())
    await monthly_spend.charge(conn, plan_chat.STAGE, tid, *llm_gateway.billed_totals(calls))
    if result.outcome == plan_chat.OUTCOME_PAUSED:
        return _chat_paused(trip_id, result.paused_reason)
    observability.emit("plan_chat_turn", **{k: v for k, v in result.signal().items() if k != "feature"})
    return _resp(200, {"trip_id": trip_id, "status": "ok", "outcome": result.outcome, "reply": result.reply,
                       "ai_generated": result.from_model, "proposal": result.proposal})


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


async def _send_lead(conn, trip_id: str, body: dict, guest_token: Optional[str], caller: account_mod.Caller,
                     tenant_id: str, *, client: ratelimit.Client = ratelimit.UNKNOWN_CLIENT,
                     **delivery: Any) -> dict:
    """Send to advisor on an agency tenant's planner (AA-1038 slice 3): one lead.submitted.v1 for the bound tenant.
    Refused before anything is stored when the answers are invalid, the tenant has no usable route (for crm: the CRM
    has not pulled this tenant lately), the session or the traveller's IP sent too often (429 rate_limited), the
    tenant reached its daily lead cap (429 lead_cap_reached) or the trip is empty. A double click (latest event already
    `sent`) answers the same hand-off. No customer row: a guest."""
    session_id = body.get("session_id")
    if not session_id:
        return _resp(400, {"error": "session_id required"})
    if denied := await _guard(conn, trip_id, session_id, guest_token, "send", caller):
        return denied
    try:
        answers = leads_mod.validate(body.get("lead"))
        route_ = await leads_mod.load_route(conn, tenant_id)
        if not leads_mod.channel_ready(route_):
            raise leads_mod.LeadRefused(503, "lead_channel_unavailable")
        if route_.channel == "crm" and not await leads_mod.crm_pull_fresh(conn, tenant_id):
            # The CRM is not pulling this tenant (agency locked / offboarding / not provisioned, or the pull is
            # down): a stored lead would only wait for the hold to end it.
            raise leads_mod.LeadRefused(503, "lead_channel_unavailable")
    except leads_mod.LeadRefused as e:
        observability.emit("lead_refused", code=e.code, tenant_id=tenant_id)
        return _resp(e.status, e.body())
    # Counted only when the request would store a lead (same mechanism as narrate): per session, then per IP.
    limits = [(ratelimit.SCOPE_SEND, ratelimit.KIND_SESSION, session_id)]
    if client.ip:
        limits.append((ratelimit.SCOPE_SEND_IP, ratelimit.KIND_IP, client.ip))
    for scope, kind, value in limits:
        decision = await ratelimit.hit(conn, scope, kind, value, signed_in=caller.signed_in or client.signed_in)
        if not decision.allowed:
            observability.emit("lead_refused", code="rate_limited", scope=scope, tenant_id=tenant_id)
            return {"statusCode": 429, "headers": ratelimit.limited_headers(decision, _HEADERS),
                    "body": json.dumps(ratelimit.limited_body(decision))}
    async with conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"tp_send:{trip_id}")
        last = await _last_event(conn, trip_id)
        if last and last["event_type"] == "sent":
            itinerary = await events_mod.current_itinerary(conn, trip_id)
            return _resp(200, {"trip_id": trip_id, "status": "sent", "handoff_id": last["id"],
                               "already_sent": True, "itinerary": itinerary})
        current = await events_mod.current_itinerary(conn, trip_id)
        added = await events_mod.current_custom_days(conn, trip_id)
        if not current and not added:
            return _resp(400, {"error": "empty_trip", "detail": "add a stop or a day before sending"})
        if await leads_mod.tenant_leads_24h(conn, tenant_id) >= config.TP_LEAD_TENANT_DAILY_CAP:
            observability.emit("lead_refused", code="lead_cap_reached", tenant_id=tenant_id,
                               cap=config.TP_LEAD_TENANT_DAILY_CAP)
            return _resp(429, {"error": "lead_cap_reached"})
        # Added days count as days; their notes (free text) never go into the lead.
        summary = await leads_mod.trip_summary(conn, current, extra_days=len(added))
        itinerary = await events_mod.append_event(conn, trip_id, session_id, "sent", {"session_id": session_id})
        handoff = await _last_event(conn, trip_id)
        payload = leads_mod.build_lead(route=route_, trip_id=trip_id, answers=answers, summary=summary)
        row_id = await leads_mod.write_lead(conn, trip_id=trip_id, payload=payload, channel=route_.channel)
    observability.emit("lead_stored", channel=route_.channel, tenant_id=tenant_id)
    if row_id is not None and route_.channel in ("webhook", "email"):
        # After the commit: a slow or failing tenant endpoint never loses the lead (the daily run retries it).
        await leads_mod.deliver_now(conn, row_id, route_, payload, **delivery)
    return _resp(200, {"trip_id": trip_id, "status": "sent", "handoff_id": handoff["id"], "already_sent": False,
                       "itinerary": itinerary, "lead": {"lead_id": payload["lead_id"], "channel": route_.channel}})


# --- the CRM's lead pull (AA-1038 slice 3, contract lead.submitted.v1, M2M; no edge key) --------------------------
_M2M_PREFIX = "/trip/m2m/"
_M2M_LEADS_RE = re.compile(r"^/trip/m2m/v1/tenants/([^/]+)/leads$")
_M2M_ACK_RE = re.compile(r"^/trip/m2m/v1/tenants/([^/]+)/leads/([^/]+)/ack$")


def is_m2m_path(path: str) -> bool:
    return path.startswith(_M2M_PREFIX)


async def serve_m2m(method: str, path: str, body: dict, authorization: Optional[str], *, acquire=None,
                    verify=None) -> dict:
    """GET /trip/m2m/v1/tenants/{tenant_id}/leads and POST .../leads/{lead_id}/ack. The bearer is verified before any
    SQL; the connection is then bound to the path's tenant (source m2m), so the tp-025 policy filters as well.

    The CRM client is a platform credential (one client, every tenant): only a tenant whose route resolves to crm NOW
    is served (else 404 tenant_not_on_crm), so a leaked token reaches no tenant that left the CRM add-on or was never
    on it. A list call is the tenant's beacon (leads.mark_crm_pull): the crm channel takes leads only while it is
    fresh."""
    verify = verify or m2m_auth.verify_bearer
    try:
        await verify(authorization)
    except m2m_auth.Refused as e:
        observability.emit("m2m_refused", code=e.code)
        return _resp(e.status, {"error": e.code})
    m_list, m_ack = _M2M_LEADS_RE.match(path), _M2M_ACK_RE.match(path)
    if not (m_list and method == "GET") and not (m_ack and method == "POST"):
        return _resp(404, {"error": "not_found"})
    tenant_raw = (m_list or m_ack).group(1)
    if not leads_mod.is_uuid(tenant_raw):
        return _resp(400, {"error": "invalid_tenant_id"})
    if m_ack and not leads_mod.is_uuid(m_ack.group(2)):
        return _resp(400, {"error": "invalid_lead_id"})
    limit = 20
    if m_list:
        raw = body.get("limit", 20)
        if not str(raw).isdigit() or not 1 <= int(raw) <= leads_mod.PAGE_MAX:
            return _resp(400, {"error": "invalid_limit"})
        limit = int(raw)
    outcome: Optional[str] = None
    reason: Optional[str] = None
    if m_ack:
        try:
            outcome, reason = leads_mod.valid_ack(body)
        except ValueError:
            return _resp(400, {"error": "invalid_ack"})
    try:
        if acquire is None:
            from backend.shared.db import get_pool
            acquire = (await get_pool()).acquire
        async with acquire() as conn, tenant.bound(conn, tenant_raw, source=tenant.SOURCE_M2M) as tid:
            try:
                route_ = await leads_mod.load_route(conn, tid)
            except leads_mod.LeadRefused as e:
                if e.status == 503:
                    return _resp(503, {"error": "unavailable"})
                route_ = None
            if route_ is None or route_.channel != "crm":
                observability.emit("m2m_tenant_not_on_crm", tenant_id=tid)
                return _resp(404, {"error": "tenant_not_on_crm"})
            if m_list:
                page = await leads_mod.list_pending(conn, tid, limit)
                await leads_mod.mark_crm_pull(conn, tid)
                return _resp(200, page)
            ack = await leads_mod.acknowledge(conn, tid, m_ack.group(2), outcome or "stored", reason)
            return _resp(200, ack) if ack is not None else _resp(404, {"error": "lead_not_found"})
    except Exception as e:  # noqa: BLE001
        observability.emit("assembly_error", route="m2m_leads", error=type(e).__name__)
        observability.capture(e)
        return _resp(503, {"error": "unavailable"})


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
      1. Look up authored per-day text from catalog.v_tour.itinerary_text
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
                access_token: Optional[str] = None, acquire=None, tenant_id: Optional[str] = None,
                tenant_source: str = tenant.SOURCE_DEFAULT,
                client: ratelimit.Client = ratelimit.UNKNOWN_CLIENT) -> dict:
    """route() on a pooled connection, behind a catch-all (AA-827): an unexpected error answers 500
    and logs only its type, where it was raised and the route name — never str(e), which can carry
    request values (asyncpg echoes bad input). `acquire` is injectable for tests.

    AA-908: `access_token` is the raw `x-tripplanner-access` header; it is verified before any SQL
    (account.caller_from_header). The JWKS is fetched on the first signed-in request of a container
    and kept (a failed refresh keeps the last good key set).

    AA-1038 (R5 slice 1, tp-025 shadow RLS): the tenant context is bound right after acquire, before route(), and
    unbound in a finally even when route() raises (tenant.bound). Every request is the demo tenant
    (config.DEFAULT_TENANT_ID) unless `tenant_id` is given. A tenant id that is not a uuid fails here, before a
    connection is taken or any SQL runs.

    AA-1038 slice 2: `tenant_id` comes only from a verified BFF tenant assertion (_handle,
    backend/shared/tenant_assertion.py), source "assertion"; None = the bare path = the default tenant."""
    try:
        caller = await account_mod.caller_from_header(access_token)
        tid = tenant.canonical(config.DEFAULT_TENANT_ID if tenant_id is None else tenant_id)
        if acquire is None:
            from backend.shared.db import get_pool
            acquire = (await get_pool()).acquire
        async with acquire() as conn, tenant.bound(conn, tid, source=tenant_source):
            return await route(method, path, body, conn=conn, guest_token=guest_token, caller=caller, client=client)
    except Exception as e:  # noqa: BLE001
        frames = traceback.extract_tb(e.__traceback__)
        at = f"{os.path.basename(frames[-1].filename)}:{frames[-1].lineno}" if frames else ""
        observability.emit("assembly_error", route=_route_name(method, path), error=type(e).__name__, at=at)
        observability.capture(e)  # AA-871: Sentry, scrubbed (no-op when SENTRY_DSN is unset)
        return _resp(500, {"error": "internal"})


def tenant_assertion_answer(headers: dict[str, str]) -> dict:
    """{"tenant": serve() kwargs} for a request without an assertion (the default tenant) or with a valid one, else
    {"response": 401 | 503} (forged, altered, expired, over-long; or the key cannot be read). No SQL either way."""
    try:
        checked = tenant_assertion.from_headers(headers)
    except tenant_assertion.AssertionRefused:
        return {"response": tenant_assertion.refused_response()}
    except tenant_assertion.AssertionKeyUnavailable:
        return {"response": tenant_assertion.unavailable_response()}
    if checked is None:
        return {"tenant": {}}
    return {"tenant": {"tenant_id": checked.tenant_id, "tenant_source": tenant.SOURCE_ASSERTION}}


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


def _handle(event, context):  # pragma: no cover - thin AWS adapter
    import asyncio

    from backend.shared import auth

    # AA-675: operator-only extraction checks, reached by a direct `lambda invoke` (IAM-authorised).
    # Read-only since AA-712 slice 6a (writer ops answer 410). API Gateway events always carry
    # requestContext, so this branch is not reachable over HTTP.
    if "extraction" in event and "requestContext" not in event:
        from backend.extraction.lambda_entry import run_extraction_event
        return asyncio.get_event_loop().run_until_complete(run_extraction_event(event["extraction"]))

    method, path, body = _extract_request(event)
    # AA-1038 slice 3: the CRM's lead pull carries an M2M bearer token instead of the BFF's edge key (m2m_auth.py);
    # every other route keeps the edge key check below.
    if is_m2m_path(path):
        authorization = auth._headers_lower(event).get("authorization")  # noqa: SLF001
        return asyncio.get_event_loop().run_until_complete(serve_m2m(method, path, body, authorization))

    denied = auth.check_event(event)
    if denied is not None:
        return denied

    # AA-827: the BFF's guest token (raw; guest_session rejects a malformed one before any SQL).
    headers = auth._headers_lower(event)  # noqa: SLF001
    guest_token = headers.get(config.GUEST_TOKEN_HEADER)
    # AA-908: the signed-in traveller's EXTERNAL access token, forwarded by the BFF (never logged).
    access_token = headers.get(config.ACCESS_TOKEN_HEADER)
    # AA-1038 slice 2: the tenant of a /{slug} request, signed by the BFF; checked before any connection or SQL.
    picked = tenant_assertion_answer(headers)
    if "response" in picked:
        return picked["response"]

    # AA-1038 slice 3: the traveller's IP the BFF forwards (trusted only with the edge key) for the send limits.
    client = ratelimit.client_from_headers(headers)
    return asyncio.get_event_loop().run_until_complete(
        serve(method, path, body, guest_token=guest_token, access_token=access_token, client=client,
              **picked["tenant"]))


# AA-871: request id (BFF header > API Gateway requestId), one JSON request_completed line, Sentry when SENTRY_DSN set.
handler = observability.instrument(_handle, "assembly", _route_name)
