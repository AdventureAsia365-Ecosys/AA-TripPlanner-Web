"""Migration tests (X1 plan S7, AA-852). Run from the repo root: ``pytest migrations/tests``.

Kept outside ``backend/`` on purpose: a ``backend/**`` change redeploys both Lambdas on merge
(deploy-lambdas.yml), and a migration PR ships no Lambda code (the AA-827 precedent).

``--pg-dsn`` enables the replay test (test_tp013_pg.py) against a throwaway LOCAL Postgres (superuser
DSN, e.g. ``postgresql://postgres@127.0.0.1:55432/postgres``); without it that test is skipped, so CI
runs only the offline lint.
"""
from __future__ import annotations


def pytest_addoption(parser):
    parser.addoption("--pg-dsn", default=None,
                     help="superuser DSN of a throwaway local Postgres (tp-013 replay test)")
