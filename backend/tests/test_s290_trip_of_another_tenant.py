"""S290 follow-up (S289 review of the tenant isolation): a fresh session of agency B that writes to agency A's trip id
reaches the trip_drafts upsert, which row-level security refuses (SQLSTATE 42501) because A's row is hidden. That was a
500 with rollback; it is now the same 403 as a trip owned by another session. No database: the RLS refusal is faked
on the upsert. On PostgreSQL (tp-028 FORCE RLS): tests/test_aa1038_tenant_site_pg.py."""
from __future__ import annotations

import json
from contextlib import asynccontextmanager

import pytest

from backend.assembly import events, handler
from backend.tests.test_assembly import S1, T1, FakeConn, _session, call


class _Refused(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__("refused")
        self.sqlstate = sqlstate


class HiddenDraftConn(FakeConn):
    """The upsert of trip_drafts fails with ``sqlstate`` (a row of another tenant, hidden by RLS)."""

    def __init__(self, sqlstate: str = events.RLS_REFUSED) -> None:
        super().__init__()
        self.sqlstate = sqlstate

    async def execute(self, query, *args):
        if " ".join(query.split()).startswith("INSERT INTO tripplanner.trip_drafts"):
            self.sql.append("refused")
            raise _Refused(self.sqlstate)
        return await super().execute(query, *args)


async def test_the_rls_refusal_of_the_draft_upsert_is_a_typed_error():
    conn = HiddenDraftConn()
    with pytest.raises(events.TripOfAnotherTenant):
        await events.append_event(conn, T1, S1, "add_day", {"day_id": "d1", "kind": "free_day"})


async def test_another_database_error_still_propagates():
    conn = HiddenDraftConn(sqlstate="23505")
    with pytest.raises(_Refused):
        await events.append_event(conn, T1, S1, "add_day", {"day_id": "d1", "kind": "free_day"})


async def test_route_raises_it_and_serve_answers_403_not_500(monkeypatch, capsys):
    conn = HiddenDraftConn()
    conn.sessions[S1] = _session(S1)
    with pytest.raises(events.TripOfAnotherTenant):
        await call("POST", f"/trip/{T1}/days", {"session_id": S1, "kind": "free_day"}, conn=conn)

    async def route(method, path, body, **kw):
        raise events.TripOfAnotherTenant()

    @asynccontextmanager
    async def acquire():
        yield conn

    monkeypatch.setattr(handler, "route", route)
    out = await handler.serve("POST", f"/trip/{T1}/days", {"session_id": S1, "kind": "free_day"}, acquire=acquire)
    assert out["statusCode"] == 403
    assert json.loads(out["body"]) == {"error": "forbidden", "detail": "this trip belongs to another session"}
    lines = [json.loads(ln) for ln in capsys.readouterr().out.splitlines() if ln.startswith("{")]
    assert not any(ln["evt"] == "assembly_error" for ln in lines)
    assert {"evt": "guest_auth", "outcome": "forbidden_owner", "route": "add_day"} in lines
