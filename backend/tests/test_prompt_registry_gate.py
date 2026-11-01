"""AA-642 S263 E2: the prompt registry gate. CI fails when a TripPlanner prompt changes without a version bump
(`backend/scripts/prompt_registry_check.py`, same rules as AA-CIS-App AA-900). Offline: temp repositories with
invented prompt text, plus the real manifest against the real source."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from backend.assembly import agent
from backend.shared import prompt_registry

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "prompt_registry_check.py"
_spec = importlib.util.spec_from_file_location("prompt_registry_check", _SCRIPT)
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)


@pytest.fixture(autouse=True)
def _fresh_cache():
    prompt_registry.invalidate()
    yield
    prompt_registry.invalidate()


# --- the real manifest ----------------------------------------------------------------------------------------------

def test_the_repository_manifest_matches_the_deployed_prompts():
    """The CI gate: a changed prompt in backend/assembly/agent.py needs `prompt_registry_check.py --bump`."""
    assert gate.problems(prompt_registry.load_manifest()) == []


def test_every_text_stage_of_the_app_is_in_the_manifest():
    assert prompt_registry.newest(prompt_registry.load_manifest(), agent.STAGE) is not None
    assert prompt_registry.version_for(agent.STAGE) == "v1"


def test_the_manifest_covers_both_narration_prompts_and_the_user_prompt_builder():
    entry = prompt_registry.newest(prompt_registry.load_manifest(), agent.STAGE)
    assert entry["source_path"] == "backend/assembly/agent.py"
    assert entry["symbols"] == ["_COMPOSE_SYSTEM", "_RENARRATE_SYSTEM", "_build_body"]


def test_the_script_exits_zero_on_the_repository():
    assert gate.main([]) == 0


# --- the rules on a temp repository ---------------------------------------------------------------------------------

SOURCE = '''"""An invented prompt module."""
SYSTEM = "You narrate trips through Zedport and Qorvale."


def build(days):
    return "Days: " + ", ".join(days)


def helper():
    return 1
'''


def _repo(tmp_path: Path, source: str = SOURCE) -> Path:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "prompts.py").write_text(source, encoding="utf-8")
    return tmp_path


def _entry(root: Path, version="v1", symbols=("SYSTEM", "build")) -> dict:
    return {"stage": "zz_compose", "version": version, "source_path": "pkg/prompts.py", "symbols": list(symbols),
            "prompt_sha256": prompt_registry.prompt_hash_of("pkg/prompts.py", list(symbols), root)}


def test_a_matching_manifest_passes(tmp_path):
    root = _repo(tmp_path)
    assert gate.problems({"zz_compose": [_entry(root)]}, root) == []


def test_a_changed_prompt_without_a_bump_fails(tmp_path):
    root = _repo(tmp_path)
    manifest = {"zz_compose": [_entry(root)]}
    (root / "pkg" / "prompts.py").write_text(SOURCE.replace("Zedport", "Ostravel"), encoding="utf-8")
    (problem,) = gate.problems(manifest, root)
    assert "zz_compose: the prompt in pkg/prompts.py" in problem and "--bump" in problem


def test_a_change_outside_the_prompt_symbols_needs_no_bump(tmp_path):
    root = _repo(tmp_path)
    manifest = {"zz_compose": [_entry(root)]}
    (root / "pkg" / "prompts.py").write_text(SOURCE.replace("return 1", "return 2"), encoding="utf-8")
    assert gate.problems(manifest, root) == []


def test_bump_appends_the_next_version_and_the_gate_passes(tmp_path):
    root = _repo(tmp_path)
    raw = {"prompts": [_entry(root)]}
    (root / "pkg" / "prompts.py").write_text(SOURCE.replace("Zedport", "Ostravel"), encoding="utf-8")
    assert gate.bump(raw, root) == ["zz_compose"]
    assert [e["version"] for e in raw["prompts"]] == ["v1", "v2"]
    manifest = {"zz_compose": raw["prompts"]}
    assert gate.problems(manifest, root) == []
    assert gate.bump(raw, root) == []                                  # nothing changed since


def test_a_missing_symbol_or_file_is_reported(tmp_path):
    root = _repo(tmp_path)
    gone = {**_entry(root), "symbols": ["SYSTEM", "nowhere"]}
    assert "no module-level symbol nowhere" in gate.problems({"zz_compose": [gone]}, root)[0]
    moved = {**_entry(root), "source_path": "pkg/missing.py"}
    assert "no such file" in gate.problems({"zz_compose": [moved]}, root)[0]


def test_duplicate_versions_and_hashes_and_bad_keys_are_reported(tmp_path):
    root = _repo(tmp_path)
    first = _entry(root)
    found = gate.problems({"zz_compose": [first, {**first}]}, root)
    assert any("version listed twice" in p for p in found)
    assert any("same prompt_sha256" in p for p in found)
    bad = {**first, "version": "v 1", "source_path": "../escape.py", "prompt_sha256": "nothex"}
    found = gate.problems({"Bad-Stage": [bad]}, root)
    assert any("stage key" in p for p in found) and any("version does not match" in p for p in found)
    assert any("not a repository path" in p for p in found) and any("not a sha256 hex" in p for p in found)


def test_next_version():
    assert (gate.next_version("v1"), gate.next_version("v9"), gate.next_version("draft")) == ("v2", "v10",
                                                                                                "draft.2")


def test_the_hash_matches_the_cis_algorithm(tmp_path):
    """Same bytes as AA-CIS-App prompt_hash_of: the symbols' source text joined by a NUL line, sha256 hex."""
    root = _repo(tmp_path)
    system = 'SYSTEM = "You narrate trips through Zedport and Qorvale."'
    build = 'def build(days):\n    return "Days: " + ", ".join(days)'
    expected = hashlib.sha256((system + "\n\x00\n" + build).encode("utf-8")).hexdigest()
    assert prompt_registry.prompt_hash_of("pkg/prompts.py", ["SYSTEM", "build"], root) == expected


# --- version_for at run time ---------------------------------------------------------------------------------------

def test_version_for_is_none_on_drift_and_prints_a_note(tmp_path, monkeypatch, capsys):
    manifest = tmp_path / "prompt_versions.json"
    entry = {"stage": agent.STAGE, "version": "v1", "source_path": "backend/assembly/agent.py",
             "symbols": ["_COMPOSE_SYSTEM"], "prompt_sha256": "0" * 64}
    manifest.write_text(json.dumps({"prompts": [entry]}), encoding="utf-8")
    monkeypatch.setattr(prompt_registry, "MANIFEST_PATH", manifest)
    monkeypatch.setattr(prompt_registry.load_manifest, "__defaults__", (manifest,))
    assert prompt_registry.version_for(agent.STAGE) is None
    assert "differs from the manifest" in capsys.readouterr().out


def test_version_for_never_raises(monkeypatch, tmp_path, capsys):
    missing = tmp_path / "nope.json"
    monkeypatch.setattr(prompt_registry.load_manifest, "__defaults__", (missing,))
    assert prompt_registry.version_for(agent.STAGE) is None
    assert "prompt manifest unreadable" in capsys.readouterr().out
    assert prompt_registry.version_for(None) is None and prompt_registry.version_for("zz_unknown") is None


def test_system_prompt_hash():
    assert prompt_registry.system_prompt_hash("sys") == hashlib.sha256(b"sys").hexdigest()
    assert prompt_registry.system_prompt_hash(None) is None
