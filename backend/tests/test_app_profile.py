"""AA-1025 H1b — TripPlanner's app profile (CIS view catalog.v_llm_app_profile_v1, CIS 347): the read and its cache,
the AI switch in load_route (a 0 USD refused row, AiPaused("ai_off"), narration and search degrade) and the cap from
the profile (every tp_* stage, offline ones included). Invented stage names only; no network, no database."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from backend import config
from backend.aa_llm import ledger
from backend.assembly import agent
from backend.assembly import handler
from backend.browse import search as search_mod
from backend.shared import app_profile, llm_gateway as gw, ratelimit
from backend.tests.test_assembly import AiConn, S1, T1, call, events


class PgError(Exception):
    def __init__(self, sqlstate, message="pg error"):
        super().__init__(message)
        self.sqlstate = sqlstate


def _row(**kw):
    base = {"app": "tripplanner", "ai_enabled": True, "bulk_paused": False, "default_daily_budget_usd": None,
            "degrade_mode": "basic_narration", "updated_at": None}
    base.update(kw)
    return base


class ProfileDb:
    """Answers the profile SELECT with `row` (or raises `error`), counts the reads."""

    def __init__(self, row=None, error=None):
        self.row, self.error, self.reads = row, error, 0

    async def fetchrow(self, sql, *args):
        assert sql == app_profile.PROFILE_SQL
        self.reads += 1
        if self.error is not None:
            raise self.error
        return self.row


@pytest.fixture
def real_read(monkeypatch):
    """Undo conftest's default: read the profile through the test's db."""
    async def _read(db):
        return app_profile.profile_of(await db.fetchrow(app_profile.PROFILE_SQL))
    monkeypatch.setattr(app_profile, "read", _read)


def off(budget=None):
    """conftest's `read` replaced by a profile with AI off (and an optional budget)."""
    async def _read(db):
        return app_profile.AppProfile(ai_enabled=False, default_daily_budget_usd=budget, source="view")
    return _read


def budget_of(amount):
    async def _read(db):
        return app_profile.AppProfile(default_daily_budget_usd=amount, source="view")
    return _read


# --- the read ------------------------------------------------------------------------------------------------------

def test_the_select_names_the_contract_columns():
    assert app_profile.PROFILE_SQL == (
        "SELECT app, ai_enabled, bulk_paused, default_daily_budget_usd, degrade_mode, updated_at "
        "FROM catalog.v_llm_app_profile_v1 WHERE app = 'tripplanner'")


def test_profile_of_a_row_and_of_no_row():
    p = app_profile.profile_of(_row(ai_enabled=False, default_daily_budget_usd=Decimal("2.5000"), bulk_paused=True))
    assert (p.ai_enabled, p.bulk_paused, p.default_daily_budget_usd, p.source) == (False, True, 2.5, "view")
    assert app_profile.profile_of(None) == app_profile.DEFAULT
    assert app_profile.DEFAULT.ai_enabled is True and app_profile.DEFAULT.default_daily_budget_usd is None
    odd = app_profile.profile_of(_row(ai_enabled=None, default_daily_budget_usd="x", degrade_mode=None))
    assert odd.ai_enabled is True and odd.default_daily_budget_usd is None and odd.degrade_mode == "basic_narration"


async def test_current_caches_for_the_ttl(real_read, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(app_profile.time, "monotonic", lambda: now[0])
    db = ProfileDb(_row(ai_enabled=False))
    assert (await app_profile.current(db)).ai_enabled is False
    now[0] += app_profile.CACHE_TTL_SECONDS - 1
    await app_profile.current(db)
    assert db.reads == 1
    now[0] += 2
    db.row = _row()
    assert (await app_profile.current(db)).ai_enabled is True and db.reads == 2


async def test_a_missing_view_is_ai_on_and_retried_later(real_read, monkeypatch, capsys):
    now = [1000.0]
    monkeypatch.setattr(app_profile.time, "monotonic", lambda: now[0])
    db = ProfileDb(error=PgError("42P01", "relation does not exist"))
    assert await app_profile.current(db) == app_profile.DEFAULT
    await app_profile.current(db)
    assert db.reads == 1                                      # not retried within VIEW_RETRY_SECONDS
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["evt"] == "llm_gateway_note" and "347" in line["detail"]
    now[0] += app_profile.VIEW_RETRY_SECONDS + 1
    db.error, db.row = None, _row(ai_enabled=False)
    assert (await app_profile.current(db)).ai_enabled is False


async def test_another_read_error_fails_open_with_an_error_line(real_read, monkeypatch, capsys):
    now = [1000.0]
    monkeypatch.setattr(app_profile.time, "monotonic", lambda: now[0])
    db = ProfileDb(error=RuntimeError("connection reset"))
    p = await app_profile.current(db)
    assert p.ai_enabled is True and p.default_daily_budget_usd is None and p.source == "unreadable"
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["evt"] == "llm_gateway_error" and line["what"] == "app_profile_unreadable"
    now[0] += app_profile.FAILURE_TTL_SECONDS - 1
    await app_profile.current(db)
    assert db.reads == 1
    now[0] += 2
    await app_profile.current(db)
    assert db.reads == 2


async def test_the_env_switch_skips_the_read(real_read, monkeypatch):
    monkeypatch.setenv(app_profile.SWITCH_ENV, "off")
    db = ProfileDb(_row(ai_enabled=False))
    assert (await app_profile.current(db)).ai_enabled is True and db.reads == 0


# --- the AI switch in load_route -----------------------------------------------------------------------------------

class LedgerDb:
    """Records the ledger and counter writes."""

    def __init__(self):
        self.executed: list = []

    async def execute(self, sql, *args):
        self.executed.append((sql, args))

    def ledger_rows(self):
        return [dict(zip(ledger.COLUMNS, a)) for s, a in self.executed if "shared.llm_call_log" in s]


async def _route(db, stage):
    return gw.builtin_route(stage)


async def test_ai_off_refuses_with_a_zero_usd_refused_row(monkeypatch, capsys):
    monkeypatch.setattr(gw, "_load_route", _route)
    monkeypatch.setattr(app_profile, "read", off())
    db = LedgerDb()
    with pytest.raises(gw.AiPaused) as e:
        await gw.load_route(db, "tp_compose")
    assert e.value.reason == gw.REASON_AI_OFF == "ai_off"
    rows = db.ledger_rows()
    assert len(rows) == 1
    row = rows[0]
    assert (row["app"], row["stage"], row["role"], row["status"], row["error_class"]) == (
        "tripplanner", "tp_compose", "writer", "refused", "disabled")
    assert row["cost_usd"] == 0.0 and row["tokens_in"] == 0 and row["connection_key"] is None
    assert row["pricing_mode"] is None
    signal = json.loads(row["quality_signal"]) if isinstance(row["quality_signal"], str) else row["quality_signal"]
    assert signal["refused"] == "ai_off" and signal["not_called"] == "ai_off"
    # the core classifies the stored text 'disabled' too, like the CIS stage kill switch's
    assert ledger.failed_outcome(signal["error"]) == ("refused", "disabled")
    evt = [json.loads(x) for x in capsys.readouterr().out.splitlines() if '"llm_app_ai_off_refused"' in x]
    assert evt and evt[-1]["stage"] == "tp_compose"


async def test_ai_on_writes_nothing(monkeypatch):
    monkeypatch.setattr(gw, "_load_route", _route)
    db = LedgerDb()
    route = await gw.load_route(db, "tp_search_embed")
    assert route.stage == "tp_search_embed" and db.executed == []


async def test_narration_degrades_to_basic_when_ai_is_off(monkeypatch, capsys):
    monkeypatch.setattr(handler.llm_gateway, "_load_route", _route)
    monkeypatch.setattr(app_profile, "read", off())

    def no_model(*a, **kw):
        raise AssertionError("no model call when AI is off")

    async def no_limit(*a, **kw):
        raise AssertionError("a refused request is not counted by the per-session limit")

    monkeypatch.setattr(agent.llm_gateway, "generate_text", no_model)
    monkeypatch.setattr(ratelimit, "hit", no_limit)
    conn = AiConn()
    await events.append_event(conn, T1, S1, "add_component", {"component_id": "c1"})
    resp = await call("POST", f"/trip/{T1}/narrate", {"session_id": S1}, conn=conn)
    body = json.loads(resp["body"])
    assert resp["statusCode"] == 200 and body["source"] == "basic" and body["ai_paused"] is True
    assert "ai_budget_exhausted" not in body and conn.cache == {}
    evt = [json.loads(x) for x in capsys.readouterr().out.splitlines() if x.startswith('{"evt": "narration_degraded"')]
    assert evt and evt[-1]["reason"] == "ai_off"


class SearchPool:
    def __init__(self):
        self.executed: list = []

    async def execute(self, sql, *args):
        self.executed.append(sql)


async def test_search_answers_the_keyword_ranking_when_ai_is_off(monkeypatch):
    monkeypatch.setattr(gw, "_load_route", _route)
    monkeypatch.setattr(app_profile, "read", off())
    seen = {}

    async def keyword(q, filters, *, pool, limit, reason):
        seen["reason"] = reason
        return {"destinations": [], "ranking": "keyword"}

    def no_embed(*a, **kw):
        raise AssertionError("no embed call when AI is off")

    monkeypatch.setattr(search_mod, "keyword_search", keyword)
    monkeypatch.setattr(gw, "embed_texts", no_embed)
    pool = SearchPool()
    out = await search_mod.search("misty lake", search_mod.BrowseFilters(), pool=pool)
    assert seen["reason"] == "ai_off" and out["ai_paused"] is True and out["ranking"] == "keyword"
    assert any("shared.llm_call_log" in s for s in pool.executed)       # the refused row


# --- the cap from the profile --------------------------------------------------------------------------------------

class AppSpendDb:
    """The spend view: the whole-app read sums every stage, the stage-list read sums those stages."""

    def __init__(self, by_stage):
        self.by_stage, self.reads = by_stage, []

    async def fetchrow(self, sql, *args):
        if "stage = ANY" in sql:
            self.reads.append(list(args[0]))
            return {"spent": sum(v for k, v in self.by_stage.items() if k in args[0])}
        assert sql == gw._APP_SPEND_ALL_SQL and not args
        self.reads.append(None)
        return {"spent": sum(self.by_stage.values())}


async def test_the_profile_budget_caps_every_stage(monkeypatch):
    monkeypatch.setattr(config, "TP_DAILY_SPEND_CAP_USD", 5.0)
    monkeypatch.setattr(app_profile, "read", budget_of(2.0))
    db = AppSpendDb({"tp_compose": 0.5, "tp_zz_offline": 1.6})
    status = await gw.budget_status(db, "tp_compose")
    assert status["cap_usd"] == 2.0 and status["spent_usd"] == pytest.approx(2.1) and status["exhausted"] is True
    assert db.reads == [None]                                 # the whole-app read, offline stage included
    offline = await gw.budget_status(db, "tp_zz_offline")
    assert offline["exhausted"] is True                       # an offline stage is capped too


async def test_without_a_profile_budget_the_env_cap_over_online_stages_applies(monkeypatch):
    monkeypatch.setattr(config, "TP_DAILY_SPEND_CAP_USD", 5.0)
    db = AppSpendDb({"tp_compose": 0.5, "tp_zz_offline": 40.0})
    status = await gw.budget_status(db, "tp_compose")
    assert status["cap_usd"] == 5.0 and status["exhausted"] is False
    assert db.reads == [list(gw.ONLINE_STAGES)]


async def test_a_zero_budget_stops_every_call(monkeypatch):
    monkeypatch.setattr(app_profile, "read", budget_of(0.0))
    status = await gw.budget_status(AppSpendDb({}), "tp_search_embed")
    assert status["exhausted"] is True and status["reason"] == "cap_reached"


async def test_the_app_alert_names_its_scope_and_source(monkeypatch, capsys):
    monkeypatch.setattr(app_profile, "read", budget_of(1.0))
    await gw.budget_status(AppSpendDb({"tp_compose": 0.85}))
    alerts = [json.loads(x) for x in capsys.readouterr().out.splitlines() if '"llm_spend_alert"' in x]
    assert [(a["scope"], a["threshold"], a["cap_source"]) for a in alerts] == [("app", "near", "app_profile")]


@pytest.mark.parametrize("code", ["42P01", "42501", "57014"])
async def test_the_whole_app_spend_fails_closed_without_the_view(monkeypatch, code):
    """AA-1027 E5: no counter fallback. A missing, ungranted or failing spend view = spend_unreadable, and the only
    SQL run is the view read (never tripplanner.llm_spend_daily)."""
    monkeypatch.setattr(app_profile, "read", budget_of(3.0))

    class ViewFails:
        def __init__(self):
            self.sql = []

        async def fetchrow(self, sql, *args):
            self.sql.append(sql)
            raise PgError(code)

    db = ViewFails()
    status = await gw.budget_status(db)
    assert status["exhausted"] is True and status["reason"] == "spend_unreadable" and status["spent_usd"] is None
    assert status["cap_usd"] == 3.0
    assert db.sql == [gw._APP_SPEND_ALL_SQL]
    assert not any("llm_spend_daily" in q for q in db.sql)
