"""Circuit breakers of the TripPlanner gateway (AA-1027 E3, root plan S263; gaps TP-2, TP-3).

Two sources decide whether a deployment (connection x catalog model) may be called now; ``llm_gateway`` asks
``skip_class`` before each attempt.

1. **Shared state, read only.** AA-CIS-App's gateway keeps one circuit per deployment in ``shared.llm_provider_health``
   (CIS migration 309, ``shared/llm_client/breaker.py``) and derives a connection-level circuit from it (AA-1018).
   TripPlanner cannot read or write that table; CIS migration 341 appends both to the contract view
   ``catalog.v_llm_gateway_config_v1`` (``deployment_routing``), so ``Deployment`` carries them. Rules:

   - a tripped connection circuit (``connection_circuit_class`` set: an account-wide refusal, e.g. Bedrock
     "Error 002" on the LLM account) skips every deployment of the connection, open **or half-open**. CIS lets a
     half-open connection through so that one of its own calls can probe it; TripPlanner takes no probe lease and
     writes no health row, so its call could never close the circuit and would only add a doomed attempt per Lambda
     container. A CIS call that answers on the connection (a job, the daily probe, the admin's Test connection) clears
     it, and TripPlanner sees that within its 60 s route cache;
   - a deployment circuit: skipped while open (``circuit_open_until`` in the future), and while half-open with
     another caller's probe lease (``probe_until`` in the future), exactly ``breaker.is_open`` in CIS.

2. **In-process (this module's ``local``), per Lambda container.** The view can be up to 60 s old and does not exist
   before CIS 341 is applied, so the container also counts its own failed attempts with the CIS rules (``RULES``: an
   account-level refusal opens at once, throttling after 3 in a row, anything else after 5; the open period doubles
   on each failed probe, capped). A refusal of the request's own content (input too long, malformed) is not counted.
   The container's connection circuit trips on one ``quota_exhausted`` / ``credential`` failure, or on
   ``access_denied`` from 2 distinct models (one model the account may not call is not an account block). A
   half-open circuit lets the next call through (one container serves one request at a time, so that call is the
   probe); its success closes the circuit, its failure opens it again for twice as long.

Pure state machine plus a small in-memory table; no I/O, never raises. Times: wall clock (epoch seconds) for the
shared timestamps (they come from the database), monotonic for the local circuits.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, replace
from typing import Callable, Optional

from backend.aa_llm import errors as core_errors

ACCESS_DENIED = core_errors.ACCESS_DENIED
QUOTA_EXHAUSTED = core_errors.QUOTA_EXHAUSTED
THROTTLED = core_errors.THROTTLED
CREDENTIAL = core_errors.CREDENTIAL
OTHER = core_errors.OTHER

CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"


@dataclass(frozen=True)
class Rule:
    threshold: int        # consecutive failures that open a closed circuit
    open_s: float         # the first open period; doubled on each failed probe
    max_open_s: float     # cap of the doubling


# The numbers of AA-CIS-App shared/llm_client/breaker.py RULES (AA-1002 S4).
RULES: dict[str, Rule] = {
    ACCESS_DENIED: Rule(threshold=1, open_s=300, max_open_s=1800),
    QUOTA_EXHAUSTED: Rule(threshold=1, open_s=300, max_open_s=1800),
    CREDENTIAL: Rule(threshold=1, open_s=120, max_open_s=900),
    THROTTLED: Rule(threshold=3, open_s=30, max_open_s=300),
    OTHER: Rule(threshold=5, open_s=60, max_open_s=600),
}
# Classes that trip the container's connection circuit from ONE deployment; access_denied needs 2 distinct models.
CONNECTION_SOLO_CLASSES = (CREDENTIAL, QUOTA_EXHAUSTED)
CONNECTION_DENIED_MODELS = 2
CONNECTION_CLASSES = (CREDENTIAL, QUOTA_EXHAUSTED, ACCESS_DENIED)

# A refusal of one request's content, never counted (CIS breaker._REQUEST_WORDING).
_REQUEST_WORDING = re.compile(
    r"validationexception"
    r"|\binvalid_request_error\b"
    r"|\brequest_too_large\b"
    r"|context_length_exceeded"
    r"|maximum context length"
    r"|input is too long"
    r"|prompt is too long"
    r"|too many (input )?tokens"
    r"|malformed input request"
    r"|content_filter",
    re.IGNORECASE)


def is_request_error(message: Optional[str]) -> bool:
    return bool(message) and bool(_REQUEST_WORDING.search(message or ""))


@dataclass(frozen=True)
class Health:
    """One deployment's local circuit. Times are monotonic seconds."""
    consecutive_failures: int = 0
    last_error_class: Optional[str] = None
    last_error_at: Optional[float] = None
    last_ok_at: Optional[float] = None
    open_until: Optional[float] = None
    open_streak: int = 0


def state_of(h: Optional[Health], now: float) -> str:
    if h is None or h.open_until is None:
        return CLOSED
    return OPEN if now < h.open_until else HALF_OPEN


def open_seconds(error_class: str, streak: int) -> float:
    rule = RULES.get(error_class, RULES[OTHER])
    return min(rule.open_s * (2 ** max(streak - 1, 0)), rule.max_open_s)


def after_failure(h: Health, error_class: str, now: float) -> Health:
    """CIS breaker.after_failure: open while open changes nothing but the counters; a failed half-open probe opens for
    twice as long; a closed circuit opens at its class's threshold."""
    cls = error_class if error_class in RULES else OTHER
    before = state_of(h, now)
    base = replace(h, last_error_at=now, last_error_class=cls, consecutive_failures=h.consecutive_failures + 1)
    if before == OPEN:
        return base
    if before == HALF_OPEN:
        streak = h.open_streak + 1
    elif base.consecutive_failures >= RULES[cls].threshold:
        streak = 1
    else:
        return base
    return replace(base, open_until=now + open_seconds(cls, streak), open_streak=streak)


def after_success(h: Health, now: float) -> Health:
    return Health(last_ok_at=now)


class LocalBreaker:
    """The container's circuits, keyed by (connection_key, model_key)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._rows: dict[tuple[str, str], Health] = {}

    def clear(self) -> None:
        self._rows.clear()

    def record_failure(self, connection_key: str, model_key: str, error_class: Optional[str],
                       message: Optional[str] = None) -> None:
        """A failed provider attempt. Not counted: a refusal of the request's content, or a class that is not a
        provider failure (truncated: the provider answered; deadline / budget: nothing was sent)."""
        if is_request_error(message):
            return
        cls = error_class if error_class in RULES else (OTHER if error_class in (None, OTHER) else None)
        if cls is None:
            return
        key = (connection_key, model_key)
        self._rows[key] = after_failure(self._rows.get(key, Health()), cls, self._clock())

    def record_success(self, connection_key: str, model_key: str) -> None:
        self._rows[(connection_key, model_key)] = after_success(self._rows.get((connection_key, model_key), Health()),
                                                                self._clock())

    def connection_open(self, connection_key: str) -> Optional[str]:
        """The class of the container's connection circuit while it is OPEN, else None (CIS breaker.connection_open on
        the local rows: account-wide classes newer than the connection's last answered call)."""
        now = self._clock()
        mine = [(m, h) for (c, m), h in self._rows.items() if c == connection_key]
        last_ok = max((h.last_ok_at for _, h in mine if h.last_ok_at is not None), default=None)
        signals = [(m, h) for m, h in mine
                   if h.last_error_class in CONNECTION_CLASSES and state_of(h, now) != CLOSED
                   and h.last_error_at is not None and (last_ok is None or h.last_error_at > last_ok)]
        solo = [(m, h) for m, h in signals if h.last_error_class in CONNECTION_SOLO_CLASSES]
        denied = [(m, h) for m, h in signals if h.last_error_class == ACCESS_DENIED]
        if len({m for m, _ in denied}) < CONNECTION_DENIED_MODELS:
            denied = []
        members = solo + denied
        if not members or now >= max(h.open_until for _, h in members):
            return None
        return next(c for c in CONNECTION_CLASSES if any(h.last_error_class == c for _, h in members))

    def open_class(self, connection_key: str, model_key: str) -> Optional[str]:
        """The class that keeps the deployment shut in this container (its own circuit OPEN, or the connection's),
        else None. Half-open answers None: the next call is the probe."""
        h = self._rows.get((connection_key, model_key))
        if h is not None and state_of(h, self._clock()) == OPEN:
            return h.last_error_class or OTHER
        return self.connection_open(connection_key)


local = LocalBreaker()


def shared_skip_class(*, connection_circuit_class: Optional[str], circuit_open_until: Optional[float],
                      probe_until: Optional[float], last_error_class: Optional[str], now: float) -> Optional[str]:
    """The class of the CIS circuit (shared state from the contract view) that skips the deployment, else None. See
    the module docstring: a tripped connection circuit skips open or half-open; a deployment circuit skips while open
    or while another caller holds its half-open probe."""
    if connection_circuit_class:
        return connection_circuit_class
    if circuit_open_until is not None:
        if now < circuit_open_until:
            return last_error_class or OTHER
        if probe_until is not None and now < probe_until:
            return last_error_class or OTHER
    return None
