#!/usr/bin/env python3
"""The backend coverage floor only goes up (AA-1119, S282 audit R31).

"Backend (pytest)" runs the unit tests with `--cov --cov-config=.coveragerc` from backend/, so pytest-cov fails below
`fail_under` in backend/.coveragerc. This script guards the floor itself:

  check [--base SHA]  The floor must be set (a number in [report] fail_under, 0 < floor <= 100) and, against the base
                      commit, never lower (a missing base file is a first floor). A pull request that lowers it fails;
                      raise it instead in the PR that adds tests.
  show                Prints the floor (for a local run).

Stdlib only (configparser reads the coverage config). Run from the repo root:
  python3 .github/scripts/coverage_floor.py check [--base SHA]
Exit 0 = fine, 1 = the floor is missing, invalid or lower than the base's, 2 = git could not read the base.
"""

from __future__ import annotations

import argparse
import configparser
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = "backend/.coveragerc"


def floor_of(text: str) -> float | None:
    """fail_under of a .coveragerc text, or None when it is missing or not a number in (0, 100]."""
    parser = configparser.ConfigParser()
    try:
        parser.read_string(text)
        value = float(parser.get("report", "fail_under"))
    except (configparser.Error, ValueError):
        return None
    return value if 0 < value <= 100 else None


def base_text(base: str) -> str | None:
    """The config at the base commit; None when the file does not exist there."""
    proc = subprocess.run(
        ["git", "-C", str(ROOT), "show", f"{base}:{CONFIG}"], capture_output=True, text=True, check=False
    )
    if proc.returncode == 0:
        return proc.stdout
    exists = subprocess.run(
        ["git", "-C", str(ROOT), "cat-file", "-e", f"{base}^{{commit}}"], capture_output=True, check=False
    )
    if exists.returncode != 0:
        raise RuntimeError(f"base commit {base} is not readable")
    return None


def check(head_text: str, base: str | None, read_base=base_text) -> list[str]:
    errors = []
    head = floor_of(head_text)
    if head is None:
        return [f"{CONFIG}: [report] fail_under must be a number in (0, 100]"]
    if base:
        old_text = read_base(base)
        old = floor_of(old_text) if old_text is not None else None
        if old is not None and head < old:
            errors.append(f"{CONFIG}: fail_under {head} is lower than the base's {old}; the floor only goes up")
    return errors


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--base", default="")
    sub.add_parser("show")
    a = p.parse_args(argv)
    head_text = (ROOT / CONFIG).read_text() if (ROOT / CONFIG).exists() else ""
    if a.cmd == "show":
        print(floor_of(head_text))
        return 0
    try:
        errors = check(head_text, a.base or None)
    except RuntimeError as exc:
        print(f"::error::{exc}")
        return 2
    for e in errors:
        print(f"::error file={CONFIG}::{e}")
    if not errors:
        print(f"coverage floor: {floor_of(head_text)}% ({CONFIG})")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
