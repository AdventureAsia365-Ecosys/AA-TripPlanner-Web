"""AA-1111 (S294 ZAP triage): a Lambda error answer carries a code, never exception text.

The narration 502 used to return ``detail: str(e)``; now the class and the raising frame go to the log line only. The
AST guard keeps every 5xx answer of the HTTP handlers free of the bound exception (``except ... as e`` then
``_resp(5xx, {... e ...})``). Fixture text is invented.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from backend.assembly import events
from backend.tests.test_assembly import S1, T1, FakeConn, call

BACKEND = Path(__file__).resolve().parent.parent
HANDLERS = ["assembly/handler.py", "assembly/day_addons.py", "assembly/planner_admin.py", "browse/handler.py"]
SECRET = "quillmere-internal /var/task/backend/assembly/agent.py line 12"


@pytest.mark.asyncio
async def test_narration_defect_answers_the_code_only_and_logs_names(capsys):
    conn = FakeConn()
    await events.append_event(conn, T1, S1, "add_component", {"component_id": "c1"})

    def boom(itinerary):
        raise KeyError(SECRET)
        yield  # pragma: no cover — make it a generator

    resp = await call("POST", f"/trip/{T1}/narrate", {"session_id": S1}, conn=conn, narrator=boom)
    assert resp["statusCode"] == 502
    assert json.loads(resp["body"]) == {"error": "narration_failed"}
    assert "quillmere" not in resp["body"] and "/var/task" not in resp["body"]
    out = capsys.readouterr().out
    lines = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
    failed = [line for line in lines if line.get("evt") == "narration_failed"]
    assert len(failed) == 1 and failed[0]["error"] == "KeyError"
    assert failed[0]["at"].endswith(".py:" + failed[0]["at"].rsplit(":", 1)[1])
    assert "quillmere" not in out


def _status(node: ast.Call) -> int | None:
    first = node.args[0] if node.args else None
    return first.value if isinstance(first, ast.Constant) and isinstance(first.value, int) else None


def _leaks(tree: ast.AST) -> list[str]:
    found = []
    for handler in ast.walk(tree):
        if not isinstance(handler, ast.ExceptHandler) or not handler.name:
            continue
        for node in ast.walk(handler):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", "")) in
                    {"_resp", "resp"}):
                continue
            status = _status(node)
            if status is None or status < 500:
                continue
            names = {n.id for arg in node.args[1:] for n in ast.walk(arg) if isinstance(n, ast.Name)}
            if handler.name in names:
                found.append(f"line {node.lineno}: {status} answer uses `{handler.name}`")
    return found


@pytest.mark.parametrize("rel", HANDLERS)
def test_no_5xx_answer_carries_the_exception(rel):
    tree = ast.parse((BACKEND / rel).read_text())
    assert _leaks(tree) == [], f"{rel}: answer a code; log type(e).__name__ only"


def test_the_guard_finds_a_leak():
    src = "try:\n    pass\nexcept Exception as e:\n    return_value = _resp(502, {'error': 'x', 'detail': str(e)})\n"
    assert _leaks(ast.parse(src)) == ["line 4: 502 answer uses `e`"]
