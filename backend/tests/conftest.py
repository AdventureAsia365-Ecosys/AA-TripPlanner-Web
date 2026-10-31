"""Shared pytest options.

``--pg-dsn`` enables the Postgres integration tests (AA-712 vector search equivalence). They need a
throwaway LOCAL Postgres with pgvector; without the option they are skipped, so CI stays offline.
"""
from __future__ import annotations


def pytest_addoption(parser):
    parser.addoption("--pg-dsn", default=None,
                     help="DSN of a throwaway local Postgres with pgvector (integration tests)")


import pytest


@pytest.fixture(autouse=True)
def _fresh_search_embed_state():
    """AA-856: the query-embedding cache and the throttle cooldown are per container (module
    globals); every test starts with both empty."""
    from backend.browse import search as search_mod
    from backend.shared import llm_gateway

    llm_gateway.query_embed_cache.clear()
    search_mod._throttled_until = 0.0
    llm_gateway._spend_unreadable_until = 0.0  # AA-642 S0: the per-container "spend unreadable" hold
    llm_gateway._alerted.clear()                # AA-1015: the once-per-day spend alert lines
    yield
    llm_gateway.query_embed_cache.clear()
    search_mod._throttled_until = 0.0
    llm_gateway._spend_unreadable_until = 0.0
    llm_gateway._alerted.clear()
