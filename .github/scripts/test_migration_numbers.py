"""Migration numbers of a pull request (AA-1024 follow-up): ``.github/scripts/migration_numbers.py`` and its CI step.

An added tp-NNN file must take main max + 1 (then + 2, ...), a number is used once, and another open PR adding the
same number under another name refuses the newer PR (two lanes that both took main max + 1). Stdlib only: runs under
``python3 -m unittest`` (CI script tests) and pytest."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
import migration_numbers as mn  # noqa: E402

BASE = ["001_base.sql", "024_drop_widget.sql", "025_gizmo_rows.sql"]


def _pr(number, *paths):
    return {"number": number, "files": [{"path": p, "additions": 1, "deletions": 0} for p in paths]}


class NumberTest(unittest.TestCase):
    def test_next_numbers_pass(self):
        self.assertEqual(mn.number_problems(BASE, BASE + ["026_widget_gate.sql", "027_widget_rows.sql"]), ([], []))
        self.assertEqual(mn.number_problems(BASE, BASE), ([], []))

    def test_taken_lower_and_skipped_numbers_are_refused(self):
        errors, _ = mn.number_problems(BASE, BASE + ["025_widget_gate.sql"])
        self.assertTrue(any("number 025 is also used by" in e for e in errors), errors)
        self.assertTrue(any("`025_widget_gate.sql`: number 025 is already taken on main" in e for e in errors), errors)
        errors, _ = mn.number_problems(BASE, BASE + ["023_reserved.sql"])
        self.assertEqual(len(errors), 1)
        self.assertIn("not the next one (026 = main max + 1)", errors[0])
        errors, _ = mn.number_problems(BASE, BASE + ["028_gap.sql"])
        self.assertEqual(len(errors), 1)
        self.assertIn("`028_gap.sql`: number 028 is not the next one (026", errors[0])

    def test_two_files_with_one_number_in_the_head_are_refused(self):
        errors, _ = mn.number_problems(BASE, BASE + ["026_widget_gate.sql", "026_gizmo_gate.sql"])
        self.assertTrue(any("is also used by" in e for e in errors), errors)

    def test_an_older_open_pr_on_the_same_number_refuses_the_newer_one(self):
        head = BASE + ["026_widget_gate.sql"]
        older = _pr(40, "migrations/026_gizmo_gate.sql", "backend/x.py")
        errors, warnings = mn.number_problems(BASE, head, [older], pr=41)
        self.assertEqual(warnings, [])
        self.assertEqual(errors, ["`026_widget_gate.sql`: open PR #40 also adds tp-026 (`026_gizmo_gate.sql`) and is "
                                  "older: renumber this PR's file to the next free number"])
        errors, warnings = mn.number_problems(BASE, head, [older], pr=39)
        self.assertEqual(errors, [])
        self.assertEqual(len(warnings), 1)
        self.assertIn("open PR #40 also adds tp-026", warnings[0])

    def test_same_file_other_numbers_this_pr_and_other_folders_pass(self):
        head = BASE + ["026_widget_gate.sql"]
        prs = [
            _pr(41, "migrations/026_widget_gate.sql"),          # this PR
            _pr(30, "migrations/026_widget_gate.sql"),          # a stacked PR carrying the same file
            _pr(31, "migrations/027_other.sql"),                # another number
            _pr(32, "migrations/025_gizmo_rows.sql"),           # an edit of a file on main
            _pr(33, "migrations/tests/026_fixture.sql"),        # not a migration of this folder
            _pr(34, "docs/026_note.sql"),
        ]
        self.assertEqual(mn.number_problems(BASE, head, prs, pr=41), ([], []))


def _git(cwd, *args):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout.strip()


class MainTest(unittest.TestCase):
    @unittest.skipIf(shutil.which("git") is None, "needs git")
    def test_main_on_a_real_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            (t / "migrations").mkdir()
            for name in BASE:
                (t / "migrations" / name).write_text("BEGIN;\nCOMMIT;\n")
            _git(t, "init", "-q")
            _git(t, "add", "-A")
            _git(t, "commit", "-qm", "base")
            base = _git(t, "rev-parse", "HEAD")
            (t / "migrations" / "026_widget_gate.sql").write_text("BEGIN;\nCOMMIT;\n")
            _git(t, "add", "-A")
            _git(t, "commit", "-qm", "the PR")
            prs = t / "open.json"
            prs.write_text(json.dumps([_pr(7, "migrations/026_gizmo_gate.sql")]))
            cwd = os.getcwd()
            try:
                os.chdir(t)
                ok = mn.main(["--migrations-dir", "migrations", "--base", base, "--pr", "9"])
                clash = mn.main(["--migrations-dir", "migrations", "--base", base, "--pr", "9", "--open-prs", str(prs)])
                unreadable = mn.main(["--migrations-dir", "migrations", "--base", base, "--pr", "9",
                                      "--open-prs", str(t / "missing.json")])
                stale = mn.main(["--migrations-dir", "migrations", "--base", "HEAD"])
            finally:
                os.chdir(cwd)
            self.assertEqual((ok, clash, unreadable, stale), (0, 1, 0, 0))


class CiStepTest(unittest.TestCase):
    def test_the_migrations_job_runs_the_check_on_pull_requests_with_live_main(self):
        ci = (REPO / ".github" / "workflows" / "ci.yml").read_text()
        job = ci[ci.index("\n  migrations:\n"):ci.index("\n  frontend:\n")]
        self.assertIn("python3 .github/scripts/migration_numbers.py", job)
        self.assertIn("git fetch --quiet --depth=1 origin main", job)
        self.assertIn('--base "$(git rev-parse FETCH_HEAD)"', job)
        self.assertIn("gh pr list --repo \"$GITHUB_REPOSITORY\" --base main --state open --limit 100 --json number,files",
                      job)
        self.assertIn("      pull-requests: read", job)

    def test_deploy_lambdas_grants_what_the_called_ci_asks_for(self):
        """ci.yml runs as deploy-lambdas.yml's `ci` job (workflow_call): a job permission the caller does not grant
        fails the whole deploy at startup."""
        wf = (REPO / ".github" / "workflows" / "deploy-lambdas.yml").read_text()
        ci_job = wf[wf.index("\n  ci:\n"):wf.index("\n  deploy:\n")]
        self.assertIn("      pull-requests: read", ci_job)
        self.assertIn("      contents: read", ci_job)


if __name__ == "__main__":
    unittest.main()
