"""AA-1034 R0 (root ADR 0019 d8/d11, B2B-only model): while config.ADVISOR_HANDOFF_DEMO is on (the default),
send-to-advisor answers 410 ``b2b_only`` before any SQL. No lead is stored (no registration, no ``sent`` event, no
inquiry.submitted outbox row), so Booking never opens a case for it."""
from __future__ import annotations

import json

import pytest

from backend import config
from backend.assembly import events, notify
from backend.tests.test_assembly import S1, T1, FakeConn, _session, call


class RecordingConn(FakeConn):
    """FakeConn that also records every statement, to prove the refusal runs no SQL at all."""

    def __init__(self):
        super().__init__()
        self.statements: list[str] = []

    async def fetch(self, sql, *args):
        self.statements.append(sql)
        return await super().fetch(sql, *args)

    async def fetchrow(self, sql, *args):
        self.statements.append(sql)
        return await super().fetchrow(sql, *args)

    async def fetchval(self, sql, *args):
        self.statements.append(sql)
        return await super().fetchval(sql, *args)

    async def execute(self, sql, *args):
        self.statements.append(sql)
        return await super().execute(sql, *args)


@pytest.mark.parametrize("raw, expected", [
    (None, True), ("", True), ("true", True), ("TRUE", True), ("yes", True), ("flase", True),
    ("false", False), (" False ", False),
])
def test_the_demo_flag_is_on_unless_exactly_false(raw, expected):
    assert config.demo_flag(raw) is expected


@pytest.mark.asyncio
async def test_send_answers_410_b2b_only_and_stores_nothing(monkeypatch):
    monkeypatch.setattr(config, "ADVISOR_HANDOFF_DEMO", True)
    monkeypatch.setattr(config, "REQUIRE_REGISTRATION_BEFORE_HANDOFF", True)
    recording = RecordingConn()
    recording.sessions[S1] = _session(S1, None)
    await events.append_event(recording, T1, S1, "add_component", {"component_id": "c1"})
    events_before = list(recording.events)
    recording.statements = []
    sender = notify.LoggingSender()
    resp = await call(
        "POST", f"/trip/{T1}/send-to-advisor",
        {"session_id": S1, "customer": {"name": "Ira Example", "email": "ira@example.test", "consent": True}},
        conn=recording, sender=sender,
    )
    assert resp["statusCode"] == 410
    body = json.loads(resp["body"])
    assert body["error"] == "b2b_only" and "demo" in body["detail"]
    assert recording.statements == []          # no SQL: no guard read, no registration, no event, no outbox row
    assert recording.outbox == [] and recording.customers == []
    assert recording.events == events_before
    assert sender.sent == []                  # no advisor notification


@pytest.mark.asyncio
async def test_other_trip_routes_keep_working_in_demo(monkeypatch):
    monkeypatch.setattr(config, "ADVISOR_HANDOFF_DEMO", True)
    conn = FakeConn()
    conn.sessions[S1] = _session(S1, None)
    resp = await call("POST", f"/trip/{T1}/components", {"session_id": S1, "component_id": "c1"}, conn=conn)
    assert resp["statusCode"] == 200


@pytest.mark.asyncio
async def test_demo_off_restores_the_hand_off(monkeypatch):
    monkeypatch.setattr(config, "ADVISOR_HANDOFF_DEMO", False)
    monkeypatch.setattr(config, "REQUIRE_REGISTRATION_BEFORE_HANDOFF", False)
    conn = FakeConn()
    conn.sessions[S1] = _session(S1, None)
    await events.append_event(conn, T1, S1, "add_component", {"component_id": "c1"})
    resp = await call("POST", f"/trip/{T1}/send-to-advisor", {"session_id": S1}, conn=conn)
    assert resp["statusCode"] == 200 and len(conn.outbox) == 1
