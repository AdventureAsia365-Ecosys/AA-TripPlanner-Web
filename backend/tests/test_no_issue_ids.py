"""Guard: no string the Lambdas can send to the site names an internal issue ID (AA-994, rule R2).

Every backend module (tests excluded) is parsed and each string constant is checked, except docstrings: error
details and response bodies reach the customer pages through the frontend. Comments are not in the syntax tree, so
issue IDs stay allowed there (and in docstrings, tests and commit text). A hit: reword the string and keep the issue
reference in a comment above it. A string that only an operator ever sees goes into ``OPERATOR_ONLY`` with its reason.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ISSUE_ID = re.compile(r"\bAA-\d+\b")
# "file:text" of strings that never reach a page.
OPERATOR_ONLY = {
    # The 410 of a removed extraction op: answered only to a direct `lambda invoke` by an operator script.
    "extraction/lambda_entry.py:CIS place pipeline (ADR 0007, AA-712): place_extract / place_geocode / place_graph "
    "jobs; content checks on /admin/places",
    # A structured log note (CloudWatch), never part of a response.
    "shared/llm_gateway.py: is configured but TripPlanner does not run shadows (AA-805); only the primary route is "
    "called",
    # The Sentry test event of a direct operator invoke (unreachable over HTTP), matched by name in Sentry.
    "shared/observability.py:AA-871 Sentry test event (expected); contact test@example.com",
}


def _docstring_nodes(tree: ast.AST) -> set[int]:
    docs: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docs.add(id(first.value))
    return docs


def find_issue_ids(source: str, name: str) -> list[str]:
    tree = ast.parse(source, filename=name)
    docs = _docstring_nodes(tree)
    hits = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docs
        and ISSUE_ID.search(node.value)
        and f"{name}:{node.value}" not in OPERATOR_ONLY
    ]
    hits.sort(key=lambda n: (n.lineno, n.col_offset))
    return [f"{name}:{node.lineno}: {node.value[:120]}" for node in hits]


def test_scanner_skips_comments_and_docstrings() -> None:
    sample = (
        '"""Module AA-1."""\n# AA-2\ndef f():\n    """AA-3"""\n    raise ValueError("see AA-4")\nX = f"wave {1} AA-5"\n'
    )
    hits = [h.split(": ", 1)[1] for h in find_issue_ids(sample, "sample.py")]
    assert hits == ["see AA-4", " AA-5"]


def test_no_client_visible_string_names_an_issue_id() -> None:
    files = [p for p in sorted(BACKEND.rglob("*.py")) if "tests" not in p.relative_to(BACKEND).parts]
    assert len(files) > 20
    hits = [
        hit
        for p in files
        for hit in find_issue_ids(p.read_text(encoding="utf-8"), p.relative_to(BACKEND).as_posix())
    ]
    assert hits == [], "issue IDs in client-visible strings:\n" + "\n".join(hits)


def test_every_operator_only_exemption_still_exists() -> None:
    """A reworded string drops out of the exemption list instead of leaving a stale entry behind."""
    found = set()
    for p in sorted(BACKEND.rglob("*.py")):
        name = p.relative_to(BACKEND).as_posix()
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                found.add(f"{name}:{node.value}")
    assert OPERATOR_ONLY <= found, sorted(OPERATOR_ONLY - found)
