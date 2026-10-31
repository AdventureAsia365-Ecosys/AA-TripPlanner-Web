"""AA-1015: per-session / per-IP limits on the AI routes (backend/shared/ratelimit.py). No DB, no network."""
from __future__ import annotations

import json

import pytest

from backend import config
from backend.browse import handler as browse
from backend.browse import search as search_mod
from backend.shared import llm_gateway, ratelimit

KEY = "edge-key-for-tests"


class CounterDb:
    """The tp-023 upsert in memory (one window per key); records the hashed keys it saw."""

    def __init__(self, fail: bool = False, rows=()):
        self.fail = fail
        self.counts: dict[str, int] = {}
        self.rules = list(rows)

    async def fetchrow(self, sql, *args):
        if "tripplanner.ai_rate_counter" in sql:
            if self.fail:
                raise RuntimeError('relation "tripplanner.ai_rate_counter" does not exist')
            self.counts[args[0]] = self.counts.get(args[0], 0) + 1
            return {"count": self.counts[args[0]], "retry_after_s": 37}
        return {"spent": 0.0}

    async def fetch(self, sql, *args):
        for needle, rows in self.rules:
            if needle in sql:
                return rows
        return []


@pytest.fixture
def edge_key(monkeypatch):
    monkeypatch.setattr(config, "TRIPPLANNER_API_KEY", KEY)
    return KEY


# --- rules -----------------------------------------------------------------------------------------------------

def test_rules_follow_the_capacity_plan_and_signed_in_doubles():
    assert ratelimit.rule_for("narrate", signed_in=False) == ratelimit.Rule("narrate", 3600, 10)
    assert ratelimit.rule_for("narrate", signed_in=True) == ratelimit.Rule("narrate", 3600, 20)
    assert ratelimit.rule_for("search", signed_in=False) == ratelimit.Rule("search", 60, 30)
    assert ratelimit.rule_for("search", signed_in=True) == ratelimit.Rule("search", 60, 60)
    with pytest.raises(ValueError):
        ratelimit.rule_for("compose", signed_in=False)


def test_rules_read_the_config(monkeypatch):
    monkeypatch.setattr(config, "TP_RATE_SEARCH_PER_MINUTE", 5)
    monkeypatch.setattr(config, "TP_RATE_SIGNED_IN_FACTOR", 3)
    assert ratelimit.rule_for("search", signed_in=True).limit == 15


# --- addresses and trust ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("203.0.113.7", "203.0.113.7"),
    (" 198.51.100.20 ", "198.51.100.20"),
    ("2001:db8:1234:5678:aaaa:bbbb:cccc:dddd", "2001:db8:1234:5678::/64"),
    ("2001:db8:1234:5678::1", "2001:db8:1234:5678::/64"),
    ("::ffff:203.0.113.9", "203.0.113.9"),
    ("not-an-ip", None), ("", None), (None, None), ("203.0.113.7, 10.0.0.1", None),
])
def test_normalise_ip(raw, expected):
    assert ratelimit.normalise_ip(raw) == expected


def test_client_headers_are_trusted_only_with_the_edge_key(edge_key):
    headers = {"x-tp-client-ip": "203.0.113.7", "x-tp-signed-in": "1"}
    assert ratelimit.client_from_headers(headers) == ratelimit.UNKNOWN_CLIENT                       # no key
    assert ratelimit.client_from_headers({**headers, "x-tripplanner-key": "wrong"}) == ratelimit.UNKNOWN_CLIENT
    trusted = ratelimit.client_from_headers({**headers, "x-tripplanner-key": KEY})
    assert trusted == ratelimit.Client(ip="203.0.113.7", signed_in=True)
    plain = ratelimit.client_from_headers({"x-tripplanner-key": KEY, "x-tp-client-ip": "bogus", "x-tp-signed-in": "yes"})
    assert plain == ratelimit.Client(ip=None, signed_in=False)


def test_without_the_edge_gate_the_client_headers_are_ignored(monkeypatch):
    """Local dev / unit tests: no key configured, so nothing proves the headers came from the BFF."""
    monkeypatch.setattr(config, "TRIPPLANNER_API_KEY", "")
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    assert ratelimit.client_from_headers({"x-tp-client-ip": "203.0.113.7"}) == ratelimit.UNKNOWN_CLIENT


def test_key_hash_hides_the_value_and_depends_on_scope_kind_and_secret(edge_key, monkeypatch):
    h = ratelimit.key_hash("search", "ip", "203.0.113.7")
    assert len(h) == 64 and "203" not in h
    assert h != ratelimit.key_hash("narrate", "ip", "203.0.113.7")
    assert h != ratelimit.key_hash("search", "session", "203.0.113.7")
    monkeypatch.setattr(config, "TRIPPLANNER_API_KEY", "another-edge-key")
    assert h != ratelimit.key_hash("search", "ip", "203.0.113.7")


# --- hit -------------------------------------------------------------------------------------------------------

async def test_the_31st_search_in_a_minute_is_refused():
    db = CounterDb()
    decisions = [await ratelimit.hit(db, "search", "ip", "203.0.113.7") for _ in range(31)]
    assert all(d.allowed for d in decisions[:30]) and not decisions[30].allowed
    assert decisions[30].retry_after_s == 37 and decisions[30].count == 31
    other = await ratelimit.hit(db, "search", "ip", "198.51.100.20")
    assert other.allowed and other.count == 1


async def test_a_signed_in_traveller_gets_twice_the_allowance():
    db = CounterDb()
    decisions = [await ratelimit.hit(db, "narrate", "session", "s-1", signed_in=True) for _ in range(21)]
    assert [d.allowed for d in decisions] == [True] * 20 + [False]


async def test_the_limiter_fails_open_with_an_error_line(capsys):
    decision = await ratelimit.hit(CounterDb(fail=True), "search", "ip", "203.0.113.7")
    assert decision.allowed and decision.reason == "counter_unavailable"
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["evt"] == "ai_rate_counter_error" and line["level"] == "error" and "203.0.113.7" not in json.dumps(line)


def test_limited_body_and_headers():
    d = ratelimit.Decision(False, "search", 30, 60, 31, 12)
    assert ratelimit.limited_body(d) == {"error": "rate_limited", "scope": "search", "limit": 30, "window_s": 60,
                                         "retry_after_s": 12}
    assert ratelimit.limited_headers(d, {"cache-control": "no-store"}) == {"cache-control": "no-store",
                                                                           "retry-after": "12"}


# --- /browse/search --------------------------------------------------------------------------------------------

@pytest.fixture
def keyword_only(monkeypatch):
    """Search answers by keyword (route unreadable), so no embed is needed; counts the searches that ran."""
    ran = []

    async def paused(db, stage):
        raise llm_gateway.AiPaused("route_unreadable")

    real = search_mod.search

    async def counting(q, filters, *, pool, **kw):
        ran.append(q)
        return await real(q, filters, pool=pool, **kw)

    monkeypatch.setattr(search_mod.llm_gateway, "load_route", paused)
    monkeypatch.setattr(search_mod, "search", counting)
    return ran


async def test_a_burst_from_one_ip_gets_429_and_another_ip_still_searches(keyword_only):
    db = CounterDb()
    client = ratelimit.Client(ip="203.0.113.7")
    statuses = [(await browse.route("GET", "/browse/search", {"q": "temple"}, pool=db, client=client))["statusCode"]
                for _ in range(31)]
    assert statuses == [200] * 30 + [429] and len(keyword_only) == 30
    limited = await browse.route("GET", "/browse/search", {"q": "temple"}, pool=db, client=client)
    assert limited["headers"]["retry-after"] == "37" and limited["headers"]["cache-control"] == "no-store"
    assert json.loads(limited["body"])["error"] == "rate_limited"
    other = await browse.route("GET", "/browse/search", {"q": "temple"}, pool=db,
                               client=ratelimit.Client(ip="198.51.100.20"))
    assert other["statusCode"] == 200


async def test_search_without_a_trusted_ip_or_without_a_word_is_not_counted(keyword_only):
    db = CounterDb()
    for _ in range(40):
        assert (await browse.route("GET", "/browse/search", {"q": "temple"}, pool=db))["statusCode"] == 200
    for _ in range(40):
        resp = await browse.route("GET", "/browse/search", {"q": "?!"}, pool=db,
                                  client=ratelimit.Client(ip="203.0.113.7"))
        assert resp["statusCode"] == 200
    assert db.counts == {}
