"""AA-1038 R5 slice 1 — the tenant context of the assembly Lambda (backend/shared/tenant.py), offline.

The Postgres behaviour (tp-025 shadow policy as the runtime role) is in test_tenant_rls_pg.py. Here, with fake
connections: set_config is the first statement of a request and RESET the last, on success and when route() raises;
a tenant id that is not a uuid runs no SQL at all (and fails the cold start when it comes from the env); an unbind
that fails never hides the answer and discards the connection; the jobs Lambda logs its tenant-less system context.
tp-028 (FORCE slice): ``system_scope`` opens the jobs Lambda's cross-tenant path first and resets it last, also when the
body raises, and a reset that fails discards the connection.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from backend import config
from backend.assembly import handler
from backend.jobs import handler as jobs_handler
from backend.shared import tenant

DEMO = "00000000-0000-0000-0000-000000000002"
OTHER = "7e57c0de-0000-4000-8000-00000000f00d"
REPO = Path(__file__).resolve().parents[2]


class RecordingConn:
    """Records every statement; `fail_on` makes the statements starting with it raise."""

    def __init__(self, fail_on: str | None = None):
        self.statements: list[tuple[str, tuple]] = []
        self.fail_on = fail_on
        self.terminated = False

    async def execute(self, query, *args):
        self.statements.append((query, args))
        if self.fail_on and query.startswith(self.fail_on):
            raise ConnectionError("connection lost")
        return "OK"

    def terminate(self):
        self.terminated = True


def _acquirer(conn, calls: list):
    @asynccontextmanager
    async def acquire():
        calls.append("acquire")
        yield conn

    return acquire


def _lines(capsys) -> list[dict]:
    return [json.loads(ln) for ln in capsys.readouterr().out.splitlines() if ln.startswith("{")]


# --- config -----------------------------------------------------------------------------------------------------

def test_the_default_tenant_is_the_demo_constant():
    assert config.DEMO_TENANT_ID == DEMO
    assert config.DEFAULT_TENANT_ID == DEMO  # no TP_DEFAULT_TENANT_ID in the test env


@pytest.mark.parametrize("raw, want", [
    (DEMO, DEMO),
    (DEMO.upper(), DEMO),
    ("{" + OTHER + "}", OTHER),
    (f"  {OTHER}\n", OTHER),
    (OTHER.replace("-", ""), OTHER),
])
def test_tenant_uuid_returns_the_canonical_form(raw, want):
    assert config.tenant_uuid(raw, "TP_DEFAULT_TENANT_ID") == want


@pytest.mark.parametrize("raw", ["aa-tripplanner", "aa_internal", "1", "", None, DEMO + "0"])
def test_tenant_uuid_refuses_a_non_uuid_without_echoing_it(raw):
    with pytest.raises(RuntimeError) as e:
        config.tenant_uuid(raw, "TP_DEFAULT_TENANT_ID")
    assert str(e.value) == "TP_DEFAULT_TENANT_ID must be a uuid"


def _import_config(value: str | None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "TP_DEFAULT_TENANT_ID"}
    if value is not None:
        env["TP_DEFAULT_TENANT_ID"] = value
    env["PYTHONPATH"] = str(REPO)
    return subprocess.run([sys.executable, "-c", "from backend import config; print(config.DEFAULT_TENANT_ID)"],
                          cwd=REPO, env=env, capture_output=True, text=True, timeout=60, check=False)


def test_a_bad_env_override_fails_the_cold_start():
    out = _import_config("aa-tripplanner")
    assert out.returncode != 0
    assert "TP_DEFAULT_TENANT_ID must be a uuid" in out.stderr
    assert "aa-tripplanner" not in out.stderr


@pytest.mark.parametrize("value, want", [(OTHER, OTHER), (OTHER.upper(), OTHER), ("", DEMO), ("   ", DEMO)])
def test_a_uuid_override_is_used_and_blank_means_the_demo_tenant(value, want):
    out = _import_config(value)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == want


# --- tenant.bind / unbind / bound ----------------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["aa-tripplanner", "", None, 2, b"00000000000000000000000000000002", DEMO + "x"])
def test_canonical_refuses_a_non_uuid(bad):
    with pytest.raises(tenant.InvalidTenant) as e:
        tenant.canonical(bad)
    assert "aa-tripplanner" not in str(e.value)


def test_canonical_accepts_a_uuid_object():
    assert tenant.canonical(uuid.UUID(OTHER)) == OTHER


async def test_bind_sets_the_session_level_guc_and_logs_one_line(capsys):
    conn = RecordingConn()
    assert await tenant.bind(conn, DEMO.upper()) == DEMO
    assert conn.statements == [("SELECT set_config('app.tenant_id', $1, false)", (DEMO,))]
    assert _lines(capsys) == [{"evt": "tenant_context", "tenant_id": DEMO, "source": "default"}]


async def test_bind_runs_no_sql_for_a_non_uuid():
    conn = RecordingConn()
    with pytest.raises(tenant.InvalidTenant):
        await tenant.bind(conn, "aa-tripplanner")
    assert conn.statements == []


async def test_bound_unbinds_after_the_body_and_after_an_exception():
    conn = RecordingConn()
    async with tenant.bound(conn, DEMO) as tid:
        assert tid == DEMO
        await conn.execute("SELECT 1")
    assert [q for q, _ in conn.statements] == [tenant.BIND_SQL, "SELECT 1", "RESET app.tenant_id"]

    conn = RecordingConn()
    with pytest.raises(KeyError):
        async with tenant.bound(conn, DEMO):
            raise KeyError("boom")
    assert [q for q, _ in conn.statements] == [tenant.BIND_SQL, tenant.UNBIND_SQL]
    assert not conn.terminated


async def test_a_failed_unbind_keeps_the_body_error_and_discards_the_connection(capsys):
    conn = RecordingConn(fail_on="RESET")
    with pytest.raises(KeyError):
        async with tenant.bound(conn, DEMO):
            raise KeyError("boom")
    assert conn.terminated
    assert {"evt": "tenant_unbind_failed", "error": "ConnectionError"} in _lines(capsys)


# --- assembly serve() --------------------------------------------------------------------------------------------

@pytest.fixture
def fake_route(monkeypatch):
    seen: dict = {}

    async def route(method, path, body, *, conn, guest_token=None, caller=None, client=None):
        seen["statements_before"] = list(conn.statements)
        await conn.execute("SELECT 'route'")
        if seen.get("raise"):
            raise RuntimeError("route failed")
        return handler._resp(200, {"ok": True})

    monkeypatch.setattr(handler, "route", route)
    return seen


async def test_serve_binds_first_and_unbinds_last(fake_route, capsys):
    conn, calls = RecordingConn(), []
    out = await handler.serve("GET", f"/trip/{OTHER}", {}, acquire=_acquirer(conn, calls))
    assert out["statusCode"] == 200
    assert fake_route["statements_before"] == [(tenant.BIND_SQL, (DEMO,))]  # set_config is the first statement
    assert [q for q, _ in conn.statements] == [tenant.BIND_SQL, "SELECT 'route'", tenant.UNBIND_SQL]
    ctx = [ln for ln in _lines(capsys) if ln["evt"] == "tenant_context"]
    assert ctx == [{"evt": "tenant_context", "tenant_id": DEMO, "source": "default"}]  # no request values


async def test_serve_unbinds_when_route_raises(fake_route, capsys):
    fake_route["raise"] = True
    conn, calls = RecordingConn(), []
    out = await handler.serve("GET", f"/trip/{OTHER}", {}, acquire=_acquirer(conn, calls))
    assert out["statusCode"] == 500 and json.loads(out["body"]) == {"error": "internal"}
    assert [q for q, _ in conn.statements] == [tenant.BIND_SQL, "SELECT 'route'", tenant.UNBIND_SQL]
    errors = [ln for ln in _lines(capsys) if ln["evt"] == "assembly_error"]
    assert [e["error"] for e in errors] == ["RuntimeError"]  # the route's own error, not a cleanup one


async def test_serve_binds_the_configured_tenant(fake_route, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_TENANT_ID", OTHER)
    conn, calls = RecordingConn(), []
    await handler.serve("GET", f"/trip/{OTHER}", {}, acquire=_acquirer(conn, calls))
    assert conn.statements[0] == (tenant.BIND_SQL, (OTHER,))


@pytest.mark.parametrize("bad", ["aa-tripplanner", "", None])
async def test_serve_runs_no_sql_when_the_default_tenant_is_invalid(fake_route, monkeypatch, capsys, bad):
    monkeypatch.setattr(config, "DEFAULT_TENANT_ID", bad)
    conn, calls = RecordingConn(), []
    out = await handler.serve("GET", f"/trip/{OTHER}", {}, acquire=_acquirer(conn, calls))
    assert out["statusCode"] == 500 and json.loads(out["body"]) == {"error": "internal"}
    assert conn.statements == [] and calls == [] and "statements_before" not in fake_route
    lines = _lines(capsys)
    assert [ln["error"] for ln in lines if ln["evt"] == "assembly_error"] == ["InvalidTenant"]
    assert not any(ln["evt"] == "tenant_context" for ln in lines)


async def test_serve_answers_when_only_the_unbind_fails(fake_route, capsys):
    conn, calls = RecordingConn(fail_on="RESET"), []
    out = await handler.serve("GET", f"/trip/{OTHER}", {}, acquire=_acquirer(conn, calls))
    assert out["statusCode"] == 200
    assert conn.terminated  # never reused with a tenant possibly still bound
    assert any(ln["evt"] == "tenant_unbind_failed" for ln in _lines(capsys))


# --- jobs Lambda: tenant-less on purpose ------------------------------------------------------------------------

async def test_jobs_lambda_logs_the_system_context_and_binds_nothing(monkeypatch, capsys):
    conn = RecordingConn()

    class Pool:
        def acquire(self):
            return _acquirer(conn, [])()

    async def recent(c, kinds):
        return []

    monkeypatch.setattr(jobs_handler.queue, "recent", recent)
    out = await jobs_handler.serve({"op": "status"}, pool=Pool(), worker="w", time_left=lambda: 600.0)
    assert out == {"op": "status", "jobs": []}
    assert conn.statements == []  # no set_config: the unset-GUC branch of the tp-025 policy
    ctx = [ln for ln in _lines(capsys) if ln["evt"] == "tenant_context"]
    assert ctx == [{"evt": "tenant_context", "tenant_id": None, "source": "system", "op": "status"}]


async def test_jobs_lambda_refuses_an_unknown_op_before_logging_a_context(capsys):
    out = await jobs_handler.serve({"op": "nope"}, pool=None, worker="w", time_left=lambda: 600.0)
    assert "error" in out
    assert not any(ln["evt"] == "tenant_context" for ln in _lines(capsys))


# --- the system scope (tp-028, the jobs Lambda) -----------------------------------------------------------------------

async def test_system_scope_sets_then_resets_and_logs_no_tenant(capsys):
    conn = RecordingConn()
    async with tenant.system_scope(conn, op="tp_lead_deliver"):
        conn.statements.append(("BODY", ()))
    assert [q for q, _ in conn.statements] == [tenant.SYSTEM_SCOPE_SQL, "BODY", tenant.UNSCOPE_SQL]
    assert tenant.SYSTEM_SCOPE_SQL == "SELECT set_config('app.tenant_scope', 'system', false)"
    assert tenant.current() is None                       # no tenant is named for the run
    line = [x for x in _lines(capsys) if x.get("evt") == "tenant_context"][-1]
    assert line["tenant_id"] is None and line["source"] == "system" and line["scope"] == "system"
    assert line["op"] == "tp_lead_deliver"


async def test_system_scope_resets_when_the_body_raises():
    conn = RecordingConn()
    with pytest.raises(RuntimeError):
        async with tenant.system_scope(conn, op="x"):
            raise RuntimeError("synthetic")
    assert [q for q, _ in conn.statements] == [tenant.SYSTEM_SCOPE_SQL, tenant.UNSCOPE_SQL]
    assert conn.terminated is False


async def test_a_failed_scope_reset_discards_the_connection_and_keeps_the_body_error(capsys):
    conn = RecordingConn(fail_on="RESET app.tenant_scope")
    with pytest.raises(RuntimeError):
        async with tenant.system_scope(conn, op="x"):
            raise RuntimeError("synthetic")
    assert conn.terminated is True
    assert any(x.get("evt") == "tenant_scope_reset_failed" and x["error"] == "ConnectionError"
               for x in _lines(capsys))
