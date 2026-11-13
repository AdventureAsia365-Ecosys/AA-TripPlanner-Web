"""S279 thresholds lane L5 (AA-1096): TripPlanner's admin-editable settings (backend/shared/settings.py), read from the
CIS view catalog.v_app_settings_v1 (CIS 415) with a 25 s cache and the env value / code default as the fallback.

Parity of the effective values with the CIS seed (AA-CIS-App CIS 414 and its test tests/unit/test_aa1096_tp_settings_
seed.py pin the same table), the fallback while the view is absent, the per-tenant override, the refusal of a value that
breaks its bounds, the cache, the emergency env-first switch, the call sites and the jobs op ``settings``. The database
side (TripPlanner cannot read another app's rows) is tested in CIS (test_aa1096_migration_415.py); here the reader also
ignores any key it does not know. Invented values only; no network, no database."""

from __future__ import annotations

import ast
import json
import pathlib

import pytest

from backend import config
from backend.assembly import agent, leads, plan_chat
from backend.jobs import handler as jobs
from backend.shared import app_profile, ratelimit, settings

# The CIS seed (CIS 414): key -> (default, lo, hi, per tenant). Same numbers as AA-CIS-App
# tests/unit/test_aa1096_tp_settings_seed.py; a change on one side only fails one of the two suites.
SEED = {
    "tripplanner.rate.narrate_per_hour": (10, 1, 200, False),
    "tripplanner.rate.search_per_minute": (30, 1, 600, False),
    "tripplanner.rate.signed_in_factor": (2, 1, 10, False),
    "tripplanner.rate.send_per_hour": (5, 1, 100, False),
    "tripplanner.rate.send_ip_per_hour": (20, 1, 500, False),
    "tripplanner.rate.chat_per_hour": (20, 1, 200, False),
    "tripplanner.rate.chat_ip_per_hour": (40, 1, 1000, False),
    "tripplanner.spend.plan_chat_monthly_cap_usd": (2.0, 0, 200, False),
    "tripplanner.spend.plan_chat_tenant_monthly_cap_usd": (2.0, 0, 200, True),
    "tripplanner.leads.tenant_daily_cap": (200, 1, 5000, True),
    "tripplanner.leads.hold_days": (7, 3, 7, False),
    "tripplanner.ai.narration_cache_ttl_hours": (168, 1, 720, False),
    "tripplanner.ai.narration_max_tokens": (1200, 400, 4000, False),
    "tripplanner.ai.plan_chat_max_steps": (3, 1, 5, False),
    "tripplanner.ai.plan_chat_max_tokens": (700, 200, 2000, False),
    "tripplanner.handoff.advisor_demo": (True, None, None, False),
    "tripplanner.handoff.require_registration": (True, None, None, False),
}
TENANT = "7e1d0c4a-5b2f-4c3e-9a10-1096aa0a1096"
OTHER = "7e1d0c4a-5b2f-4c3e-9a10-1096aa0a2222"
BACKEND = pathlib.Path(__file__).resolve().parents[1]
_REAL_READ = settings.read          # captured at import, before conftest's autouse patch


class PgError(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__("pg error")
        self.sqlstate = sqlstate


def _row(key, value, version=1, tenant_id=None):
    return {"key": key, "value_text": json.dumps(value), "version": version, "tenant_id": tenant_id}


@pytest.fixture
def view(monkeypatch):
    """The view answers ``state["rows"]`` (or raises ``state["error"]``); counts the reads."""
    state = {"rows": [], "error": None, "reads": 0}

    async def _read(db):
        state["reads"] += 1
        if state["error"] is not None:
            raise state["error"]
        return state["rows"]

    monkeypatch.setattr(settings, "read", _read)
    settings.invalidate()
    return state


def _code_defaults(monkeypatch) -> dict:
    """config's values with every env var of a setting unset: the code defaults."""
    import importlib
    for s in settings.SETTINGS.values():
        if s.env:
            monkeypatch.delenv(s.env, raising=False)
    fresh = importlib.reload(config)
    try:
        return {k: getattr(fresh, s.attr) for k, s in settings.SETTINGS.items()}
    finally:
        importlib.reload(config)


# ── parity with the CIS seed ─────────────────────────────────────────────────────────────────────────────────────

def test_the_keys_are_the_cis_seed():
    assert sorted(settings.SETTINGS) == sorted(SEED)


@pytest.mark.parametrize("key", sorted(SEED))
def test_the_fallback_code_default_and_bounds_equal_the_seed(key, monkeypatch):
    default, lo, hi, per_tenant = SEED[key]
    s = settings.SETTINGS[key]
    got = _code_defaults(monkeypatch)[key]
    assert got == default and type(got) is type(default)
    assert (s.lo, s.hi, s.tenant_scoped) == (lo, hi, per_tenant)
    assert settings.problem(s, default) is None


def test_the_narration_body_keeps_its_hashed_literal_equal_to_the_default():
    # agent._build_body is part of the tp_compose prompt hash: its 1200 stays, _run puts the setting over it
    assert agent._build_body("sys", [])["max_tokens"] == config.NARRATION_MAX_TOKENS == SEED[
        "tripplanner.ai.narration_max_tokens"][0]


# ── the reader ───────────────────────────────────────────────────────────────────────────────────────────────────

async def test_view_absent_serves_the_fallback_and_retries_later(view, monkeypatch, capsys):
    view["error"] = PgError("42P01")
    assert await settings.refresh(object()) == "fallback"
    monkeypatch.setattr(config, "TP_RATE_NARRATE_PER_HOUR", 13)          # e.g. the env var of today
    assert settings.resolve(settings.RATE_NARRATE_PER_HOUR) == (13, "fallback", None)
    await settings.refresh(object())                                     # inside VIEW_RETRY_SECONDS: no read
    assert view["reads"] == 1 and "settings_note" in capsys.readouterr().out


async def test_the_platform_row_wins_over_the_env_value(view, monkeypatch):
    monkeypatch.setattr(config, "TP_RATE_CHAT_PER_HOUR", 99)
    view["rows"] = [_row(settings.RATE_CHAT_PER_HOUR, 12, version=3)]
    assert await settings.refresh(object()) == "view"
    assert settings.resolve(settings.RATE_CHAT_PER_HOUR) == (12, "platform", 3)


async def test_a_tenant_override_applies_to_that_tenant_only(view):
    view["rows"] = [_row(settings.LEAD_TENANT_DAILY_CAP, 200, version=0),
                    _row(settings.LEAD_TENANT_DAILY_CAP, 25, version=2, tenant_id=TENANT),
                    _row(settings.RATE_SEND_PER_HOUR, 7, version=1, tenant_id=TENANT)]   # platform key: no tenant row
    await settings.refresh(object())
    assert settings.resolve(settings.LEAD_TENANT_DAILY_CAP, TENANT) == (25, "tenant", 2)
    assert settings.resolve(settings.LEAD_TENANT_DAILY_CAP, OTHER) == (200, "platform", 0)
    assert settings.get(settings.LEAD_TENANT_DAILY_CAP) == 200
    assert settings.resolve(settings.RATE_SEND_PER_HOUR, TENANT)[1] == "fallback"


async def test_a_value_out_of_bounds_or_of_another_type_is_refused(view, capsys):
    view["rows"] = [_row(settings.LEAD_HOLD_DAYS, 30), _row(settings.ADVISOR_HANDOFF_DEMO, "no"),
                    _row(settings.RATE_SEARCH_PER_MINUTE, 2.5), _row(settings.PLAN_CHAT_MONTHLY_CAP_USD, 3)]
    await settings.refresh(object())
    assert settings.resolve(settings.LEAD_HOLD_DAYS)[1] == "fallback"
    assert settings.resolve(settings.ADVISOR_HANDOFF_DEMO)[1] == "fallback"
    assert settings.resolve(settings.RATE_SEARCH_PER_MINUTE)[1] == "fallback"
    assert settings.resolve(settings.PLAN_CHAT_MONTHLY_CAP_USD) == (3.0, "platform", 1)
    assert capsys.readouterr().out.count("settings_value_refused") == 3


async def test_another_apps_key_is_ignored(view):
    view["rows"] = [_row("cis.publish.daily_limit_default", 21), _row("tripplanner.zz.future_key", 1)]
    await settings.refresh(object())
    assert settings._state.snapshot == {}


async def test_cache_and_last_good_snapshot(view, capsys):
    view["rows"] = [_row(settings.RATE_NARRATE_PER_HOUR, 11)]
    await settings.refresh(object())
    await settings.refresh(object())
    assert view["reads"] == 1                                             # inside CACHE_TTL_SECONDS
    view["error"] = RuntimeError("connection lost")
    assert await settings.refresh(object(), force=True) == "last_good"
    assert settings.get(settings.RATE_NARRATE_PER_HOUR) == 11
    assert "settings_unreadable" in capsys.readouterr().out


async def test_the_read_runs_in_a_savepoint_on_a_connection():
    seen = []

    class Tx:
        async def __aenter__(self):
            seen.append("savepoint")

        async def __aexit__(self, *exc):
            seen.append("released")

    class Conn:
        def transaction(self):
            return Tx()

        async def fetch(self, sql):
            assert sql == settings.SETTINGS_SQL and "WHERE app = 'tripplanner'" in sql
            seen.append("fetch")
            return []

    assert await _REAL_READ(Conn()) == [] and seen == ["savepoint", "fetch", "released"]


async def test_env_first_switch_is_an_emergency_override(view, monkeypatch):
    view["rows"] = [_row(settings.RATE_CHAT_IP_PER_HOUR, 12)]
    await settings.refresh(object())
    monkeypatch.setenv("TP_RATE_CHAT_IP_PER_HOUR", "41")
    monkeypatch.setattr(config, "TP_RATE_CHAT_IP_PER_HOUR", 41)
    assert settings.get(settings.RATE_CHAT_IP_PER_HOUR) == 12            # switch off: the view wins
    monkeypatch.setenv(settings.ENV_FIRST_SWITCH, "on")
    assert settings.resolve(settings.RATE_CHAT_IP_PER_HOUR) == (41, "env", None)
    assert settings.get(settings.PLAN_CHAT_MAX_STEPS) == 3               # no env var: never "env"


# ── call sites ───────────────────────────────────────────────────────────────────────────────────────────────────

async def test_call_sites_read_the_settings(view):
    view["rows"] = [_row(settings.RATE_NARRATE_PER_HOUR, 4), _row(settings.RATE_SIGNED_IN_FACTOR, 3),
                    _row(settings.LEAD_HOLD_DAYS, 5), _row(settings.PLAN_CHAT_MAX_TOKENS, 900),
                    _row(settings.NARRATION_MAX_TOKENS, 1500)]
    await settings.refresh(object())
    assert ratelimit.rule_for(ratelimit.SCOPE_NARRATE, signed_in=False).limit == 4
    assert ratelimit.rule_for(ratelimit.SCOPE_NARRATE, signed_in=True).limit == 12
    assert leads.hold_days() == 5
    assert plan_chat.max_tokens() == 900
    with pytest.raises(ValueError):
        ratelimit.rule_for("zz", signed_in=False)
    bodies = []
    list(agent._run("sys", [{"day": 1, "name": "Zedp", "text_extract": "x"}],
                    lambda stage, body: bodies.append(body) or {"content": [{"type": "text", "text": "Day 1: ok"}]},
                    None, None))
    assert bodies[0]["max_tokens"] == 1500


def test_no_call_site_reads_the_old_config_values():
    old = {s.attr for s in settings.SETTINGS.values()}
    hits = []
    for path in BACKEND.rglob("*.py"):
        if "tests" in path.parts or path.name in ("config.py", "settings.py"):
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Attribute) and node.attr in old and getattr(node.value, "id", "") == "config":
                hits.append(f"{path.relative_to(BACKEND)}:{node.lineno} config.{node.attr}")
    assert hits == []


# ── jobs op "settings" (the live check) ──────────────────────────────────────────────────────────────────────────

async def test_jobs_settings_op_reports_the_sources_and_the_daily_cap(view, monkeypatch):
    view["rows"] = [_row(settings.LEAD_TENANT_DAILY_CAP, 150, version=2),
                    _row(settings.LEAD_TENANT_DAILY_CAP, 30, version=1, tenant_id=TENANT)]

    async def _profile(db):
        return app_profile.AppProfile(default_daily_budget_usd=5.0, source="view")

    monkeypatch.setattr(app_profile, "read", _profile)
    app_profile.invalidate()
    out = await jobs.serve({"op": "settings"}, pool=object(), worker="w", time_left=lambda: 60.0)
    assert out["op"] == "settings" and out["source"] == "view" and out["tenant_overrides"] == 1
    assert out["settings"][settings.LEAD_TENANT_DAILY_CAP] == {"value": 150, "source": "platform", "version": 2}
    assert out["settings"][settings.RATE_NARRATE_PER_HOUR]["source"] == "fallback"
    assert out["daily_cap"] == {"cap_usd": 5.0, "source": "app_profile", "profile_source": "view"}
    app_profile.invalidate()


async def test_jobs_settings_op_before_the_migrations(view):
    view["error"] = PgError("42501")
    out = await jobs.serve({"op": "settings"}, pool=object(), worker="w", time_left=lambda: 60.0)
    assert out["source"] == "fallback" and out["daily_cap"]["source"] == "env"
    assert out["daily_cap"]["cap_usd"] == float(config.TP_DAILY_SPEND_CAP_USD)
    assert "settings" in jobs.OPS
