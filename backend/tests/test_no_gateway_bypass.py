"""Guard: every model call goes through the gateway (`backend/shared/llm_gateway.py`), so it reads the route and the
spend decision, fails closed and writes its `shared.llm_call_log` row (AA-642 L6, root ADR 0009; same rule as
AA-CIS-App `tests/unit/test_aa685_no_raw_model_calls.py`).

Python (every backend module except tests, plus `tools/`) is parsed with `ast`, so comments and docstrings that name
these methods do not count. What counts as a bypass:
- a Bedrock runtime or batch method: invoke_model(_with_response_stream), converse(_stream), apply_guardrail,
  create_model_invocation_job;
- a vendor SDK call: <x>.completions / .messages / .responses / .embeddings .create/.stream/.parse;
- importing the vendor SDK (`openai`, `anthropic`) or the gateway's AWS helper `bedrock_satellite`;
- a vendor endpoint in a string: api.anthropic.com, api.openai.com, bedrock-runtime (an httpx / requests / urllib URL,
  or a boto3 "bedrock-runtime" client built outside the gateway).
The frontend never calls a model: its sources must not import a vendor AI SDK or name a vendor endpoint.

A real bypass found later goes into ALLOWED with "TODO: ..." and an issue, then gets routed through the gateway.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PY_ROOTS = ("backend", "tools")
SKIP_DIRS = {"tests", "node_modules", ".venv", "__pycache__"}
RAW_BEDROCK_METHODS = {
    "invoke_model", "invoke_model_with_response_stream", "converse", "converse_stream",
    "apply_guardrail", "create_model_invocation_job",
}
SDK_RESOURCES = {"completions", "messages", "responses", "embeddings"}
SDK_VERBS = {"create", "stream", "parse"}
VENDOR_MODULES = {"openai", "anthropic"}
GATEWAY_INTERNAL_MODULES = {"bedrock_satellite"}
VENDOR_HOSTS = ("api.anthropic.com", "api.openai.com", "bedrock-runtime")

# path -> reason: the gateway itself (the only modules that may call a model). Ratcheted: an entry with no hit fails.
ALLOWED: dict[str, str] = {
    "backend/shared/llm_gateway.py":
        "the gateway: Converse / InvokeModel / embeddings after route, budget and breaker; writes the ledger row",
    "backend/shared/bedrock_satellite.py":
        "the gateway's AWS helper: builds the bedrock-runtime client (acc2 direct or the acc3 role)",
}

FRONTEND = REPO / "frontend"
FRONTEND_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}
FRONTEND_SKIP = {"node_modules", ".next", "out", "coverage"}
FRONTEND_BYPASS = re.compile(
    r"""(?:from\s+|require\(\s*|import\(\s*)['"](?:openai|@anthropic-ai/[\w-]+|@aws-sdk/client-bedrock[\w-]*|@ai-sdk/[\w-]+)['"]"""
    r"|api\.anthropic\.com|api\.openai\.com|bedrock-runtime"
)


def _docstring_nodes(tree: ast.AST) -> set[int]:
    docs: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docs.add(id(first.value))
    return docs


def find_bypasses(source: str) -> list[tuple[int, str]]:
    """(line, what) for every gateway bypass in one Python module's source."""
    tree = ast.parse(source)
    docs = _docstring_nodes(tree)
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[0] in VENDOR_MODULES or parts[-1] in GATEWAY_INTERNAL_MODULES:
                    hits.append((node.lineno, f"import {alias.name}"))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.split(".")[0] in VENDOR_MODULES and node.level == 0:
                hits.append((node.lineno, f"import {module}"))
            elif module.split(".")[-1] in GATEWAY_INTERNAL_MODULES:
                hits.append((node.lineno, f"import {module}"))
            else:
                names = [a.name for a in node.names if a.name in GATEWAY_INTERNAL_MODULES]
                if names:
                    hits.append((node.lineno, "import " + ", ".join(names)))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            func = node.func
            if func.attr in RAW_BEDROCK_METHODS:
                hits.append((node.lineno, f".{func.attr}()"))
            elif func.attr in SDK_VERBS and isinstance(func.value, ast.Attribute) and func.value.attr in SDK_RESOURCES:
                hits.append((node.lineno, f".{func.value.attr}.{func.attr}()"))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            for host in VENDOR_HOSTS:
                if host in node.value:
                    hits.append((node.lineno, f"host {host}"))
    return hits


def _python_files():
    for root in PY_ROOTS:
        for path in sorted((REPO / root).rglob("*.py")):
            rel = path.relative_to(REPO)
            if not SKIP_DIRS.intersection(rel.parts):
                yield rel, path


def _scan() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for rel, path in _python_files():
        hits = find_bypasses(path.read_text(encoding="utf-8"))
        if hits:
            found[rel.as_posix()] = [f"{line} {what}" for line, what in hits]
    return found


def test_no_model_call_outside_the_gateway():
    bad = [f"{rel}:{hit}" for rel, hits in _scan().items() if rel not in ALLOWED for hit in hits]
    assert not bad, (
        "Model calls outside backend/shared/llm_gateway.py — route them through the gateway "
        "(generate_text / embed_texts and record()), or add a reviewed ALLOWED entry:\n" + "\n".join(bad)
    )


def test_allowlist_entries_still_match():
    found = _scan()
    stale = [rel for rel in ALLOWED if rel not in found]
    assert not stale, f"stale ALLOWED entries (file gone or no model call left, remove them): {stale}"


def test_frontend_never_calls_a_model():
    bad = []
    for path in sorted(FRONTEND.rglob("*")):
        rel = path.relative_to(REPO)
        if path.suffix not in FRONTEND_SUFFIXES or FRONTEND_SKIP.intersection(rel.parts) or not path.is_file():
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if FRONTEND_BYPASS.search(line):
                bad.append(f"{rel}:{n} {line.strip()[:120]}")
    assert not bad, "The frontend must reach models only through the backend gateway:\n" + "\n".join(bad)


# The detectors themselves, on invented snippets.
_BYPASSES = {
    "rt.converse(modelId=m, messages=[])": ".converse()",
    "rt.converse_stream(modelId=m)": ".converse_stream()",
    "rt.invoke_model(body=b, modelId=m)": ".invoke_model()",
    "rt.apply_guardrail(guardrailIdentifier=g, source='INPUT', content=[])": ".apply_guardrail()",
    "bedrock.create_model_invocation_job(jobName=j)": ".create_model_invocation_job()",
    "client.chat.completions.create(model=m, messages=[])": ".completions.create()",
    "client.messages.create(model=m, max_tokens=5, messages=[])": ".messages.create()",
    "client.responses.create(model=m, input='hi')": ".responses.create()",
    "client.embeddings.create(model=m, input=['hi'])": ".embeddings.create()",
    "import openai": "import openai",
    "from anthropic import Anthropic": "import anthropic",
    "from backend.shared import bedrock_satellite": "import bedrock_satellite",
    "from backend.shared.bedrock_satellite import bedrock_for_account": "import backend.shared.bedrock_satellite",
    "httpx.post('https://api.anthropic.com/v1/messages', json={})": "host api.anthropic.com",
    "requests.post(f'https://api.openai.com/v1/{path}', json={})": "host api.openai.com",
    "boto3.client('bedrock-runtime', region_name='us-west-1')": "host bedrock-runtime",
}


def test_detector_catches_each_bypass_pattern():
    for snippet, expected in _BYPASSES.items():
        assert [what for _, what in find_bypasses(snippet)] == [expected], snippet


def test_detector_ignores_docstrings_comments_and_unrelated_calls():
    source = '''
"""Calls api.openai.com through the gateway; never rt.converse() here."""
def f(db, inbox):
    """bedrock-runtime is only named in this docstring."""
    # client.messages.create(...) in a comment
    inbox.messages.send("x")
    db.create(table="t")
    return "converse"
'''
    assert find_bypasses(source) == []


def test_frontend_detector():
    assert FRONTEND_BYPASS.search('import OpenAI from "openai";')
    assert FRONTEND_BYPASS.search("import Anthropic from '@anthropic-ai/sdk';")
    assert FRONTEND_BYPASS.search('const { BedrockRuntimeClient } = require("@aws-sdk/client-bedrock-runtime");')
    assert FRONTEND_BYPASS.search('fetch("https://api.openai.com/v1/responses")')
    assert not FRONTEND_BYPASS.search('fetch("/api/trip/narrate")')
    assert not FRONTEND_BYPASS.search("These models run on Amazon Bedrock (Amazon Web Services).")
