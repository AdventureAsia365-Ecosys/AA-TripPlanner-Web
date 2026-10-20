"""Migration tests (X1 plan S7, AA-852). Run from the repo root: ``pytest migrations/tests``.

Kept outside ``backend/`` on purpose: a ``backend/**`` change redeploys both Lambdas on merge
(deploy-lambdas.yml), and a migration PR ships no Lambda code (the AA-827 precedent).

Options (CI job "Migrations" passes all of them, see .github/workflows/ci.yml):
  * ``--pg-dsn``: superuser DSN of a throwaway LOCAL Postgres; enables the replay test
    (test_tp013_pg.py). CI: ``postgresql://postgres@127.0.0.1:5433/postgres`` (PostgreSQL 15 + pgvector
    from PGDG, the RDS major). Without it the replay test is skipped.
  * ``--pg-major N``: fail unless the server's major version is N (CI: 15, as RDS Dev), so the replay
    cannot silently run on another major.
  * ``--pg-require-vector``: fail unless the pgvector extension is available (CI), so tp-001 replays with
    the real ``VECTOR(1536)`` column and HNSW index instead of the ``real[]`` stand-in.
  * ``--fail-on-skip``: exit non-zero when any test or module was skipped, xfailed, xpassed or deselected (CI,
    AA-849): a skipped replay is a replay that did not run, so it must not read as green.
  * ``--infra-runner PATH``: AA-CIS-Infra ``db/run_migration.py`` (pinned commit in CI); enables
    test_infra_runner.py, which lints every file through ``--inspect`` as ``tripplanner/NNN_*.sql``.
"""
from __future__ import annotations

import pytest

NOT_RUN = ("skipped", "xfailed", "xpassed", "deselected")


def pytest_addoption(parser):
    parser.addoption("--pg-dsn", default=None,
                     help="superuser DSN of a throwaway local Postgres (tp-013 replay test)")
    parser.addoption("--pg-major", type=int, default=None,
                     help="required server major version for the replay test (CI: 15, as RDS Dev)")
    parser.addoption("--pg-require-vector", action="store_true", default=False,
                     help="fail the replay test when the pgvector extension is not available")
    parser.addoption("--infra-runner", default=None,
                     help="path of AA-CIS-Infra db/run_migration.py (DB Migrate lint, test_infra_runner.py)")
    parser.addoption("--fail-on-skip", action="store_true", default=False,
                     help="exit non-zero when any test or module was skipped, xfailed, xpassed or deselected (CI)")


def pytest_sessionfinish(session, exitstatus):
    """--fail-on-skip: a skip anywhere (a test, or a whole module at collection) turns the run red."""
    if not session.config.getoption("--fail-on-skip"):
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    counts = {k: len(reporter.stats.get(k, [])) for k in NOT_RUN} if reporter else {}
    not_run = {k: n for k, n in counts.items() if n}
    if not_run:
        reporter.write_line(f"--fail-on-skip: tests that did not run as tests: {not_run}", red=True, bold=True)
        if session.exitstatus == pytest.ExitCode.OK:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED
