"""AA-1038 R5 slice 3 (offline): tp_lead_deliver keeps the 7-day hold whatever the delivery does.

The expiry runs first and commits on its own; the retry has a wall-clock budget, a breaker per tenant (one dead
endpoint cannot use up the run) and one per channel (no key / no sender stops the channel); an expired undelivered
lead asks for attention so the scheduled tick fails loudly. A fake connection plays the database. Invented names only.
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest

from backend.assembly import leads
from backend.jobs import handler, lead_deliver
from backend.jobs.queue import Job
from backend.shared import tenant

NOW = datetime(2026, 11, 7, 3, 0, tzinfo=timezone.utc)
T_SLOW = "5a0e0000-0000-4000-8000-00000000000a"
T_OK = "5a0e0000-0000-4000-8000-00000000000b"
T_MAIL = "5a0e0000-0000-4000-8000-00000000000c"


def _route(tid, channel="webhook"):
    return leads.Route(tid, f"slug-{tid[-1]}", f"Agency {tid[-1]}", channel,
                       webhook_url="https://hooks.example.invalid/leads" if channel == "webhook" else None,
                       email="desk@agency.example.invalid" if channel == "email" else None)


class FakeConn:
    """Records the order of statements; pending rows to retry and old rows to expire (an expired row is gone from
    the pending set, as in the database). ``locked`` ids are passed over by SKIP LOCKED but still counted."""

    def __init__(self, retry_rows=(), old_rows=(), migrated=True, rejected=(), locked=(), fail_picks=0):
        self.retry_rows = list(retry_rows)
        self.old_rows = list(old_rows)
        self.migrated = migrated
        self.rejected = list(rejected)
        self.locked = set(locked)
        self.fail_picks = fail_picks
        self.log: list[str] = []
        self.scope_log: list[str] = []
        self.committed: list[str] = []
        self.in_tx = False
        self.pick_limits: list[int] = []

    async def fetchval(self, sql, *args):
        if sql == lead_deliver.MIGRATED_SQL:
            assert self.scope_log[-1:] == ["scope"], "the outbox was read outside the system scope"
            return self.migrated
        if sql == lead_deliver.COUNT_EXPIRED_SQL:
            return len(self.old_rows)
        raise AssertionError(sql)

    async def fetch(self, sql, *args):
        if sql == lead_deliver.RETRY_SQL:
            self.log.append("retry_pick")
            return self.retry_rows
        if sql == lead_deliver.EXPIRE_PICK_SQL:
            assert self.in_tx
            if self.fail_picks:
                self.fail_picks -= 1
                raise ConnectionError("synthetic")
            self.log.append("expire_pick")
            self.pick_limits.append(args[1])
            return [r for r in self.old_rows if r["id"] not in self.locked][: args[1]]
        if sql == lead_deliver.REJECTED_SINCE_SQL:
            return self.rejected
        raise AssertionError(sql)

    async def execute(self, sql, *args):
        if sql in (tenant.SYSTEM_SCOPE_SQL, tenant.UNSCOPE_SQL):     # tp-028: the jobs Lambda's system scope
            self.scope_log.append("scope" if sql == tenant.SYSTEM_SCOPE_SQL else "unscope")
            return "SELECT 1" if sql == tenant.SYSTEM_SCOPE_SQL else "RESET"
        assert self.scope_log[-1:] == ["scope"], "a tenant table was written outside the system scope"
        assert sql == lead_deliver.EXPIRE_SQL
        payload = json.loads(args[1])
        assert payload["redacted"] is True and "traveller" not in payload
        self.log.append(f"expire:{args[0]}")
        self.old_rows = [r for r in self.old_rows if r["id"] != args[0]]
        return "UPDATE 1"

    def transaction(self):
        conn = self

        @asynccontextmanager
        async def tx():
            conn.in_tx = True
            try:
                yield
            finally:
                conn.in_tx = False
            conn.committed.append("expire")

        return tx()


class Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        conn = self.conn

        @asynccontextmanager
        async def acquire():
            yield conn

        return acquire()


def _row(i, tid, channel="webhook"):
    return {"id": i, "tenant_id": tid, "delivery_channel": channel,
            "payload": json.dumps({"lead_id": f"lead-{i}", "traveller": {"name": "Synthetic"}})}


def _old(i, channel):
    return {"id": i, "delivery_channel": channel, "payload": {"lead_id": f"old-{i}", "traveller": {"name": "Gone"}}}


def _job(**payload):
    return Job(id="j", kind=lead_deliver.KIND, payload=payload or {"trigger": "schedule"}, attempt=1, max_attempts=1)


@pytest.fixture
def routes(monkeypatch):
    table = {T_SLOW: _route(T_SLOW), T_OK: _route(T_OK), T_MAIL: _route(T_MAIL, "email")}

    async def load_route(conn, tid):
        return table[tid]

    monkeypatch.setattr(leads, "load_route", load_route)
    return table


@pytest.mark.asyncio
async def test_expiry_commits_before_any_delivery_and_a_timeout_cannot_hold_it_back(routes):
    """A slow endpoint that blows the budget still finds the expiry done and committed."""
    conn = FakeConn(retry_rows=[_row(i, T_SLOW) for i in range(1, 6)], old_rows=[_old(90, "crm"), _old(91, "webhook")])
    clock = {"t": 0.0}
    calls = []

    async def slow_deliver(c, row_id, route, payload):
        assert conn.committed and not conn.old_rows  # the hold already ended, committed, before the first POST
        calls.append(row_id)
        clock["t"] += 300.0  # one call eats the whole Lambda budget
        raise TimeoutError("synthetic")

    with pytest.raises(TimeoutError):
        await lead_deliver.run(Pool(conn), _job(), now=NOW, deliver=slow_deliver, clock=lambda: clock["t"])
    assert conn.log[:3] == ["expire_pick", "expire:90", "expire:91"] and conn.committed == ["expire", "expire"]


@pytest.mark.asyncio
async def test_retry_budget_defers_the_rest(routes):
    conn = FakeConn(retry_rows=[_row(i, T_OK) for i in range(1, 11)])
    clock = {"t": 0.0}

    async def deliver(c, row_id, route, payload):
        clock["t"] += 30.0
        return None

    out = await lead_deliver.run(Pool(conn), _job(), now=NOW, deliver=deliver, budget_s=100.0,
                                 clock=lambda: clock["t"])
    assert out["retry"]["delivered"] == 4 and out["retry"]["deferred"] == 6
    assert "attention" not in out


@pytest.mark.asyncio
async def test_one_dead_endpoint_is_tried_once_per_run(routes):
    rows = [_row(1, T_SLOW), _row(2, T_SLOW), _row(3, T_OK), _row(4, T_SLOW), _row(5, T_OK)]
    conn = FakeConn(retry_rows=rows)
    tried = []

    async def deliver(c, row_id, route, payload):
        tried.append(row_id)
        return "webhook_timeouterror" if route.tenant_id == T_SLOW else None

    out = await lead_deliver.run(Pool(conn), _job(), now=NOW, deliver=deliver)
    assert tried == [1, 3, 5]
    assert out["retry"] == {"tried": 3, "delivered": 2, "failed": 1, "route_changed": 0, "route_defect": 0,
                            "tenant_skipped": 2, "channel_skipped": 0, "deferred": 0}


@pytest.mark.asyncio
async def test_a_channel_this_deployment_cannot_use_stops_at_once(routes):
    rows = [_row(1, T_SLOW), _row(2, T_OK), _row(3, T_MAIL, "email"), _row(4, T_OK)]
    conn = FakeConn(retry_rows=rows)
    tried = []

    async def deliver(c, row_id, route, payload):
        tried.append(row_id)
        return "webhook_key_unavailable" if route.channel == "webhook" else None

    out = await lead_deliver.run(Pool(conn), _job(), now=NOW, deliver=deliver)
    assert tried == [1, 3]  # the e-mail channel still works
    assert out["retry"]["channel_skipped"] == 2 and out["retry"]["delivered"] == 1


@pytest.mark.asyncio
async def test_an_expired_undelivered_lead_asks_for_attention(routes):
    conn = FakeConn(old_rows=[_old(7, "crm"), _old(8, "crm"), _old(9, "email")])
    out = await lead_deliver.run(Pool(conn), _job(), now=NOW, deliver=None)
    assert out["expire"] == {"candidates": 3, "expired": 3, "by_channel": {"crm": 2, "email": 1}, "batches": 2,
                             "left": 0}
    assert out["attention"] == ["lead_expired_undelivered"]
    job = {"job_id": "j", "status": "succeeded", "result": out}
    assert handler.attention([job]) == ["j"]


@pytest.mark.asyncio
async def test_dry_run_counts_only(routes):
    conn = FakeConn(retry_rows=[_row(1, T_OK)], old_rows=[_old(7, "crm")])

    async def deliver(*a):
        raise AssertionError("a dry run delivers nothing")

    out = await lead_deliver.run(Pool(conn), _job(dry_run=True), now=NOW, deliver=deliver)
    assert out["mode"] == "dry_run" and out["expire"]["candidates"] == 1 and out["expire"]["expired"] == 0
    assert "attention" not in out and conn.log == []


@pytest.mark.asyncio
async def test_not_migrated_skips(routes):
    out = await lead_deliver.run(Pool(FakeConn(migrated=False)), _job(), now=NOW)
    assert out == {"mode": "skipped", "reason": "outbox_not_migrated"}


# --- review fixes (S272 lane 10): a true 7-day maximum, the whole backlog each run, retries, rejected leads ---------

def _ticks(start: datetime, days: int, late_minutes: int = 0):
    """The real schedule: EventBridge cron(30 19 * * ? *) in AA-CIS-Infra tripplanner_jobs.tf, optionally late."""
    first = start.replace(hour=19, minute=30, second=0, microsecond=0)
    return [first + timedelta(days=d, minutes=late_minutes) for d in range(days)]


@pytest.mark.parametrize("late_minutes", [0, 59])
@pytest.mark.parametrize("written_minute_after_tick", [1, 60, 600, 1439])
def test_a_lead_is_redacted_within_7_days_under_the_real_schedule(monkeypatch, late_minutes,
                                                                  written_minute_after_tick):
    """Review repro: written 1 minute after a tick, the old cutoff (now - 7 days) kept it 7 d 23 h 59 m."""
    monkeypatch.setattr(leads.config, "LEAD_HOLD_DAYS", 7)
    tick0 = datetime(2026, 11, 1, 19, 30, tzinfo=timezone.utc)
    written = tick0 + timedelta(minutes=written_minute_after_tick)
    for tick in _ticks(tick0, 12, late_minutes):
        if tick <= written:
            continue
        if written < leads.hold_cutoff(tick):  # the EXPIRE_PICK_SQL predicate: created_at < cutoff
            assert tick - written <= timedelta(days=7)
            break
    else:
        raise AssertionError("never expired")


def test_the_old_cutoff_was_the_bug():
    tick0 = datetime(2026, 11, 1, 19, 30, tzinfo=timezone.utc)
    written = tick0 + timedelta(minutes=5)
    old_cutoff = lambda now: now - timedelta(days=7)  # noqa: E731
    first = next(t for t in _ticks(tick0, 12) if t > written and written < old_cutoff(t))
    assert first - written > timedelta(days=7, hours=23)
    assert leads.EXPIRY_TICK_INTERVAL == timedelta(days=1)  # Infra cron(30 19 * * ? *): change both together


@pytest.mark.parametrize("raw,days", [("7", 7), ("30", 7), ("70", 7), ("5", 5), ("3", 3), ("2", 3), ("1", 3),
                                      ("0", 3), ("-3", 3), ("seven", 7), ("", 7), (None, 7)])
def test_the_hold_env_is_clamped_to_at_most_7_days(raw, days):
    from backend import config

    assert config.lead_hold_days(raw) == days
    assert config.LEAD_HOLD_DAYS <= config.LEAD_HOLD_DAYS_MAX == 7


def test_the_hold_floor_leaves_room_for_a_failed_tick(monkeypatch):
    """At the floor the cutoff still lies in the past: the expiry never redacts a lead the moment it is written."""
    monkeypatch.setattr(leads.config, "LEAD_HOLD_DAYS", leads.config.LEAD_HOLD_DAYS_MIN)
    assert leads.effective_hold() > timedelta(0)


class ScheduleConn(FakeConn):
    """One pending lead written at ``written``: picked and counted only when it is older than the run's cutoff, as
    the EXPIRE_PICK_SQL / COUNT_EXPIRED_SQL predicate ``created_at < cutoff`` does. ``down`` = the database is
    unreachable for every try of the run (an RDS reboot, failover or a stopped instance)."""

    def __init__(self, written):
        super().__init__(old_rows=[_old(1, "webhook")])
        self.written = written
        self.down = False
        self.expired_by: datetime | None = None
        self.run_at: datetime | None = None

    async def fetchval(self, sql, *args):
        if sql == lead_deliver.COUNT_EXPIRED_SQL:
            return len(self.old_rows) if self.written < args[0] else 0
        return await super().fetchval(sql, *args)

    async def fetch(self, sql, *args):
        if sql == lead_deliver.EXPIRE_PICK_SQL:
            if self.down:
                raise ConnectionError("synthetic: database unreachable")
            if not self.written < args[0]:
                self.pick_limits.append(args[1])
                return []
        return await super().fetch(sql, *args)

    async def execute(self, sql, *args):
        if sql == lead_deliver.EXPIRE_SQL:
            self.expired_by = self.run_at
        return await super().execute(sql, *args)


async def _replay(written, tick0, late_minutes, fail_first_expiring_tick=True):
    """The real schedule; the first tick whose cutoff passes the lead fails (the job is re-queued for the next tick,
    the scheduler has no other wake-up). Returns (expired at, failed tick)."""

    async def sleep(_s):
        return None

    conn, failed = ScheduleConn(written), None
    for tick in _ticks(tick0, 14, late_minutes):
        if tick <= written:
            continue
        conn.run_at = tick
        conn.down = fail_first_expiring_tick and failed is None and written < leads.hold_cutoff(tick)
        try:
            await lead_deliver.run(Pool(conn), _job(), now=tick, deliver=None, sleep=sleep)
        except ConnectionError:
            failed = tick
            continue
        if conn.expired_by is not None:
            return conn.expired_by, failed
    raise AssertionError("never expired")


@pytest.mark.asyncio
@pytest.mark.parametrize("late_minutes", [0, 59])
@pytest.mark.parametrize("written_minute_after_tick", [1, 60, 61, 600, 1439])
async def test_review_repro_the_hold_survives_a_failed_expiring_tick(monkeypatch, late_minutes,
                                                                    written_minute_after_tick):
    """Review repro: the tick that should end the hold fails, the job waits for the next daily tick, and the lead
    was redacted 7 d 22 h 59 m after it was written. The cutoff now absorbs that one failed tick."""
    monkeypatch.setattr(leads.config, "LEAD_HOLD_DAYS", 7)
    tick0 = datetime(2026, 11, 1, 19, 30, tzinfo=timezone.utc)
    written = tick0 + timedelta(minutes=written_minute_after_tick)
    expired_at, failed = await _replay(written, tick0, late_minutes)
    assert failed is not None and expired_at > failed
    assert expired_at - written <= timedelta(days=7)


@pytest.mark.asyncio
async def test_review_repro_the_reported_lead_is_redacted_inside_7_days(monkeypatch):
    """The exact case of the finding: written Mon 2026-11-02 20:31 UTC, the expiring tick's database unreachable."""
    monkeypatch.setattr(leads.config, "LEAD_HOLD_DAYS", 7)
    written = datetime(2026, 11, 2, 20, 31, tzinfo=timezone.utc)
    expired_at, failed = await _replay(written, datetime(2026, 11, 1, 19, 30, tzinfo=timezone.utc), 0)
    assert failed == datetime(2026, 11, 8, 19, 30, tzinfo=timezone.utc)
    assert expired_at == datetime(2026, 11, 9, 19, 30, tzinfo=timezone.utc)
    assert expired_at - written == timedelta(days=6, hours=22, minutes=59)


@pytest.mark.asyncio
async def test_without_the_absorbed_tick_a_failed_tick_broke_the_promise(monkeypatch):
    """The previous cutoff (one tick interval of headroom only) kept that lead 7 d 22 h 59 m."""
    monkeypatch.setattr(leads.config, "LEAD_HOLD_DAYS", 7)
    monkeypatch.setattr(leads, "EXPIRY_TICKS_ABSORBED", 0)
    written = datetime(2026, 11, 2, 20, 31, tzinfo=timezone.utc)
    expired_at, _ = await _replay(written, datetime(2026, 11, 1, 19, 30, tzinfo=timezone.utc), 0)
    assert expired_at - written == timedelta(days=7, hours=22, minutes=59)


@pytest.mark.asyncio
async def test_a_webhook_route_on_a_private_address_is_a_route_defect(routes):
    """Rows stored before AA staff pointed the URL at a private address: never tried again, the run asks for
    attention; a transient failure of another tenant keeps the old breaker."""
    rows = [_row(1, T_SLOW), _row(2, T_SLOW), _row(3, T_OK), _row(4, T_OK)]
    conn = FakeConn(retry_rows=rows)
    tried = []

    async def deliver(c, row_id, route, payload):
        tried.append(row_id)
        return leads.WEBHOOK_URL_NOT_PUBLIC if route.tenant_id == T_SLOW else "webhook_urlunresolvable"

    out = await lead_deliver.run(Pool(conn), _job(), now=NOW, deliver=deliver)
    assert tried == [1, 3]
    assert out["retry"]["route_defect"] == 2 and out["retry"]["tenant_skipped"] == 1
    assert out["attention"] == ["lead_route_defect"]
    assert handler.attention([{"job_id": "j", "status": "succeeded", "result": out}]) == ["j"]


@pytest.mark.asyncio
async def test_the_whole_backlog_drains_in_one_run(routes, monkeypatch):
    """Review repro: 1,000 undelivered leads a day against EXPIRE_MAX=500 built a backlog that never drained."""
    monkeypatch.setattr(lead_deliver, "EXPIRE_MAX", 500)
    conn = FakeConn(old_rows=[_old(i, "webhook") for i in range(1, 1235)])
    out = await lead_deliver.run(Pool(conn), _job(), now=NOW, deliver=None)
    assert out["expire"]["expired"] == 1234 and out["expire"]["left"] == 0 and conn.old_rows == []
    assert out["expire"]["batches"] == 4 and conn.pick_limits == [500, 500, 500, 500]
    assert out["attention"] == ["lead_expired_undelivered"]


@pytest.mark.asyncio
async def test_an_expiry_batch_that_raises_is_tried_again_at_once(routes):
    conn = FakeConn(old_rows=[_old(1, "crm")], fail_picks=2)
    slept = []

    async def sleep(s):
        slept.append(s)

    out = await lead_deliver.run(Pool(conn), _job(), now=NOW, deliver=None, sleep=sleep)
    assert out["expire"]["expired"] == 1 and out["expire"]["left"] == 0 and slept == [1.0, 2.0]


@pytest.mark.asyncio
async def test_an_expiry_that_keeps_failing_raises_so_the_job_is_retried(routes):
    conn = FakeConn(old_rows=[_old(1, "crm")], fail_picks=lead_deliver.EXPIRE_TRIES)

    async def sleep(_s):
        return None

    with pytest.raises(ConnectionError):
        await lead_deliver.run(Pool(conn), _job(), now=NOW, deliver=None, sleep=sleep)
    assert lead_deliver.MAX_ATTEMPTS > 1  # the runner re-queues it (the expiry is idempotent)
    assert handler.DAILY[lead_deliver.KIND][1] == lead_deliver.MAX_ATTEMPTS
    requeued = {"job_id": "j", "status": "queued", "error": "ConnectionError", "result": None}
    assert handler.attention([requeued]) == ["j"]  # reported at once, not only when the attempts run out


@pytest.mark.asyncio
async def test_rows_held_by_a_concurrent_ack_are_looked_at_again_then_reported(routes):
    conn = FakeConn(old_rows=[_old(1, "crm"), _old(2, "crm")], locked={2})
    slept = []

    async def sleep(s):
        slept.append(s)

    out = await lead_deliver.run(Pool(conn), _job(), now=NOW, deliver=None, sleep=sleep)
    assert out["expire"]["expired"] == 1 and out["expire"]["left"] == 1
    assert slept == [lead_deliver.LOCKED_WAIT_S] * lead_deliver.LOCKED_ROUNDS
    assert out["attention"] == ["lead_expired_undelivered", "lead_expiry_incomplete"]


@pytest.mark.asyncio
async def test_a_used_up_expiry_budget_asks_for_attention(routes, monkeypatch):
    monkeypatch.setattr(lead_deliver, "EXPIRE_MAX", 2)
    conn = FakeConn(old_rows=[_old(i, "email") for i in range(1, 8)])
    clock = {"t": 0.0}

    def tick():
        clock["t"] += 10.0
        return clock["t"]

    out = await lead_deliver.run(Pool(conn), _job(), now=NOW, deliver=None, clock=tick, expire_budget_s=25.0)
    assert 0 < out["expire"]["left"] < 7 and "lead_expiry_incomplete" in out["attention"]


@pytest.mark.asyncio
async def test_a_lead_the_crm_rejected_asks_for_attention(routes):
    """Review repro: a rejected ack redacted the lead with only an INFO line; the agency never saw it."""
    conn = FakeConn(rejected=[{"delivery_error": "rejected:invalid_lead", "n": 2},
                              {"delivery_error": "rejected", "n": 1}])
    out = await lead_deliver.run(Pool(conn), _job(), now=NOW, deliver=None)
    assert out["rejected"] == {"count": 3, "by_reason": {"invalid_lead": 2, "unspecified": 1}}
    assert out["attention"] == ["lead_rejected_by_crm"]
    assert handler.attention([{"job_id": "j", "status": "succeeded", "result": out}]) == ["j"]


@pytest.mark.asyncio
async def test_the_run_opens_the_system_scope_first_and_resets_it_last(routes):
    """tp-028 (FORCE row-level security): the jobs Lambda binds no tenant, so the run reads and writes the leads of
    every tenant under the system scope, and the scope is reset when the run ends, also when it raises."""
    conn = FakeConn(retry_rows=[_row(1, T_OK)], old_rows=[_old(7, "crm")])

    async def deliver(*a):
        return None

    await lead_deliver.run(Pool(conn), _job(), now=NOW, deliver=deliver)
    assert conn.scope_log == ["scope", "unscope"]

    failing = FakeConn(old_rows=[_old(7, "crm")], fail_picks=lead_deliver.EXPIRE_TRIES)

    async def sleep(_s):
        return None

    with pytest.raises(ConnectionError):
        await lead_deliver.run(Pool(failing), _job(), now=NOW, deliver=None, sleep=sleep)
    assert failing.scope_log == ["scope", "unscope"]
