"""AA-1143 (tech lead decision G3; CIS 455 / 456): TripPlanner's share of each LLM connection's monthly budget, read
from catalog.v_llm_connection_cap_v2 (its own rows, by column name), log only by default.

- the reader: one cached read, fail open (the view missing or not granted: nothing known, retried later);
- the rule: a reached share is logged once per connection and cache window; the connection is left out of the route
  only with the switch tripplanner.spend.connection_share_enforce on AND the dev spend rules; prod only logs;
- the gateway: deployment_plan leaves the connection out with a note when enforced, keeps it otherwise.
Fakes only (no DB, no AWS); invented connection, model and wire names."""
from __future__ import annotations

import pytest

from backend.shared import connection_share as cs
from backend.shared import observability, settings
from backend.tests.test_llm_gateway_deployments import _setup, dep, route, writer  # noqa: F401 — autouse setup
from backend.shared import llm_gateway as gw


def _row(key, *, stops, in_pool=False, share=10.0, spent=10.0, pool_spent=0.0):
    return {"connection_key": key, "share_usd": None if in_pool else share, "in_cis_pool": in_pool,
            "month_spent_usd": spent, "cis_pool_spent_usd": pool_spent, "hard_stop": True, "over_cap": stops,
            "stops": stops}


class PgError(Exception):
    def __init__(self, sqlstate):
        super().__init__("pg error")
        self.sqlstate = sqlstate


@pytest.fixture
def view(monkeypatch):
    state = {"rows": [], "error": None, "reads": 0}

    async def fetch(db):
        state["reads"] += 1
        if state["error"]:
            raise state["error"]
        return state["rows"]

    monkeypatch.setattr(cs, "_fetch", fetch)
    return state


@pytest.fixture
def events(monkeypatch):
    seen = []
    monkeypatch.setattr(observability, "emit", lambda name, **kw: seen.append((name, kw)))
    return seen


@pytest.fixture
def switch(monkeypatch):
    values = {settings.CONNECTION_SHARE_ENFORCE: False, settings.SPEND_ENVIRONMENT: "dev"}
    monkeypatch.setattr(settings, "get", lambda key, tenant_id=None: values[key])
    return values


async def test_the_select_names_its_columns_and_its_app():
    assert cs.SHARE_SQL == ("SELECT connection_key, share_usd, in_cis_pool, month_spent_usd, cis_pool_spent_usd, "
                            "hard_stop, over_cap, stops FROM catalog.v_llm_connection_cap_v2 WHERE app = 'tripplanner'")


async def test_one_read_per_cache_window(view):
    view["rows"] = [_row("zz-a", stops=False)]
    await cs.refresh(object(), now=100.0)
    await cs.refresh(object(), now=110.0)
    assert view["reads"] == 1 and cs.share_of("zz-a").share_usd == 10.0
    await cs.refresh(object(), now=100.0 + cs.CACHE_TTL_SECONDS)
    assert view["reads"] == 2


@pytest.mark.parametrize("state,quiet", [("42P01", True), ("42501", True), ("XX000", False)])
async def test_an_unreadable_view_fails_open(view, events, switch, state, quiet):
    view["error"] = PgError(state)
    await cs.refresh(object(), now=100.0)
    assert cs.share_of("zz-a") is None and cs.leave_out("tp_compose", "zz-a") is False
    assert (events == []) is quiet


async def test_log_only_by_default(view, events, switch):
    view["rows"] = [_row("zz-a", stops=True), _row("zz-b", stops=False)]
    await cs.refresh(object(), now=100.0)
    assert cs.leave_out("tp_compose", "zz-a") is False and cs.leave_out("tp_compose", "zz-a") is False
    assert cs.leave_out("tp_compose", "zz-b") is False
    reached = [kw for name, kw in events if name == "llm_connection_share_reached"]
    assert len(reached) == 1 and reached[0]["connection"] == "zz-a" and reached[0]["enforced"] is False


async def test_enforced_under_dev_only(view, events, switch):
    view["rows"] = [_row("zz-a", stops=True, in_pool=True, pool_spent=25.0)]
    await cs.refresh(object(), now=100.0)
    switch[settings.CONNECTION_SHARE_ENFORCE] = True
    assert cs.leave_out("tp_compose", "zz-a") is True
    switch[settings.SPEND_ENVIRONMENT] = "prod"
    assert cs.leave_out("tp_compose", "zz-a") is False


async def test_the_gateway_leaves_an_over_share_connection_out_only_when_enforced(view, switch):
    view["rows"] = [_row("zz-sat", stops=True)]
    await cs.refresh(object(), now=100.0)
    model = writer("zz-writer", dep("zz-sat", "wire.sat", 1), dep("zz-task", "wire.task", 2))
    keep = gw.deployment_plan(route(model, role="judge"), model)
    assert [d.connection_key for d in keep.items] == ["zz-sat", "zz-task"]
    switch[settings.CONNECTION_SHARE_ENFORCE] = True
    plan = gw.deployment_plan(route(model, role="judge"), model)
    assert [d.connection_key for d in plan.items] == ["zz-task"]
    assert "zz-writer@zz-sat: TripPlanner's monthly share is used up" in plan.notes
