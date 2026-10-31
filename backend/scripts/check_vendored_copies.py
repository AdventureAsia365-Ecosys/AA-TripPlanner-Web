"""Checks the code AA-TripPlanner-Web vendors from AA-CIS-App (AA-642 S8; modelled on AA-Partner
``api/scripts/check_vendored_copies.py``).

- ``backend/aa_llm/`` = AA-CIS-App ``shared/aa_llm/`` (the shared AI gateway core, root ADR 0009 decision 13), pinned
  to a CIS commit by ``backend/aa_llm.lock.json`` (written by AA-CIS-App ``.github/scripts/llm-core-sync.sh``).
  * integrity (offline, required: ``tests/test_vendored_copies.py``): every file matches the copy's ``MANIFEST.json``
    (``aa_llm/check_copy.py``), nothing added or missing, the lock names that manifest, the canonical repo and path,
    this destination and a CIS commit sha (``"ref": "local"`` only with AA_LLM_LOCK_REF_LOCAL_OK=1, the CIS consumers
    check of an unmerged core change);
  * provenance (online): the lock's commit is on AA-CIS-App ``main`` and every file equals the file at that commit.
    Pinned to the lock, never to the moving CIS main, so a CIS merge cannot turn TripPlanner red.
- ``backend/shared/identity_verifier.py`` = AA-CIS-App ``api/core/identity_verifier.py`` (root ADR 0006 §8 rule 9),
  compared with CIS ``main`` byte for byte (online only, like AA-Booking and AA-Partner).

Usage (from ``backend/``)::

    python3 scripts/check_vendored_copies.py            # integrity + provenance + identity_verifier (network)
    python3 scripts/check_vendored_copies.py --offline  # integrity only

Reads CIS through the GitHub API (the repos are public); ``GITHUB_TOKEN`` / ``GH_TOKEN`` only lifts the rate limit.
Standard library only. Exit 0 ok, 1 a problem (each printed as ``FAIL``).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

ORG = "AdventureAsia365-Ecosys"
REPO = "AA-CIS-App"
BACKEND = Path(__file__).resolve().parent.parent
CORE_DIR = BACKEND / "aa_llm"
LOCK = BACKEND / "aa_llm.lock.json"
CANONICAL = {"repo": f"{ORG}/{REPO}", "path": "shared/aa_llm"}
DEST = "backend/aa_llm"
LOCAL_OK_ENV = "AA_LLM_LOCK_REF_LOCAL_OK"
# path under backend/ -> path in AA-CIS-App, compared with CIS main
MAIN_COPIES = {"shared/identity_verifier.py": "api/core/identity_verifier.py"}
_SHA = re.compile(r"^[0-9a-f]{40}$")


class FetchError(RuntimeError):
    """The CIS file could not be read (rate limit, network, GitHub outage); not a 404."""


# (path, ref) -> the file's bytes at that ref, None on a 404; raises FetchError otherwise.
Fetcher = Callable[[str, str], "bytes | None"]


def _check_copy_module(folder: Path):
    """The copy's own check_copy.py, loaded from its file (the folder is a package of any name)."""
    spec = importlib.util.spec_from_file_location("_aa_llm_check_copy", folder / "check_copy.py")
    if spec is None or spec.loader is None:
        raise ImportError(f"{folder}/check_copy.py cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def integrity_problems(folder: Path = CORE_DIR, lock: Path = LOCK) -> list[str]:
    """Offline: the copy against its manifest, and the lock's shape."""
    if not (folder / "check_copy.py").is_file():
        return [f"{folder.name}/check_copy.py is missing: re-run the llm-core sync"]
    out = list(_check_copy_module(folder).problems(folder, lock if lock.is_file() else None))
    if not lock.is_file():
        return [*out, f"{lock.name} is missing: re-run the llm-core sync"]
    try:
        data = json.loads(lock.read_text(encoding="utf-8"))
    except ValueError as exc:
        return [*out, f"{lock.name} is not JSON: {exc}"]
    if data.get("canonical") != CANONICAL:
        out.append(f"{lock.name}: canonical is {data.get('canonical')!r}, want {CANONICAL!r}")
    if data.get("dest") != DEST:
        out.append(f"{lock.name}: dest is {data.get('dest')!r}, want {DEST!r}")
    ref = str(data.get("ref") or "")
    if not _SHA.match(ref) and not (ref == "local" and os.environ.get(LOCAL_OK_ENV) == "1"):
        out.append(f"{lock.name}: ref {ref!r} is not an AA-CIS-App commit sha (sync from CIS main, not --from-local)")
    return out


def _token() -> str:
    return (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()


def _get(url: str, accept: str, attempts: int = 3, sleep: Callable[[float], None] = time.sleep) -> bytes | None:
    token = _token()
    auths = ([token] if token else []) + [""]
    last = ""
    for attempt in range(attempts):
        for auth in auths:
            headers = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28",
                       "User-Agent": "aa-tripplanner-vendored-copy-check"}
            if auth:
                headers["Authorization"] = f"Bearer {auth}"
            try:
                req = urllib.request.Request(url, headers=headers)  # noqa: S310 — fixed https URL
                with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 — fixed https URL
                    return resp.read()
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    return None
                last = f"HTTP {exc.code}"
                if exc.code in (401, 403, 429):
                    continue
                if exc.code < 500:
                    raise FetchError(f"GitHub API {last} for {url}") from None
                break
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last = f"network error {exc}"
                break
        if attempt + 1 < attempts:
            sleep(2 * (attempt + 1))
    raise FetchError(f"could not read {url}: {last}")


def github_fetch(path: str, ref: str) -> bytes | None:
    return _get(f"https://api.github.com/repos/{ORG}/{REPO}/contents/{path}?ref={ref}",
                "application/vnd.github.raw")


def github_on_main(ref: str) -> bool:
    """True when ``ref`` is on AA-CIS-App main (main is ahead of it or the same commit)."""
    body = _get(f"https://api.github.com/repos/{ORG}/{REPO}/compare/{ref}...main", "application/vnd.github+json")
    if body is None:
        return False
    return json.loads(body).get("status") in ("ahead", "identical")


def provenance_problems(fetch: Fetcher = github_fetch, on_main: Callable[[str], bool] = github_on_main,
                        folder: Path = CORE_DIR, lock: Path = LOCK) -> list[str]:
    """Online: the lock's commit is on CIS main and each file equals the file at that commit."""
    try:
        ref = json.loads(lock.read_text(encoding="utf-8")).get("ref", "")
    except (OSError, ValueError) as exc:
        return [f"{lock.name} cannot be read: {exc}"]
    if not _SHA.match(str(ref)):
        return [f"{lock.name}: ref {ref!r} is not a commit sha; provenance cannot be checked"]
    out = []
    try:
        if not on_main(ref):
            out.append(f"{lock.name}: {ref[:12]} is not on {REPO} main (sync from a merged CIS commit)")
        for path in sorted(p for p in folder.iterdir() if p.is_file() and not p.name.startswith(".")
                           and p.suffix != ".pyc"):
            upstream = fetch(f"{CANONICAL['path']}/{path.name}", ref)
            if upstream is None:
                out.append(f"aa_llm/{path.name}: not in {REPO}@{ref[:12]}:{CANONICAL['path']}")
            elif upstream != path.read_bytes():
                out.append(f"aa_llm/{path.name}: differs from {REPO}@{ref[:12]} (never edit the copy; re-sync)")
    except FetchError as exc:
        out.append(f"cannot compare with {REPO}: {exc}")
    return out


def main_copy_problems(fetch: Fetcher = github_fetch, base: Path = BACKEND) -> list[str]:
    out = []
    for name, source in MAIN_COPIES.items():
        local = base / name
        if not local.is_file():
            out.append(f"{name}: missing; copy {REPO}@main:{source} here byte for byte")
            continue
        try:
            upstream = fetch(source, "main")
        except FetchError as exc:
            out.append(f"{name}: cannot compare with {REPO}@main:{source}: {exc}")
            continue
        if upstream is None:
            out.append(f"{name}: {source} not found on {REPO}@main")
        elif upstream != local.read_bytes():
            out.append(f"{name}: differs from {REPO}@main:{source}; copy the CIS file byte for byte")
    return out


def main(argv: list[str] | None = None, out: TextIO = sys.stdout) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--offline", action="store_true", help="integrity of the aa_llm copy only (no network)")
    args = p.parse_args(argv)
    problems = integrity_problems()
    if not args.offline:
        problems += provenance_problems() + main_copy_problems()
    for line in problems:
        print(f"FAIL  {line}", file=out)
    print(f"vendored copies: {len(problems)} problem(s){' (offline)' if args.offline else ''}", file=out)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
