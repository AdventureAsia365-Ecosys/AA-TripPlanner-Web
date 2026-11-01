#!/usr/bin/env python3
"""AA-642 S263 E2 — the TripPlanner prompt registry gate (CI: backend/tests/test_prompt_registry_gate.py).

The manifest ``backend/shared/prompt_versions.json`` lists, per stage, its prompt versions (newest last) with the
source file, the symbols that hold the prompt and their hash (``prompt_registry.prompt_hash_of``: sha256 of the
symbols' source text). Same rules and commands as AA-CIS-App ``scripts/prompt_registry_check.py`` (AA-900):

  python3 backend/scripts/prompt_registry_check.py           # check; exit 1 when a prompt changed without a bump
  python3 backend/scripts/prompt_registry_check.py --bump    # append a new version for every stage whose prompt changed

The manifest fields (stage, version, prompt_sha256, source_path) are the columns of the CIS prompt register
(migration 321); AA-CIS-App registers the TripPlanner rows from this file. This repository never writes them.

Rules (each failure names the stage and what to do):
  * the newest entry of each stage matches its source today (else: --bump);
  * every file and symbol exists; (stage, version) and (stage, prompt_sha256) are unique; a stage's versions never
    repeat an older hash; stage / version / path match the shared.prompt_version CHECKs (CIS migration 321);
  * an entry is never edited: older entries keep the hash they were registered with (only the newest is compared).

No database, no network, no import of the prompt modules (ast only). Standard library + the repository. Not packaged
into the Lambda zip (package_lambdas.sh drops backend/scripts).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.shared.prompt_registry import (MANIFEST_PATH, PromptSourceError, load_manifest,  # noqa: E402
                                            prompt_hash_of)

STAGE_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,62}$")
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")
PATH_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]*$")


def problems(manifest: dict[str, list[dict]], root: Path = ROOT) -> list[str]:
    """Every rule broken by the manifest (empty = the gate passes). Pure apart from reading the source files."""
    out: list[str] = []
    for stage, entries in sorted(manifest.items()):
        if not STAGE_RE.match(stage):
            out.append(f"{stage}: stage key does not match {STAGE_RE.pattern}")
        versions, hashes = set(), set()
        for e in entries:
            v, sha, path = e.get("version", ""), e.get("prompt_sha256", ""), e.get("source_path", "")
            if not VERSION_RE.match(v):
                out.append(f"{stage} {v}: version does not match {VERSION_RE.pattern}")
            if not re.fullmatch(r"[0-9a-f]{64}", sha or ""):
                out.append(f"{stage} {v}: prompt_sha256 is not a sha256 hex")
            if len(path) > 300 or not PATH_RE.match(path) or ".." in path:
                out.append(f"{stage} {v}: source_path {path!r} is not a repository path")
            if v in versions:
                out.append(f"{stage} {v}: version listed twice")
            if sha in hashes:
                out.append(f"{stage} {v}: same prompt_sha256 as an older version (a version is a different prompt)")
            versions.add(v)
            hashes.add(sha)
        latest = entries[-1]
        try:
            current = prompt_hash_of(latest["source_path"], latest["symbols"], root)
        except PromptSourceError as exc:
            out.append(f"{stage} {latest['version']}: {exc} (update the manifest entry's source with a new version)")
            continue
        if current != latest["prompt_sha256"]:
            out.append(f"{stage}: the prompt in {latest['source_path']} ({', '.join(latest['symbols'])}) changed "
                       f"since version {latest['version']}. Bump it: python3 backend/scripts/prompt_registry_check.py "
                       "--bump (then AA-CIS-App registers the new row in its prompt register)")
    return out


def next_version(version: str) -> str:
    m = re.fullmatch(r"(.*?)(\d+)", version)
    return f"{m.group(1)}{int(m.group(2)) + 1}" if m else f"{version}.2"


def bump(raw: dict, root: Path = ROOT) -> list[str]:
    """Append a new version for every stage whose newest entry no longer matches its source. Returns the stages."""
    manifest: dict[str, list[dict]] = {}
    for e in raw["prompts"]:
        manifest.setdefault(e["stage"], []).append(e)
    bumped = []
    for stage, entries in manifest.items():
        latest = entries[-1]
        current = prompt_hash_of(latest["source_path"], latest["symbols"], root)
        if current != latest["prompt_sha256"]:
            raw["prompts"].append({**latest, "version": next_version(latest["version"]), "prompt_sha256": current})
            bumped.append(stage)
    return bumped


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    ap.add_argument("--bump", action="store_true", help="append a new version for each changed prompt")
    args = ap.parse_args(argv)
    if args.bump:
        raw = json.loads(args.manifest.read_text(encoding="utf-8"))
        bumped = bump(raw)
        args.manifest.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
        print(f"bumped: {', '.join(bumped) or 'nothing'}")
        return 0
    manifest = load_manifest(args.manifest)
    found = problems(manifest)
    for p in found:
        print(f"prompt-registry: {p}")
    print(f"prompt-registry: {sum(len(v) for v in manifest.values())} entries, {len(manifest)} stages, "
          f"{len(found)} problem(s)")
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
