"""Shared pytest options.

``--pg-dsn`` enables the Postgres integration tests (AA-712 vector search equivalence). They need a
throwaway LOCAL Postgres with pgvector; without the option they are skipped, so CI stays offline.
"""
from __future__ import annotations


def pytest_addoption(parser):
    parser.addoption("--pg-dsn", default=None,
                     help="DSN of a throwaway local Postgres with pgvector (integration tests)")
