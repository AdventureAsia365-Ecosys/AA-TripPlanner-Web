"""Advisor notification.

Sends to a single configured address (config.ADVISOR_NOTIFY_EMAIL) via a
swappable Sender. The transport (SES, SMTP, log) is injected, so tests use
a fake sender and no email is actually sent. The address is read from
config in exactly one place — never inlined elsewhere.
"""
from __future__ import annotations

import json
from typing import Protocol

from backend import config


class Sender(Protocol):
    def send(self, *, to: str, subject: str, body: str) -> None: ...


class LoggingSender:
    """Default no-transport sender: records messages, sends nothing.

    Swap for an SES/SMTP sender at deploy time. Kept as default so the
    system is safe to run before a real transport is wired.
    """

    def __init__(self) -> None:
        self.sent: list[dict] = []

    def send(self, *, to: str, subject: str, body: str) -> None:
        self.sent.append({"to": to, "subject": subject, "body": body})


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
