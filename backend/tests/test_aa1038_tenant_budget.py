"""AA-1038 R5 slice 3 (offline): the per-tenant AI cap of TripPlanner's stages (catalog.v_tenant_ai_budget_v1, CIS 390).

Fail closed: no budget key, no row, the view missing or any read error stop AI for that tenant only; the bare path and
the AA demo tenant have no tenant budget (the app caps still apply); a reached budget is a cap (narration says
ai_budget_exhausted); the ledger row carries the request's tenant and its cost is charged to the cached spend.
"""
from __future__ import annotations

import pytest

from backend import config
from backend.shared import llm_gateway as gw
from backend.shared import tenant, tenant_budget as tb

TENANT_A = "3e4f5a6b-7c8d-4e9f-a0b1-c2d3e4f5a6b7"


@pytest.fixture(autouse=True)
def _clean():
    tb.clear()
    yield
    tb.clear()


class PgError(Exception):
    def __init__(self, sqlstate):
        super().__init__("synthetic")
        self.sqlstate = sqlstate


class BudgetDb:
    def __init__(self, row=None, error=None):
        self.row, self.error, self.reads = row, error, 0

    async def fetchrow(self, sql, *args):
        if sql == tb.BUDGET_SQL:
            self.reads += 1
            if self.error:
                raise self.error
            return self.row
        raise AssertionError(sql)


def _row(source="entitlement", monthly=40.0, spent=1.0, calls=3):
    return {"tenant_id": TENANT_A, "budget_source": source, "monthly_usd": monthly, "month_spent_usd": spent,
            "month_calls": calls}


@pytest.mark.parametrize("budget,reason", [
    (tb.TenantBudget(TENANT_A, "entitlement", 40.0, 39.99), None),
    (tb.TenantBudget(TENANT_A, "entitlement", 40.0, 40.0), tb.REASON_CAP),
    (tb.TenantBudget(TENANT_A, "entitlement", 0.0, 0.0), tb.REASON_CAP),
    (tb.TenantBudget(TENANT_A, "entitlement", None, 999.0), None),             # unlimited by contract
    (tb.TenantBudget(TENANT_A, "disabled", 0.0), tb.REASON_CAP),
    (tb.TenantBudget(TENANT_A, "none", None), tb.REASON_NONE),
    (tb.TenantBudget(TENANT_A, "missing", None), tb.REASON_NONE),
    (tb.TenantBudget(TENANT_A, "unreadable", None), tb.REASON_UNREADABLE),
])
def test_stop_reasons(budget, reason):
    assert budget.stop_reason() == reason


@pytest.mark.asyncio
@pytest.mark.parametrize("db,reason", [
    (BudgetDb(error=PgError("42P01")), tb.REASON_UNREADABLE),       # before CIS 390: closed, not open
    (BudgetDb(error=PgError("08006")), tb.REASON_UNREADABLE),
    (BudgetDb(row=None), tb.REASON_NONE),
    (BudgetDb(row=_row(source="none", monthly=None)), tb.REASON_NONE),
    (BudgetDb(row=_row(spent=40.0)), tb.REASON_CAP),
    (BudgetDb(row=_row()), None),
])
async def test_check_fails_closed(db, reason):
    assert await tb.check(db, TENANT_A) == reason


@pytest.mark.asyncio
async def test_the_demo_tenant_and_the_bare_path_have_no_tenant_budget():
    db = BudgetDb(error=PgError("42P01"))
    assert await tb.check(db, None) is None
    assert await tb.check(db, config.DEMO_TENANT_ID) is None
    assert await tb.check(db, config.DEFAULT_TENANT_ID) is None
    assert db.reads == 0


@pytest.mark.asyncio
async def test_reads_are_cached_and_charged():
    db = BudgetDb(row=_row(monthly=2.0, spent=1.5))
    assert await tb.check(db, TENANT_A) is None
    tb.charge(TENANT_A, 0.6)                                          # a call of this container
    assert await tb.check(db, TENANT_A) == tb.REASON_CAP and db.reads == 1
    tb.charge(config.DEMO_TENANT_ID, 5.0)                             # no-op
    tb.clear()
    assert await tb.check(db, TENANT_A) is None and db.reads == 2


class AppDb(BudgetDb):
    """The app-level spend view answers 0 (under every app cap); the tenant view as BudgetDb."""

    async def fetchrow(self, sql, *args):
        if sql == tb.BUDGET_SQL:
            return await super().fetchrow(sql, *args)
        return {"spent": 0.0}


@pytest.fixture
def app_profile_off(monkeypatch):
    from backend.shared import app_profile

    async def default(_db):
        return app_profile.DEFAULT

    monkeypatch.setattr(app_profile, "current", default)
    monkeypatch.setattr(gw, "_spend_unreadable_until", 0.0)


@pytest.mark.asyncio
async def test_budget_status_adds_the_tenant_cap(app_profile_off):
    with tenant.using(TENANT_A, source=tenant.SOURCE_ASSERTION):
        over = await gw.budget_status(AppDb(row=_row(spent=40.0)), "tp_compose")
    assert over["exhausted"] is True and over["reason"] == gw.REASON_CAP and over["scope"] == "tenant"
    tb.clear()
    with tenant.using(TENANT_A, source=tenant.SOURCE_ASSERTION):
        closed = await gw.budget_status(AppDb(error=PgError("42P01")), "tp_search_embed")
    assert closed["exhausted"] is True and closed["reason"] == tb.REASON_UNREADABLE
    tb.clear()
    with tenant.using(TENANT_A, source=tenant.SOURCE_ASSERTION):
        fine = await gw.budget_status(AppDb(row=_row()), "tp_compose")
    assert fine["exhausted"] is False and "scope" not in fine
    bare = await gw.budget_status(AppDb(error=PgError("42P01")), "tp_compose")   # no tenant: the app caps only
    assert bare["exhausted"] is False


@pytest.mark.asyncio
async def test_the_ledger_row_carries_the_request_tenant(monkeypatch):
    rows = []

    class LedgerDb:
        async def execute(self, sql, *args):
            rows.append(args)

    call = gw.Call(stage="tp_compose", role="writer", model="m", account="acc", tokens_in=1, tokens_out=1,
                   cost_usd=0.25, fallback_used=False)
    db = BudgetDb(row=_row(monthly=1.0, spent=0.5))
    with tenant.using(TENANT_A, source=tenant.SOURCE_ASSERTION):
        await tb.check(db, TENANT_A)
        await gw.record(LedgerDb(), call, {})
        assert gw.ledger_row(call, {}, tenant.current_id()).tenant_id == TENANT_A
    assert TENANT_A in [str(a) for a in rows[0]]
    assert tb._cache[TENANT_A][0].month_spent_usd == pytest.approx(0.75)  # noqa: SLF001
