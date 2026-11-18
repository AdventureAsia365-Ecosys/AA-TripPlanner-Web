"""AA-1126 (offline): TripPlanner's AI spend rules per environment (setting tripplanner.spend.environment, CIS 447) and
the agency notice when its AI sessions quota is used up.

dev (the default, today's behaviour): the app-wide caps (the daily cap, the chat's monthly app cap) stop the AI of
every agency, and a spend / budget / quota that cannot be read stops it too. prod: those app-wide caps only alert and a
read error degrades with an error line; an agency's own AI budget and AI sessions quota stay hard. When the quota
stops a new session TripPlanner records one ai.allowance_used outbox event a month (tp-034; CIS pulls it into the
agency's bell), and a degraded check under prod one ai.spend_degraded event a day (the CIS admin bell). The
traveller notice is neutral (frontend lib/aiNotices.ts). Invented ids only; no network, no database."""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest

from backend import config
from backend.shared import app_profile, monthly_spend, observability, settings, spend_bell, spend_rules, tenant
from backend.shared import llm_gateway as gw
from backend.shared import tenant_budget as tb
from backend.shared import tenant_sessions as ts

AGENCY = "6b2d1ef6-0000-4000-8000-000000001126"
SESSION = "6b2d1ef6-0000-4000-8000-0000000000a1"
NOV = datetime(2026, 11, 17, 8, 0, tzinfo=timezone.utc)


class PgError(Exception):
    def __init__(self, sqlstate):
        super().__init__("synthetic")
        self.sqlstate = sqlstate


@pytest.fixture
def lines(monkeypatch):
    out: list = []
    monkeypatch.setattr(observability, "emit", lambda evt, **kw: out.append((evt, kw)))
    return out


@pytest.fixture
def env(monkeypatch):
    """Set the spend environment: env("prod") / env("dev")."""
    def _set(value):
        monkeypatch.setattr(settings, "spend_prod", lambda: value == "prod")
    _set("dev")
    return _set


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    tb.clear()
    ts.clear()
    monkeypatch.setattr(gw, "_spend_unreadable_until", 0.0)

    async def default(_db):
        return app_profile.DEFAULT
    monkeypatch.setattr(app_profile, "current", default)
    yield
    tb.clear()
    ts.clear()


# ── the setting ──────────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_setting_is_an_enum_dev_or_prod_with_the_dev_default():
    s = settings.SETTINGS[settings.SPEND_ENVIRONMENT]
    assert s.kind == "enum" and s.allowed == ("dev", "prod") and s.fallback() == config.TP_SPEND_ENVIRONMENT == "dev"
    assert settings.problem(s, "prod") is None and settings.problem(s, "dev") is None
    assert settings.problem(s, "staging") and settings.problem(s, True)


@pytest.mark.asyncio
async def test_spend_prod_follows_the_view(monkeypatch):
    rows = [{"key": settings.SPEND_ENVIRONMENT, "value_text": '"prod"', "version": 2, "tenant_id": None}]

    async def read(db):
        return rows
    monkeypatch.setattr(settings, "read", read)
    settings.invalidate()
    await settings.refresh(object(), force=True)
    assert settings.spend_prod() is True
    rows[0]["value_text"] = '"staging"'                      # refused by its definition: the fallback (dev) serves
    settings.invalidate()
    await settings.refresh(object(), force=True)
    assert settings.spend_prod() is False
    settings.invalidate()


# ── G3 / L4: the daily app cap and the spend read (llm_gateway.budget_status) ────────────────────────────────────────

def _spent(monkeypatch, usd=None, error=None):
    async def spent(db, stages):
        if error:
            raise error
        return usd
    monkeypatch.setattr(gw, "_spent", spent)


def _tenant_check(monkeypatch, reason=None):
    async def check(db, tenant_id):
        return reason
    monkeypatch.setattr(tb, "check", check)


@pytest.mark.asyncio
async def test_dev_the_daily_app_cap_stops(monkeypatch, env, lines):
    _spent(monkeypatch, float(config.TP_DAILY_SPEND_CAP_USD) + 1)
    _tenant_check(monkeypatch)
    out = await gw.budget_status(object(), "tp_compose")
    assert out["exhausted"] is True and out["reason"] == gw.REASON_CAP


@pytest.mark.asyncio
async def test_prod_the_daily_app_cap_only_alerts(monkeypatch, env, lines):
    env("prod")
    _spent(monkeypatch, float(config.TP_DAILY_SPEND_CAP_USD) + 1)
    _tenant_check(monkeypatch)
    out = await gw.budget_status(object(), "tp_compose")
    assert out["exhausted"] is False and out["alert_only"] is True
    assert any(e == "llm_spend_alert" and kw.get("threshold") == "cap_alert_only" for e, kw in lines)


@pytest.mark.asyncio
async def test_prod_an_unreadable_spend_degrades_dev_fails_closed(monkeypatch, env, lines):
    _spent(monkeypatch, error=PgError("57014"))
    _tenant_check(monkeypatch)
    closed = await gw.budget_status(object(), "tp_compose")
    assert closed["exhausted"] is True and closed["reason"] == gw.REASON_SPEND_UNREADABLE
    env("prod")
    open_ = await gw.budget_status(object(), "tp_compose")
    assert open_["exhausted"] is False and open_["degraded"] == gw.REASON_SPEND_UNREADABLE
    assert any(kw.get("what") == "spend_unreadable_degraded" for _, kw in lines)


@pytest.mark.asyncio
async def test_prod_keeps_the_agency_budget_hard_and_degrades_its_read_error(monkeypatch, env, lines):
    env("prod")
    _spent(monkeypatch, 0.5)
    _tenant_check(monkeypatch, tb.REASON_CAP)
    with tenant.using(AGENCY, source=tenant.SOURCE_ASSERTION):
        capped = await gw.budget_status(object(), "tp_compose")
    assert capped["exhausted"] is True and capped["scope"] == "tenant"
    _tenant_check(monkeypatch, tb.REASON_UNREADABLE)
    with tenant.using(AGENCY, source=tenant.SOURCE_ASSERTION):
        degraded = await gw.budget_status(object(), "tp_compose")
    assert degraded["exhausted"] is False
    assert any(kw.get("what") == "tenant_budget_unreadable_degraded" for _, kw in lines)
    env("dev")
    with tenant.using(AGENCY, source=tenant.SOURCE_ASSERTION):
        closed = await gw.budget_status(object(), "tp_compose")
    assert closed["exhausted"] is True and closed["reason"] == tb.REASON_UNREADABLE


# ── G3 / L4: the chat's monthly app cap (monthly_spend.check) ────────────────────────────────────────────────────────

class MonthDb:
    def __init__(self, app=0.0, ten=0.0, error=None):
        self.app, self.ten, self.error = app, ten, error

    async def fetch(self, sql, stage, scopes):
        if self.error:
            raise self.error
        rows = [{"scope": monthly_spend.SCOPE_APP, "cost_usd": self.app, "calls": 1}]
        if len(scopes) > 1:
            rows.append({"scope": scopes[1], "cost_usd": self.ten, "calls": 1})
        return rows


@pytest.mark.asyncio
async def test_chat_app_cap_stops_on_dev_alerts_on_prod(env, lines):
    db = MonthDb(app=3.0)
    assert await monthly_spend.check(db, "tp_plan_chat", AGENCY, app_cap_usd=2, tenant_cap_usd=2) \
        == monthly_spend.REASON_APP_CAP
    env("prod")
    assert await monthly_spend.check(db, "tp_plan_chat", AGENCY, app_cap_usd=2, tenant_cap_usd=2) is None
    assert any(kw.get("threshold") == "cap_alert_only" for _, kw in lines)


@pytest.mark.asyncio
async def test_chat_tenant_cap_stays_hard_on_prod(env, lines):
    env("prod")
    db = MonthDb(app=0.1, ten=2.5)
    assert await monthly_spend.check(db, "tp_plan_chat", AGENCY, app_cap_usd=2, tenant_cap_usd=2) \
        == monthly_spend.REASON_TENANT_CAP


@pytest.mark.asyncio
async def test_chat_spend_read_error_degrades_on_prod_only(env, lines):
    db = MonthDb(error=PgError("42P01"))
    assert await monthly_spend.check(db, "tp_plan_chat", AGENCY, app_cap_usd=2, tenant_cap_usd=2) \
        == monthly_spend.REASON_UNREADABLE
    env("prod")
    assert await monthly_spend.check(db, "tp_plan_chat", AGENCY, app_cap_usd=2, tenant_cap_usd=2) is None


# ── L2 / L4: the AI sessions quota (tenant_sessions.admit) ───────────────────────────────────────────────────────────

class Outbox:
    """The tp-034 outbox events (ai.allowance_used, ai.spend_degraded) of one container, in memory: ON CONFLICT
    (idempotency_key) DO NOTHING RETURNING id = None for a key already there; ``error`` makes the next write raise."""

    def __init__(self, error=None):
        self.error = error
        self.rows: dict[str, tuple] = {}

    def write(self, sql, args):
        if self.error:
            raise self.error
        tenant_id, event, key, payload = args
        if key in self.rows:
            return None
        self.rows[key] = (tenant_id, event, json.loads(payload))
        return len(self.rows)

    def events(self, event):
        return [(k, *v) for k, v in self.rows.items() if v[1] == event]


class QuotaDb:
    """The quota view row, the tp-032 table and the tp-034 outbox of one tenant, in memory."""

    def __init__(self, quota=1, used=1, quota_error=None, notice_error=None):
        self.quota, self.used, self.quota_error = quota, used, quota_error
        self.outbox = Outbox(notice_error)

    async def fetchrow(self, sql, *args):
        if self.quota_error:
            raise self.quota_error
        return {"tenant_id": args[0], "ai_sessions_monthly": self.quota}

    async def fetchval(self, sql, *args):
        if sql == ts.COUNTED_SQL:
            return None
        if sql == ts.COUNT_SQL:
            return self.used
        if sql in (ts.NOTICE_SQL, spend_bell.SQL):
            return self.outbox.write(sql, args)
        raise AssertionError(sql)

    async def execute(self, sql, *args):
        return "OK"

    @asynccontextmanager
    async def transaction(self):
        yield


def test_the_notices_go_to_the_tripplanner_outbox_never_to_a_cis_table():
    for sql in (ts.NOTICE_SQL, spend_bell.SQL):
        assert sql.startswith("INSERT INTO tripplanner.outbox ") and "shared." not in sql
        assert "ON CONFLICT (idempotency_key) DO NOTHING RETURNING id" in sql


@pytest.mark.asyncio
async def test_a_used_up_quota_stops_and_tells_the_agency_once(env, lines):
    db = QuotaDb(quota=1, used=1)
    assert await ts.admit(db, AGENCY, SESSION) == ts.REASON_CAP
    assert await ts.admit(db, AGENCY, SESSION) == ts.REASON_CAP
    events = db.outbox.events("ai.allowance_used")
    assert len(events) == 1                                       # one attempt per tenant and month per container
    key, tenant_id, _, payload = events[0]
    assert tenant_id == AGENCY and key.startswith(f"ai.allowance_used:{AGENCY}:")
    assert set(payload) == {"kind", "month", "resets_at"}         # no personal data (tp-034 outbox_ai_notice_chk)
    env("prod")
    assert await ts.admit(QuotaDb(quota=1, used=1), AGENCY, SESSION) == ts.REASON_CAP   # the plan quota stays hard


@pytest.mark.asyncio
async def test_the_notice_already_written_this_month_is_not_retried(lines):
    db = QuotaDb()
    db.outbox.rows[f"ai.allowance_used:{AGENCY}:2026-11"] = (AGENCY, "ai.allowance_used", {})   # another container
    assert await ts.notify_agency(db, AGENCY, NOV) is False
    db.outbox.rows.clear()
    assert await ts.notify_agency(db, AGENCY, NOV) is False and db.outbox.rows == {}           # not asked again


@pytest.mark.asyncio
async def test_a_failing_notice_never_raises_and_retries_later(lines, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(ts, "_clock", lambda: clock[0])
    db = QuotaDb(notice_error=PgError("23514"))                  # tp-034 not applied yet: the event CHECK refuses
    assert await ts.notify_agency(db, AGENCY, NOV) is False
    db.outbox.error = None
    assert await ts.notify_agency(db, AGENCY, NOV) is False      # paced
    clock[0] += tb.FAILURE_TTL_SECONDS + 1
    assert await ts.notify_agency(db, AGENCY, NOV) is True and len(db.outbox.rows) == 1


def test_the_notice_names_the_month_and_the_reset_day():
    key, payload = ts.notice_payload(AGENCY, datetime(2026, 12, 9, tzinfo=timezone.utc))
    assert key == f"ai.allowance_used:{AGENCY}:2026-12"
    assert payload == {"kind": "tripplanner", "month": "2026-12", "resets_at": "2027-01-01"}


@pytest.mark.asyncio
async def test_an_unreadable_quota_fails_closed_on_dev_and_degrades_on_prod(env, lines):
    db = QuotaDb(quota_error=PgError("42501"))
    assert await ts.admit(db, AGENCY, SESSION) == ts.REASON_UNREADABLE
    ts.clear()
    env("prod")
    assert await ts.admit(db, AGENCY, SESSION) is None
    assert any(kw.get("what") == "tenant_sessions_degraded" for _, kw in lines)


# ── L4: the staff bell event of a degraded check (spend_bell, tp-034) ────────────────────────────────────────────────

class BellDb:
    """Records the bell events; ``error`` makes the next one raise."""

    def __init__(self, error=None):
        self.outbox = Outbox(error)

    async def fetchval(self, sql, *args):
        assert sql == spend_bell.SQL
        return self.outbox.write(sql, args)

    @asynccontextmanager
    async def transaction(self):
        yield


@pytest.mark.asyncio
async def test_a_degraded_spend_read_raises_one_admin_bell_a_day(monkeypatch, env, lines):
    env("prod")
    _spent(monkeypatch, error=PgError("57014"))
    _tenant_check(monkeypatch)
    db = BellDb()
    with tenant.using(AGENCY, source=tenant.SOURCE_ASSERTION):
        for _ in range(3):
            assert (await gw.budget_status(db, "tp_compose"))["exhausted"] is False
    events = db.outbox.events("ai.spend_degraded")
    assert len(events) == 1                                      # once per check and UTC day per container
    key, tenant_id, _, payload = events[0]
    assert tenant_id == AGENCY and key.startswith("ai.spend_degraded:spend_unreadable:")
    assert payload["check"] == "spend_unreadable" and payload["stage"] == "tp_compose"
    assert sum(kw.get("what") == "spend_unreadable_degraded" for _, kw in lines) == 3   # the error line every time


@pytest.mark.asyncio
async def test_dev_raises_no_bell(monkeypatch, env, lines):
    _spent(monkeypatch, error=PgError("57014"))
    _tenant_check(monkeypatch)
    db = BellDb()
    with tenant.using(AGENCY, source=tenant.SOURCE_ASSERTION):
        assert (await gw.budget_status(db, "tp_compose"))["exhausted"] is True
    assert db.outbox.rows == {}


@pytest.mark.asyncio
async def test_the_bell_already_raised_today_is_not_retried_and_a_failure_retries_later(monkeypatch, lines):
    clock = [10.0]
    monkeypatch.setattr(spend_bell, "_clock", lambda: clock[0])
    db = BellDb()
    db.outbox.rows["ai.spend_degraded:tenant_sessions:2026-11-17"] = (AGENCY, "ai.spend_degraded", {})
    assert await spend_bell.degraded(db, "tenant_sessions", tenant_id=AGENCY, now=NOV) is False   # raised already
    db.outbox.error = PgError("42501")
    assert await spend_bell.degraded(db, "monthly_spend_unreadable", tenant_id=AGENCY, now=NOV) is False
    db.outbox.error = None
    assert await spend_bell.degraded(db, "monthly_spend_unreadable", tenant_id=AGENCY, now=NOV) is False   # paced
    clock[0] += spend_bell.FAILURE_TTL_SECONDS + 1
    assert await spend_bell.degraded(db, "monthly_spend_unreadable", tenant_id=AGENCY, now=NOV) is True
    assert "ai.spend_degraded:monthly_spend_unreadable:2026-11-17" in db.outbox.rows
    assert await spend_bell.degraded(object(), "spend_unreadable", tenant_id=AGENCY, now=NOV) is False  # never raises
    assert await spend_bell.degraded(None, "spend_unreadable", tenant_id=AGENCY, now=NOV) is False


@pytest.mark.asyncio
async def test_no_bound_tenant_writes_no_event_but_keeps_the_error_line(lines):
    db = BellDb()
    assert await spend_bell.degraded(db, "spend_unreadable", now=NOV) is False
    assert db.outbox.rows == {} and any(kw.get("what") == "spend_unreadable_degraded" for _, kw in lines)


@pytest.mark.asyncio
async def test_the_quota_and_chat_degrades_raise_their_bells(env, lines):
    env("prod")
    db = QuotaDb(quota_error=PgError("42501"))
    assert await ts.admit(db, AGENCY, SESSION) is None
    month = MonthDb(error=PgError("42P01"))
    month.outbox = Outbox()

    async def fetchval(sql, *args):
        return month.outbox.write(sql, args)
    month.fetchval = fetchval
    month.transaction = None
    assert await monthly_spend.check(month, "tp_plan_chat", AGENCY, app_cap_usd=2, tenant_cap_usd=2) is None
    keys = [k for k, *_ in db.outbox.events("ai.spend_degraded") + month.outbox.events("ai.spend_degraded")]
    assert [k.split(":")[1] for k in keys] == ["tenant_sessions", "monthly_spend_unreadable"]


# ── the stage caps and the near-cap share are settings (CIS 447) ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_stage_caps_and_the_warning_share_come_from_the_view(monkeypatch, env, lines):
    rows = [{"key": settings.STAGE_CAP_SEARCH_USD, "value_text": "1", "version": 3, "tenant_id": None},
            {"key": settings.NEAR_CAP_PCT, "value_text": "60", "version": 2, "tenant_id": None}]

    async def read(db):
        return rows
    monkeypatch.setattr(settings, "read", read)
    settings.invalidate()
    await settings.refresh(object(), force=True)
    try:
        assert spend_rules._stage_cap("tp_search_embed") == 1.0
        assert spend_rules._stage_cap("tp_compose") is None          # 0 (the seed) = no stage cap
        assert spend_rules.near_ratio() == 0.6

        async def spent(db, stages):
            return 1.2 if stages == ["tp_search_embed"] else 1.5
        monkeypatch.setattr(gw, "_spent", spent)
        _tenant_check(monkeypatch)
        out = await gw.budget_status(object(), "tp_search_embed")
        assert out["exhausted"] is True and out["stage_cap_usd"] == 1.0
        env("prod")
        assert (await gw.budget_status(object(), "tp_search_embed"))["alert_only"] is True   # prod: alert only
    finally:
        settings.invalidate()


def test_the_cap_settings_fall_back_to_the_cis_seed():
    assert settings.get(settings.STAGE_CAP_COMPOSE_USD) == 0.0 and settings.get(settings.STAGE_CAP_SEARCH_USD) == 0.0
    assert settings.get(settings.NEAR_CAP_PCT) == 80 and spend_rules.near_ratio() == 0.8
