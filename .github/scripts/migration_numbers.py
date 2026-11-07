#!/usr/bin/env python3
"""Migration numbers of a pull request (AA-1024 follow-up; the number part of the CIS / Booking / Partner migration
preflight, AA-993 M4b refusal 2, for TripPlanner's tp-NNN files).

Two lanes that each take "main max + 1" add the same tp-NNN; TripPlanner has no merge queue and its branch protection
does not require an up-to-date branch, so both PRs can be green and both merge, leaving two files with one number
(Dev registers one tp-NNN key, the other file never applies). This check fails a PR whose added file

  - has a number already used on main, or used twice in the PR head (MIG-3);
  - is not the next number: main max + 1, then + 2, ... for several files (a lower, reserved number or a gap is
    refused: renumber it on the latest main, lesson S259 / S265);
  - shares its number with a migration another OPEN pull request adds under another name, when that PR is older
    (a lower PR number): the newer PR renumbers. Against a newer PR it is a warning only (that PR goes red instead).

The open-PR part reads ``gh pr list --json number,files`` (a file passed with ``--open-prs``); when it cannot be
read the check says so and keeps the other two rules. A re-run after another PR merged re-checks the number on the
then-latest main (the checkout of a pull_request run is the PR merged into main at that time).

    python3 .github/scripts/migration_numbers.py --migrations-dir migrations --base "$BASE_SHA" \\
        [--pr 123 --open-prs open-prs.json]

Exit 1 on a refusal, 0 otherwise. Standard library only; tests: .github/scripts/test_migration_numbers.py.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

FILE_RE = re.compile(r"^(\d{3})_[^/]*\.sql$")


def _number(name: str) -> int | None:
    m = FILE_RE.match(name)
    return int(m.group(1)) if m else None


def number_problems(base_names: list[str], head_names: list[str], open_prs: list[dict] | None = None,
                    pr: int | None = None, migrations_dir: str = "migrations") -> tuple[list[str], list[str]]:
    """(errors, warnings) for the migration files the PR adds (on the head, not on the base)."""
    errors: list[str] = []
    warnings: list[str] = []
    base_numbers = {n for n in map(_number, base_names) if n is not None}
    seen: dict[int, str] = {}
    for name in sorted(head_names):
        n = _number(name)
        if n is None:
            continue
        if n in seen:
            errors.append(f"`{name}`: number {n:03d} is also used by `{seen[n]}` (MIG-3: one number, one file)")
        seen.setdefault(n, name)
    added = sorted(n for n in head_names if _number(n) is not None and n not in set(base_names))
    expected = max(base_numbers, default=0) + 1
    for name in added:
        n = _number(name)
        if n in base_numbers:
            errors.append(f"`{name}`: number {n:03d} is already taken on main. Renumber it to {expected:03d} "
                          "(main max + 1) on the latest main")
        elif n == expected:
            expected += 1
        else:
            errors.append(f"`{name}`: number {n:03d} is not the next one ({expected:03d} = main max + 1). Renumber it "
                          "on the latest main (a reserved or skipped number is never applied in order)")
    if open_prs is None:
        return errors, warnings
    prefix = migrations_dir.rstrip("/") + "/"
    mine = {_number(n): n for n in added}
    for other in open_prs:
        num = other.get("number")
        if num == pr:
            continue
        for f in other.get("files") or []:
            path = f.get("path", "")
            if not path.startswith(prefix) or "/" in path[len(prefix):]:
                continue
            name = path[len(prefix):]
            n = _number(name)
            if n is None or name in base_names or n not in mine or mine[n] == name:
                continue
            msg = f"`{mine[n]}`: open PR #{num} also adds tp-{n:03d} (`{name}`)"
            if pr is not None and num is not None and num < pr:
                errors.append(f"{msg} and is older: renumber this PR's file to the next free number")
            else:
                warnings.append(f"{msg}; it is newer, so it renumbers (it goes red when it re-runs)")
    return errors, warnings


def git(*args: str) -> str:
    out = subprocess.run(["git", *args], check=True, capture_output=True).stdout
    return out.decode("utf-8", errors="replace")


def main(argv: list | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--migrations-dir", required=True)
    p.add_argument("--base", required=True, help="base commit (the PR's base on main)")
    p.add_argument("--pr", type=int, default=None, help="this PR's number")
    p.add_argument("--open-prs", default="", help="JSON of `gh pr list --state open --json number,files`")
    a = p.parse_args(argv)
    mig = a.migrations_dir.rstrip("/")
    head_names = sorted(f.name for f in Path(mig).glob("*.sql"))
    try:
        base_names = [Path(x).name for x in git("ls-tree", "--name-only", f"{a.base}:{mig}").split()]
    except subprocess.CalledProcessError:
        base_names = []
    open_prs = None
    if a.open_prs:
        try:
            open_prs = json.loads(Path(a.open_prs).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"::notice::Migration numbers: open pull requests not readable ({e}): main max + 1 only")
    elif a.pr is not None:
        print("::notice::Migration numbers: no open-PR list: main max + 1 only")
    errors, warnings = number_problems(base_names, head_names, open_prs, a.pr, mig)
    for w in warnings:
        print(f"::warning::Migration numbers: {w}")
    for e in errors:
        print(f"::error::Migration numbers: {e}")
    if not errors:
        print("migration numbers: every added file takes the next free number")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
