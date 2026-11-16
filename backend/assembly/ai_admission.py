"""The last gates before a traveller-facing model call of the assembly Lambda (narration, planning chat), in order:
the per-visitor limits (AA-1015, ``shared/ratelimit.py``: 429) and then the tenant's monthly AI sessions quota
(AA-1116, ``shared/tenant_sessions.py``: the session is counted on its first AI call of the month). The money gates
(daily app caps, tenant AI budget, the chat's monthly caps) and the route run before these, so nothing that cannot
reach a model is counted. Moved out of ``handler.py`` (AA-1116) so the route stays within its code-health baseline.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Optional

from backend.shared import ratelimit, tenant, tenant_sessions


def limited_response(decision: ratelimit.Decision, headers: dict) -> dict:
    """The 429 answer of a refused per-visitor limit (``retry-after`` header and the limit body)."""
    return {"statusCode": 429, "headers": ratelimit.limited_headers(decision, headers),
            "body": json.dumps(ratelimit.limited_body(decision))}


async def narration(conn: Any, session_id: str, signed_in: bool, narrate_fn: Callable, budget: dict,
                    paused: Optional[str], headers: dict) -> tuple[Optional[dict], Optional[Callable], dict,
                                                                   Optional[str]]:
    """(429 response or None, narrate_fn, budget, paused) for a narration that would call the model. A session the
    quota refuses gets no model: a reached quota reads as a reached budget (``ai_budget_exhausted``), an unreadable one
    as a pause (``ai_paused``)."""
    decision = await ratelimit.hit(conn, ratelimit.SCOPE_NARRATE, ratelimit.KIND_SESSION, session_id,
                                   signed_in=signed_in)
    if not decision.allowed:
        return limited_response(decision, headers), narrate_fn, budget, paused
    stop = await tenant_sessions.admit(conn, tenant.current_id(), session_id)
    if stop == tenant_sessions.REASON_CAP:
        return None, None, {**budget, "exhausted": True, "reason": stop}, paused
    if stop is not None:
        return None, None, budget, stop
    return None, narrate_fn, budget, paused


async def chat(conn: Any, session_id: str, signed_in: bool, client: ratelimit.Client, headers: dict
               ) -> tuple[Optional[dict], Optional[str]]:
    """(429 response or None, the quota's stop reason or None) for a chat turn: the per-session and per-IP limits, then
    the sessions quota."""
    limits = [(ratelimit.SCOPE_CHAT, ratelimit.KIND_SESSION, session_id)]
    if client.ip:
        limits.append((ratelimit.SCOPE_CHAT_IP, ratelimit.KIND_IP, client.ip))
    for scope, kind, value in limits:
        decision = await ratelimit.hit(conn, scope, kind, value, signed_in=signed_in)
        if not decision.allowed:
            return limited_response(decision, headers), None
    return None, await tenant_sessions.admit(conn, tenant.current_id(), session_id)
