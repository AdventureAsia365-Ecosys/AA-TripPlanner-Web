"""AA-642 S263 E2 — which version of a stage's prompt this code sends (TripPlanner's prompt register).

Mirrors AA-CIS-App ``shared/llm_client/prompt_registry.py`` (AA-900), so a TripPlanner entry can be registered in the
CIS table ``shared.prompt_version`` (migration 321) with the same hash:

- **The manifest** ``backend/shared/prompt_versions.json`` (in the repository and the Lambda zip): per stage, its prompt
  versions in order, each with the source file and the module-level symbols that hold the prompt, and
  ``prompt_sha256`` = ``prompt_hash_of``: sha256 over the exact source text of those symbols (read with ``ast``, never
  imported), so editing other code in the file does not change it and editing the prompt does. The CI gate
  ``tests/test_prompt_registry_gate.py`` (``backend/scripts/prompt_registry_check.py``) fails when the newest version
  of a stage no longer matches its source: a prompt change needs a version bump
  (``python3 backend/scripts/prompt_registry_check.py --bump``).
- **At run time** ``version_for(stage)`` = the version of the stage's newest manifest entry when its hash still
  matches the deployed source, else None (a drift prints one ``llm_gateway_note`` line). Unlike CIS it does not also
  require the row in ``shared.prompt_version``: the ``tripplanner`` role has no grant on that table, and the CIS rows
  for the ``tp_*`` stages come later (plan lane L1). ``system_prompt_hash`` is the ledger's ``prompt_hash``: sha256 of
  the system prompt sent (never the prompt itself), as the CIS gateway writes it.

Never raises on the call path.
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from typing import Optional

from backend.shared import observability

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = Path(__file__).resolve().with_name("prompt_versions.json")
_SEPARATOR = "\n\x00\n"

_current: dict[str, Optional[str]] = {}     # stage -> version of the deployed source, or None


class PromptSourceError(ValueError):
    """A manifest entry names a file or a symbol that does not exist."""


def _symbol_sources(tree: ast.Module, text: str) -> dict[str, str]:
    """The exact source text of each module-level assignment, annotated assignment, function and class, by name."""
    out: dict[str, str] = {}
    for node in tree.body:
        names: list[str] = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names = [node.name]
        elif isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id]
        if names:
            segment = ast.get_source_segment(text, node) or ""
            for name in names:
                out[name] = segment
    return out


def prompt_hash_of(source_path: str, symbols: list[str], root: Path = REPO_ROOT) -> str:
    """sha256 hex of the source text of ``symbols`` in ``source_path`` (repository-relative), in the given order,
    joined by a NUL line. Raises PromptSourceError for a missing file or symbol."""
    path = (root / source_path).resolve()
    if root.resolve() not in path.parents or not path.is_file():
        raise PromptSourceError(f"{source_path}: no such file in the repository")
    text = path.read_text(encoding="utf-8")
    sources = _symbol_sources(ast.parse(text, filename=source_path), text)
    missing = [s for s in symbols if s not in sources]
    if missing:
        raise PromptSourceError(f"{source_path}: no module-level symbol {', '.join(missing)}")
    return hashlib.sha256(_SEPARATOR.join(sources[s] for s in symbols).encode("utf-8")).hexdigest()


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, list[dict]]:
    """{stage: [entry, ...]} in version order (oldest first). An entry: version, source_path, symbols, prompt_sha256."""
    data = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, list[dict]] = {}
    for entry in data.get("prompts", []):
        out.setdefault(entry["stage"], []).append(entry)
    return out


def newest(manifest: dict[str, list[dict]], stage: str) -> Optional[dict]:
    entries = manifest.get(stage) or []
    return entries[-1] if entries else None


def version_for(stage: Optional[str]) -> Optional[str]:
    """The prompt version the deployed code sends for ``stage`` (module docstring), else None. Cached per container."""
    if not stage:
        return None
    if stage in _current:
        return _current[stage]
    found: Optional[str] = None
    try:
        entry = newest(load_manifest(), stage)
        if entry is not None and prompt_hash_of(entry["source_path"], entry["symbols"]) == entry["prompt_sha256"]:
            found = entry["version"]
        elif entry is not None:
            observability.emit("llm_gateway_note", stage=stage, version=entry["version"],
                               detail="the deployed prompt differs from the manifest: no prompt_version on the ledger")
    except Exception as e:  # noqa: BLE001 — never on the call path
        observability.emit("llm_gateway_note", stage=stage, error=type(e).__name__,
                           detail=f"prompt manifest unreadable: {str(e)[:200]}")
    _current[stage] = found
    return found


def system_prompt_hash(system: object) -> Optional[str]:
    """sha256 hex of the system prompt sent (ledger ``prompt_hash``), or None when it is not text."""
    if not isinstance(system, str):
        return None
    return hashlib.sha256(system.encode("utf-8")).hexdigest()


def invalidate() -> None:
    _current.clear()
