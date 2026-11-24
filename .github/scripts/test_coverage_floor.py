"""Tests for .github/scripts/coverage_floor.py (AA-1119). Run from the repo root:

    python3 -m unittest discover -s .github/scripts -p 'test_coverage_floor.py'

Stdlib only. The real backend/.coveragerc has a valid floor, ci.yml runs pytest with it, and the ratchet refuses a
lower or missing floor against a base (read through an injected reader, no git).
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
_spec = importlib.util.spec_from_file_location("coverage_floor", HERE / "coverage_floor.py")
cf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cf)


def rc(floor: str) -> str:
    return f"[run]\nsource = .\n\n[report]\nfail_under = {floor}\n"


class FloorTests(unittest.TestCase):
    def test_repo_config_has_a_floor(self):
        floor = cf.floor_of((ROOT / "backend" / ".coveragerc").read_text())
        self.assertIsNotNone(floor)
        self.assertGreaterEqual(floor, 90.0)

    def test_ci_runs_pytest_with_the_config(self):
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
        self.assertIn("pytest -v --tb=short --cov --cov-config=.coveragerc", ci)
        self.assertIn("python3 .github/scripts/coverage_floor.py check", ci)
        self.assertIn("merge_group:", ci)
        reqs = (ROOT / "backend" / "requirements.txt").read_text()
        dev = reqs.split("# Dev / test", 1)[1]
        self.assertIn("pytest-cov==", dev)  # dev block only: never packaged into a Lambda

    def test_invalid_floors(self):
        for text in ("", "[report]\n", rc("abc"), rc("0"), rc("101"), "not ini"):
            self.assertIsNone(cf.floor_of(text), text)

    def test_ratchet(self):
        self.assertEqual(cf.check(rc("90.5"), "base", lambda _b: rc("90.0")), [])
        self.assertEqual(cf.check(rc("90.0"), "base", lambda _b: rc("90.0")), [])
        self.assertEqual(cf.check(rc("90.0"), "base", lambda _b: None), [])  # first floor
        self.assertEqual(cf.check(rc("90.0"), None), [])
        lower = cf.check(rc("89.9"), "base", lambda _b: rc("90.0"))
        self.assertEqual(len(lower), 1)
        self.assertIn("only goes up", lower[0])
        self.assertEqual(len(cf.check(rc("x"), "base", lambda _b: rc("90.0"))), 1)


if __name__ == "__main__":
    unittest.main()
