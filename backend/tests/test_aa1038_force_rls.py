"""AA-1038 (tp-028, the FORCE slice) — static guards on who may touch the tenant tables and how, offline.

Since tp-028 the four tables tripplanner.sessions, trip_drafts, trip_events and outbox are FORCE row-level security
with a strict tenant policy: a connection without a tenant context sees and writes no row. Two ways in, and these
tests keep them the only ones:

* a request path binds a tenant: every connection the assembly Lambda (and its local server) acquires is bound in the
  same ``async with`` (``tenant.bound``), so no route runs SQL on a connection without a tenant;
* the jobs Lambda's system scope (``tenant.system_scope``, policy ``<table>_system``): only ``backend/jobs/`` opens it,
  and every job module that names a tenant table opens it. The GUC ``app.tenant_scope`` is spelled only in
  ``backend/shared/tenant.py``.

The browse Lambda reads catalog views only: it never names a tenant table. The database side is in
``migrations/tests/test_tp028_pg.py`` and ``tests/test_tenant_rls_pg.py``.
"""
from __future__ import annotations

import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
TENANT_TABLES = ("sessions", "trip_drafts", "trip_events", "outbox")
TABLE_RE = re.compile(r"\btripplanner\.(?:" + "|".join(TENANT_TABLES) + r")\b")
SQL_TABLE_RE = re.compile(r"\b(?:FROM|JOIN|INTO|UPDATE)\s+tripplanner\.(?:" + "|".join(TENANT_TABLES) + r")\b", re.I)
ACQUIRE_RE = re.compile(r"async with[^\n]*\bacquire\(\)\s+as\s+(\w+)(?P<rest>[^\n]*)")


def _sources(*dirs: str) -> dict[str, str]:
    out = {}
    for d in dirs:
        for p in sorted((BACKEND / d).rglob("*.py")):
            if "tests" not in p.parts:
                out[str(p.relative_to(REPO))] = p.read_text(encoding="utf-8")
    return out


def test_the_tenant_tables_are_the_ones_tp028_forces():
    sql = (REPO / "migrations" / "028_force_tenant_rls.sql").read_text(encoding="utf-8")
    forced = re.findall(r"ALTER TABLE tripplanner\.(\w+)\s+FORCE ROW LEVEL SECURITY;", sql)
    assert sorted(forced) == sorted(TENANT_TABLES)


def test_every_assembly_connection_is_bound_to_a_tenant_where_it_is_acquired():
    sites = []
    for name, src in _sources("assembly").items():
        for m in ACQUIRE_RE.finditer(src):
            sites.append(name)
            assert re.search(rf"\btenant\.bound\(\s*{m.group(1)}\b", m.group("rest")), \
                f"{name}: a connection acquired without tenant.bound in the same `async with`: {m.group(0).strip()}"
    assert sorted(set(sites)) == ["backend/assembly/handler.py", "backend/assembly/local_server.py"], sites


def test_the_browse_lambda_never_names_a_tenant_table():
    hits = [n for n, src in _sources("browse").items() if TABLE_RE.search(src)]
    assert hits == []


def test_only_the_jobs_lambda_opens_the_system_scope():
    openers = sorted(n for n, src in _sources("assembly", "browse", "shared", "jobs", "extraction", "scripts").items()
                     if "system_scope(" in src and not n.endswith("shared/tenant.py"))
    assert openers and all(n.startswith("backend/jobs/") for n in openers), openers
    spelled = sorted(n for n, src in _sources("assembly", "browse", "shared", "jobs", "extraction", "scripts").items()
                     if "app.tenant_scope" in src or "SYSTEM_SCOPE_SQL" in src)
    assert spelled == ["backend/shared/tenant.py"], spelled
    for top in ("tools", "frontend"):
        root = REPO / top
        if root.is_dir():
            for p in root.rglob("*"):
                if p.is_file() and p.suffix in (".py", ".ts", ".tsx", ".js", ".mjs", ".sql") \
                        and "node_modules" not in p.parts:
                    assert "app.tenant_scope" not in p.read_text(encoding="utf-8", errors="ignore"), p


def test_every_job_module_that_names_a_tenant_table_opens_the_system_scope():
    named = {n: src for n, src in _sources("jobs").items() if SQL_TABLE_RE.search(src)}
    # AA-1093: the tenant backup and its restore drill read the four tables of one tenant under the system scope; the
    # erase's verification census counts the tenant's rows left in them (tenant.erasure.v1), under the same scope.
    assert sorted(named) == ["backend/jobs/lead_deliver.py", "backend/jobs/outbox_redact.py",
                             "backend/jobs/tenant_backup.py", "backend/jobs/tenant_erase.py",
                             "backend/jobs/tenant_restore_drill.py"], sorted(named)
    for name, src in named.items():
        acquires = ACQUIRE_RE.findall(src)
        assert acquires, name
        for m in ACQUIRE_RE.finditer(src):
            assert re.search(rf"\btenant\.system_scope\(\s*{m.group(1)}\s*,\s*op=KIND\)", m.group("rest")), \
                f"{name}: a connection acquired without tenant.system_scope: {m.group(0).strip()}"
