"""AA-1041 slice 2 — the planning chat test set (tests/fixtures/plan_chat_cases.json), run through the whole assembly
route POST /trip/{id}/chat with a mocked model.

Each case scripts what the model says; the test asserts what the server does with it: the answer, the outcome, the
proposal (only experiences of the invented catalog, built from catalog facts), the pause at a money cap, the 429 at
the rate limit, the ledger rows and the monthly charge. The live smoke on Dev (about 0.2 USD) is the orchestrator's,
after the deploy; nothing here reaches AWS.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend import config
from backend.assembly import chat_tools, events, handler
from backend.shared import llm_gateway, monthly_spend, ratelimit, tenant
from backend.tests import plan_chat_fakes as fakes

CASES = json.loads((Path(__file__).parent / "fixtures" / "plan_chat_cases.json").read_text(encoding="utf-8"))["cases"]
TRIP = "00000000-0000-4000-8000-0000000000c1"
SESSION = "00000000-0000-4000-8000-0000000000c2"
TENANT = "00000000-0000-4000-8000-0000000000c3"
STAGE = "tp_plan_chat"


def test_the_set_covers_every_category_with_enough_cases():
    by_cat: dict[str, int] = {}
    for c in CASES:
        by_cat[c["category"]] = by_cat.get(c["category"], 0) + 1
    assert len(CASES) >= 20
    assert set(by_cat) == {"on_catalog", "off_catalog", "injection", "budget"}
    assert all(n >= 4 for n in by_cat.values()), by_cat
    assert len({c["id"] for c in CASES}) == len(CASES)


def _call(cost: float = 0.004, failed: bool = False) -> llm_gateway.Call:
    return llm_gateway.Call(STAGE, "writer", "sonnet-4-6", "acc3", 1200, 150, None if failed else cost,
                            fallback_used=False, failed=failed, status="error" if failed else None,
                            error_class="other" if failed else None)


@pytest.fixture
def world(monkeypatch):
    conn = fakes.CatalogConn()
    seen: dict = {"prompts": [], "records": [], "calls": 0, "billed": 0, "rate": True, "daily": {"exhausted": False},
                  "route_paused": None, "flip_after_first": None, "script": []}

    async def guard(*a, **k):
        return None

    async def current_itinerary(c, trip_id):
        return seen["trip"]

    async def budget_status(db, stage=None):
        return dict(seen["daily"])

    async def load_route(db, stage, *, borrow_from=None):
        assert stage == STAGE and borrow_from == "tp_compose"
        if seen["route_paused"]:
            raise llm_gateway.AiPaused(seen["route_paused"])
        return llm_gateway.builtin_route(STAGE)

    def generate_text(route, system, user, max_tokens, *, failures=None, **kw):
        assert route.stage == STAGE and max_tokens == 700
        seen["calls"] += 1
        seen["prompts"].append(user)
        if seen["calls"] == 1 and seen["flip_after_first"] is not None:
            conn.month[(STAGE, "app")] = (seen["flip_after_first"], 1)
        out = seen["script"].pop(0)
        if isinstance(out, dict) and "raise" in out:
            failures.append(_call(failed=True))
            raise llm_gateway.BedrockError(out["raise"])
        if isinstance(out, dict) and "raise_deadline" in out:      # the route deadline passed: a 0 USD row, no reply
            failures.append(_call(failed=True))
            raise llm_gateway.RouteDeadlineExceeded(out["raise_deadline"])
        seen["billed"] += 1
        return (out if isinstance(out, str) else json.dumps(out)), _call()

    async def record(db, call, signal, *, tenant_id=None):
        seen["records"].append((call, signal))

    async def hit(db, scope, kind, value, *, signed_in=False):
        rule = ratelimit.rule_for(scope, signed_in=signed_in)
        return ratelimit.Decision(seen["rate"], scope, rule.limit, rule.window_s, 1, 0 if seen["rate"] else 120)

    monkeypatch.setattr(handler, "_guard", guard)
    monkeypatch.setattr(events, "current_itinerary", current_itinerary)
    monkeypatch.setattr(llm_gateway, "budget_status", budget_status)
    monkeypatch.setattr(llm_gateway, "load_route", load_route)
    monkeypatch.setattr(llm_gateway, "generate_text", generate_text)
    monkeypatch.setattr(llm_gateway, "record", record)
    monkeypatch.setattr(ratelimit, "hit", hit)
    monkeypatch.setattr(chat_tools.routes_mod, "proposals", fakes.fake_proposals)
    monkeypatch.setattr(chat_tools.routes_mod, "tour_route", fakes.fake_tour_route)
    monkeypatch.setattr(config, "TP_PLAN_CHAT_MONTHLY_CAP_USD", 2.0)
    monkeypatch.setattr(config, "TP_PLAN_CHAT_TENANT_MONTHLY_CAP_USD", 2.0)
    monthly_spend.clear()
    yield conn, seen
    monthly_spend.clear()


def _arrange(case: dict, conn: fakes.CatalogConn, seen: dict, monkeypatch) -> None:
    state = case.get("state", {})
    seen["trip"] = fakes.itinerary(case.get("trip", []))
    seen["script"] = list(case.get("model", []))
    if "month_app_usd" in state:
        conn.month[(STAGE, "app")] = (state["month_app_usd"], 3)
    if "month_tenant_usd" in state:
        conn.month[(STAGE, f"tenant:{TENANT}")] = (state["month_tenant_usd"], 3)
    if "tenant_cap_usd" in state:
        monkeypatch.setattr(config, "TP_PLAN_CHAT_TENANT_MONTHLY_CAP_USD", state["tenant_cap_usd"])
    conn.month_unreadable = bool(state.get("month_unreadable"))
    seen["daily"] = state.get("daily", {"exhausted": False})
    seen["rate"] = not state.get("rate_limited")
    seen["route_paused"] = state.get("route_paused")
    seen["flip_after_first"] = state.get("month_app_usd_after_first_call")
    if state.get("tool_fails"):                 # a defect in a tool after a billed model call (a SQL error, a timeout)
        async def broken(*a, **k):
            raise RuntimeError(state["tool_fails"])
        monkeypatch.setattr(chat_tools.routes_mod, "proposals", broken)


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
async def test_case(case, world, monkeypatch):
    conn, seen = world
    _arrange(case, conn, seen, monkeypatch)
    month_before = conn.month.get((STAGE, "app"), (0.0, 0))
    body = {"session_id": SESSION, "message": case["message"], "history": case.get("history", [])}
    with tenant.using(TENANT, source=tenant.SOURCE_ASSERTION):
        resp = await handler.route("POST", f"/trip/{TRIP}/chat", body, conn=conn, guest_token="t",
                                   client=ratelimit.Client(ip="198.51.100.7"))
    exp = case["expect"]
    assert resp["statusCode"] == exp.get("http", 200), resp
    out = json.loads(resp["body"])
    assert seen["calls"] == exp.get("model_calls", seen["calls"])
    assert len(seen["records"]) == exp.get("ledger_rows", seen["calls"])        # one ledger row per model attempt
    if resp["statusCode"] == 429:
        assert out["error"] == "rate_limited" and out["scope"] == "chat"
        return
    if resp["statusCode"] == 502:
        # The turn failed after billed calls: no detail leaks, and every billed call is still charged to the month.
        assert out == {"error": "chat_failed"}
        assert conn.month[(STAGE, "app")][1] == month_before[1] + seen["billed"] > month_before[1]
        assert conn.month[(STAGE, f"tenant:{TENANT}")][1] == seen["billed"]
        assert all(s.get("failed_turn") for _, s in seen["records"])
        return
    assert out["status"] == exp["status"]
    if exp["status"] == "paused":
        assert out["reason"] == exp["reason"] and "reply" not in out
        if "charged_calls" in exp:
            assert conn.month[(STAGE, "app")][1] == 1 + exp["charged_calls"]   # the flip wrote 1 call
        return
    assert out["outcome"] == exp["outcome"], out
    assert out["ai_generated"] is exp["from_model"], out["reply"]
    reply = out["reply"]
    assert reply and len(reply) <= 700
    for s in exp.get("reply_contains", []):
        assert s in reply, reply
    for s in exp.get("reply_excludes", []):
        assert s not in reply, reply
    prop = out["proposal"]
    if exp.get("action") is None:
        assert prop is None
    else:
        assert prop["action"] == exp["action"]
        names = [s["name"] for s in prop["stops"]]
        assert names == exp["final"]
        assert prop["final_component_ids"] == [fakes.BY_NAME[n]["component_id"] for n in exp["final"]]
        assert set(prop["final_component_ids"]) <= set(fakes.BY_ID)                # catalog ids only
        if "added" in exp:
            assert [fakes.BY_ID[c]["name"] for c in prop["add_component_ids"]] == exp["added"]
        if "removed" in exp:
            assert [s["name"] for s in prop["removed"]] == exp["removed"]
    if "tools" in exp:
        assert seen["records"][-1][1]["tools"] == exp["tools"]
    if "dropped_refs" in exp:
        assert seen["records"][-1][1]["dropped_refs"] == exp["dropped_refs"]
    for key, want in exp.get("signal", {}).items():          # null = the key is absent from the signal
        assert seen["records"][-1][1].get(key) == want, (key, seen["records"][-1][1])
    prompts = "\n".join(seen["prompts"])
    if "prompt_contains" in exp:
        assert exp["prompt_contains"] in prompts
    if "prompt_excludes" in exp:
        assert exp["prompt_excludes"] not in prompts
    # Every billed call of the turn is charged to this month, for the app and the tenant.
    app = conn.month.get((STAGE, "app"), (0.0, 0))
    assert app[1] == month_before[1] + seen["billed"]
    assert conn.month[(STAGE, f"tenant:{TENANT}")][1] >= seen["billed"]
    # The ledger signal carries counts and codes, never the traveller's or the model's words.
    signal_text = json.dumps([s for _, s in seen["records"]])
    assert case["message"][:20] not in signal_text and reply[:20] not in signal_text
