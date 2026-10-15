"""Advisor notification — TripPlanner side (log only, no e-mail).

TripPlanner sends no e-mail. The staff "new inquiry" e-mail is sent by AA-Booking (job
``bk_notify_email``, AA-811, S238 decision) when it processes the ``inquiry.submitted`` outbox row
that the same hand-off transaction writes (handoff.py). ADR 0004 boundary: TripPlanner is the front
door, Booking owns customers, trip cases and notifications.

What stays here is one log line per hand-off (CloudWatch shows that a trip was sent) behind a
swappable Sender, so tests can inspect the message. ``config.ADVISOR_NOTIFY_EMAIL`` is optional and
only labels the log line; it defaults to empty and no transport reads it.
"""
from __future__ import annotations

import json
import logging
from typing import Protocol

from backend import config

log = logging.getLogger(__name__)


class Sender(Protocol):
    def send(self, *, to: str, subject: str, body: str) -> None: ...


class LoggingSender:
    """The only sender: records the message and logs one line. Sends nothing (AA-Booking e-mails staff)."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    def send(self, *, to: str, subject: str, body: str) -> None:
        self.sent.append({"to": to, "subject": subject, "body": body})
        log.info("advisor hand-off logged, not e-mailed (AA-Booking sends the staff e-mail): %s", subject)


def _format_body(trip_id: str, event_log: list[dict]) -> str:
    lines = [f"Trip {trip_id} was sent to an advisor.", "", "Event history:"]
    for e in event_log:
        lines.append(f"- {e.get('event_type')}: {json.dumps(e.get('payload'))}")
    return "\n".join(lines)


async def notify_advisor(
    trip_id: str,
    event_log: list[dict],
    *,
    sender: Sender,
) -> None:
    sender.send(
        to=config.ADVISOR_NOTIFY_EMAIL,
        subject=f"[AA TripPlanner] New trip to review: {trip_id}",
        body=_format_body(trip_id, event_log),
    )
