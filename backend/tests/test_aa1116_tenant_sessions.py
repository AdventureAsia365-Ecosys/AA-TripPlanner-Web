"""AA-1116 (option B, S282 decision 10, offline): the monthly AI sessions quota of an agency tenant
(``backend/shared/tenant_sessions.py``; CIS 438 ``catalog.v_tenant_tripplanner_v1.ai_sessions_monthly``, tp-032
``tripplanner.ai_session_month``).

A session is counted on its first AI call of the UTC month; a counted session continues past the quota; a new one is
refused at the quota (NULL = no limit, still counted); the quota unreadable, no row, the count unreadable or the write
failing stop NEW sessions (fail closed); the bare path and the AA demo tenant are exempt. Narration degrades to the
basic text with ``ai_budget_exhausted`` and the chat answers ``paused`` / ``budget``. The same SQL on PostgreSQL:
tests/test_aa1116_tenant_sessions_pg.py. Invented ids only.
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager

import pytest

from backend import config
from backend.assembly import events
from backend.shared import llm_gateway, tenant, tenant_budget
from backend.shared import tenant_sessions as ts
from backend.tests.test_assembly import S1, T1, AiConn, call

AGENCY = "7a1c0de5-0000-4000-8000-000000001116"
SESSION_A = "7a1c0de5-0000-4000-8000-0000000000a1"
SESSION_B = "7a1c0de5-0000-4000-8000-0000000000b2"
SESSION_C = "7a1c0de5-0000-4000-8000-0000000000c3"


@pytest.fixture(autouse=True)
def _clean():
    ts.clear()
    yield
    ts.clear()


class PgError(Exception):
    def __init__(self, sqlstate):
        super().__init__("synthetic")
        self.sqlstate = sqlstate


class SessionsDb:
    """The quota view row and the tp-032 table of one tenant, in memory."""

    def __init__(self, quota=300, *, row=True, quota_error=None, count_error=None, write_error=None):
        self.quota, self.row = quota, row
        self.quota_error, self.count_error, self.write_error = quota_error, count_error, write_error
        self.sessions: set[tuple[str, str]] = set()
        self.quota_reads, self.locks = 0, 0

    async def fetchrow(self, sql, *args):
        assert sql == ts.QUOTA_SQL
        self.quota_reads += 1
        if self.quota_error:
            raise self.quota_error
        return {"tenant_id": args[0], "ai_sessions_monthly": self.quota} if self.row else None

    async def fetchval(self, sql, *args):
        if sql == ts.COUNTED_SQL:
            if self.count_error:
                raise self.count_error
            return 1 if (args[0], args[1]) in self.sessions else None
        if sql == ts.COUNT_SQL:
            return sum(1 for s, _ in self.sessions if s == args[0])
        raise AssertionError(sql)

    async def execute(self, sql, *args):
        if sql == ts.LOCK_SQL:
            self.locks += 1
            return "SELECT 1"
        if sql == ts.ADD_SQL:
            if self.write_error:
                raise self.write_error
            self.sessions.add((args[0], args[1]))
            return "INSERT 0 1"
        raise AssertionError(sql)

    @asynccontextmanager
    async def transaction(self):
        yield self


@pytest.mark.asyncio
async def test_new_sessions_are_counted_up_to_the_quota_and_a_counted_one_continues():
    db = SessionsDb(quota=2)
    assert await ts.admit(db, AGENCY, SESSION_A) is None
    assert await ts.admit(db, AGENCY, SESSION_A) is None                   # the same session: not counted twice
    assert await ts.admit(db, AGENCY, SESSION_B) is None
    assert len(db.sessions) == 2
    assert await ts.admit(db, AGENCY, SESSION_C) == ts.REASON_CAP          # the third new session of the month
    assert await ts.admit(db, AGENCY, SESSION_A) is None                   # a counted session continues past it
    assert len(db.sessions) == 2 and db.quota_reads == 1                   # the quota read is cached


@pytest.mark.asyncio
@pytest.mark.parametrize("quota,refused", [(None, False), (0, True), (1, False)])
async def test_quota_values(quota, refused):
    db = SessionsDb(quota=quota)
    assert (await ts.admit(db, AGENCY, SESSION_A) == ts.REASON_CAP) is refused
    assert len(db.sessions) == (0 if refused else 1)                         # NULL = no limit, still counted


@pytest.mark.asyncio
@pytest.mark.parametrize("db", [
    SessionsDb(quota_error=PgError("42703")),          # CIS 438 not applied: closed, not open
    SessionsDb(quota_error=PgError("08006")),
    SessionsDb(row=False),                             # the tenant has no row in the view
    SessionsDb(count_error=PgError("42P01")),          # tp-032 not applied
    SessionsDb(write_error=PgError("42501")),
], ids=["no-438", "conn", "no-row", "no-tp032", "no-grant"])
async def test_fails_closed_for_a_new_session(db):
    assert await ts.admit(db, AGENCY, SESSION_A) == ts.REASON_UNREADABLE


@pytest.mark.asyncio
async def test_a_counted_session_continues_even_when_the_quota_is_unreadable():
    db = SessionsDb(quota=5)
    assert await ts.admit(db, AGENCY, SESSION_A) is None
    ts.clear()
    db.quota_error = PgError("08006")
    assert await ts.admit(db, AGENCY, SESSION_A) is None
    assert await ts.admit(db, AGENCY, SESSION_B) == ts.REASON_UNREADABLE


@pytest.mark.asyncio
async def test_the_demo_tenant_and_the_bare_path_are_exempt():
    db = SessionsDb(quota_error=PgError("42P01"), count_error=PgError("42P01"))
    assert await ts.admit(db, None, SESSION_A) is None
    assert await ts.admit(db, config.DEMO_TENANT_ID, SESSION_A) is None
    assert await ts.admit(db, AGENCY, None) is None
    assert db.quota_reads == 0 and db.locks == 0


def test_sql_reads_the_438_column_and_the_utc_month():
    assert "ai_sessions_monthly" in ts.QUOTA_SQL and "*" not in ts.QUOTA_SQL
    assert "now() AT TIME ZONE 'UTC'" in ts.COUNT_SQL and "ON CONFLICT" in ts.ADD_SQL
    assert ts.scope(AGENCY) == f"tenant:{AGENCY}"


# ── The narration and the chat on a refused session ──────────────────────────


@pytest.fixture
def agency_ai(monkeypatch):
    """An agency tenant whose money gates are open; the sessions quota answers what the test sets."""
    answer: dict = {"reason": None}

    async def budget_status(db, stage=None):
        return {"exhausted": False, "reason": None}

    async def admit(db, tenant_id, session_id):
        answer["seen"] = (tenant_id, session_id)
        return answer["reason"]

    monkeypatch.setattr(llm_gateway, "budget_status", budget_status)
    monkeypatch.setattr(ts, "admit", admit)
    tenant_budget.clear()
    with tenant.using(AGENCY, source=tenant.SOURCE_ASSERTION):
        yield answer


def _narrator(calls):
    def narrator(it):
        calls.append(len(it))
        yield "\n".join(f"Day {e['day']}: lovely." for e in it)
    return narrator


@pytest.mark.asyncio
@pytest.mark.parametrize("reason,flag", [(ts.REASON_CAP, "ai_budget_exhausted"), (ts.REASON_UNREADABLE, "ai_paused")])
async def test_narration_degrades_when_the_session_is_refused(agency_ai, reason, flag):
    agency_ai["reason"] = reason
    conn = AiConn()
    await events.append_event(conn, T1, S1, "add_component", {"component_id": "c1"})
    calls: list = []
    resp = await call("POST", f"/trip/{T1}/narrate", {"session_id": S1}, conn=conn, narrator=_narrator(calls))
    body = json.loads(resp["body"])
    assert resp["statusCode"] == 200 and calls == []                     # no model call
    assert body[flag] is True and body["source"] in ("basic", "master_basic")
    assert agency_ai["seen"] == (AGENCY, S1)


@pytest.mark.asyncio
async def test_narration_runs_when_the_session_is_admitted(agency_ai):
    conn = AiConn()
    await events.append_event(conn, T1, S1, "add_component", {"component_id": "c1"})
    calls: list = []
    resp = await call("POST", f"/trip/{T1}/narrate", {"session_id": S1}, conn=conn, narrator=_narrator(calls))
    assert resp["statusCode"] == 200 and calls == [1]
    assert "ai_budget_exhausted" not in json.loads(resp["body"])


@pytest.mark.asyncio
@pytest.mark.parametrize("allowed,stop,expect", [
    (True, None, (None, None)),
    (True, ts.REASON_CAP, (None, ts.REASON_CAP)),
    (False, None, (429, None)),                                     # a refused limit never counts the session
])
async def test_chat_admission_order(monkeypatch, allowed, stop, expect):
    from backend.assembly import ai_admission, handler
    from backend.shared import ratelimit

    admitted: list = []

    async def hit(db, scope, kind, value, *, signed_in=False):
        rule = ratelimit.rule_for(scope, signed_in=signed_in)
        return ratelimit.Decision(allowed, scope, rule.limit, rule.window_s, 1, 0 if allowed else 60)

    async def admit(db, tenant_id, session_id):
        admitted.append(session_id)
        return stop

    monkeypatch.setattr(ratelimit, "hit", hit)
    monkeypatch.setattr(ts, "admit", admit)
    client = ratelimit.Client(ip="203.0.113.7", signed_in=False)
    limited, reason = await ai_admission.chat(object(), SESSION_A, False, client, {})
    assert ((limited or {}).get("statusCode"), reason) == expect
    assert admitted == ([] if not allowed else [SESSION_A])
    if stop is not None:
        assert json.loads(handler._chat_paused(T1, reason)["body"])["reason"] == "budget"
