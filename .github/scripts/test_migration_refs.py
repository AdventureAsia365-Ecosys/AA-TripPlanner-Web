"""Dangling migration references (AA-1024 l22, S271 lessons guard G10): ``.github/scripts/migration_refs.py``, and
the CI step that runs it, and the pre-push hook's refusal of a push mid-rebase.

The script is identical in AA-CIS-App, AA-Booking, AA-Partner and here (the three others call it from their migration
preflight and pin it with a pytest copy of these cases). A renumbered migration keeps its name, so a test or doc that
still names ``016_name`` after the file became ``014_name.sql`` is refused, on the lines the PR adds only; so is a
reference to a file the PR removes. Stdlib only: runs under ``python3 -m unittest`` (CI script tests) and pytest."""

from __future__ import annotations

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
import migration_refs as mr  # noqa: E402

HEAD = ["010_base.sql", "014_widget_gate.sql", "015_gizmo_rows.sql"]
BASE = ["010_base.sql", "011_old_sprocket.sql"]


def _lines(*texts):
    return [("tests/test_x.py", i + 1, t) for i, t in enumerate(texts)]


def _git(cwd, *args):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout.strip()


class DanglingTest(unittest.TestCase):
    def test_old_number_of_a_renumbered_file_is_refused(self):
        got = mr.dangling(_lines("path = MIG / '016_widget_gate.sql'", "see 016_widget_gate"), HEAD, BASE)
        self.assertEqual(len(got), 2)
        self.assertTrue(got[0].startswith("tests/test_x.py:1: `016_widget_gate.sql` names migration number 016"))
        self.assertIn("014_widget_gate.sql", got[0])

    def test_removed_file_is_refused(self):
        got = mr.dangling(_lines("replays 011_old_sprocket.sql first"), HEAD, BASE)
        self.assertEqual(got, ["tests/test_x.py:1: `011_old_sprocket.sql` names 011_old_sprocket.sql, which this PR "
                               "removes or renames"])

    def test_current_names_other_owners_fixtures_and_marked_lines_pass(self):
        self.assertEqual(mr.dangling(_lines(
            "the file 014_widget_gate.sql and 015_gizmo_rows",
            "cis 384_llm_widget.sql is another owner's file",
            "fixture names 999_x and 010_y",
            "renumbered from 016_widget_gate  # old-mig-ok: history note",
            "test_tp016_widget_gate and tp016_widget_gate and 1_000_000",
        ), HEAD, BASE), [])

    def test_added_lines_reads_only_added_lines(self):
        diff = "\n".join(["diff --git a/a.py b/a.py", "--- a/a.py", "+++ b/a.py", "@@ -3,0 +4,1 @@",
                          "+x = '016_widget_gate'", "@@ -9 +10 @@", "-old = 1", "+++ content, not a header"])
        self.assertEqual(mr.added_lines(diff), [("a.py", 4, "x = '016_widget_gate'"),
                                                ("a.py", 10, "++ content, not a header")])

    @unittest.skipIf(shutil.which("git") is None, "needs git")
    def test_check_on_a_real_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            (t / "migrations").mkdir()
            (t / "migrations" / "001_a.sql").write_text("BEGIN;\nCOMMIT;\n")
            (t / "notes.md").write_text("history\n")
            _git(t, "init", "-q")
            _git(t, "add", "-A")
            _git(t, "commit", "-qm", "base")
            base = _git(t, "rev-parse", "HEAD")
            (t / "migrations" / "002_widget_gate.sql").write_text("BEGIN;\nCOMMIT;\n")
            (t / "notes.md").write_text("history\nsee 004_widget_gate.sql\n")
            _git(t, "add", "-A")
            _git(t, "commit", "-qm", "renumbered")
            cwd = os.getcwd()
            try:
                os.chdir(t)
                errors = mr.check("migrations", base)
            finally:
                os.chdir(cwd)
            self.assertEqual(len(errors), 1)
            self.assertTrue(errors[0].startswith("notes.md:2: `004_widget_gate.sql`"))


class CiStepTest(unittest.TestCase):
    def test_the_migrations_job_runs_the_check_on_pull_requests(self):
        ci = (REPO / ".github" / "workflows" / "ci.yml").read_text()
        self.assertIn("python3 .github/scripts/migration_refs.py --migrations-dir migrations --base", ci)
        self.assertIn("github.event.pull_request.base.sha", ci)


class PrePushRebaseTest(unittest.TestCase):
    @unittest.skipIf(shutil.which("git") is None or shutil.which("bash") is None, "needs git and bash")
    def test_hook_refuses_a_push_mid_rebase(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            (t / ".githooks").mkdir()
            shutil.copy(REPO / ".githooks" / "pre-push", t / ".githooks" / "pre-push")
            _git(t, "init", "-q", "-b", "main")
            _git(t, "add", "-A")
            _git(t, "commit", "-qm", "base")
            line = f"refs/heads/main {_git(t, 'rev-parse', 'HEAD')} refs/heads/main {'0' * 40}\n"
            rebase = Path(_git(t, "rev-parse", "--path-format=absolute", "--git-path", "rebase-merge"))
            rebase.mkdir()
            p = subprocess.run(["bash", ".githooks/pre-push", "origin", "x"], cwd=t, input=line, capture_output=True,
                               text=True, timeout=60)
            self.assertEqual(p.returncode, 1)
            self.assertIn("a rebase is in progress", p.stderr)
            rebase.rmdir()
            p = subprocess.run(["bash", ".githooks/pre-push", "origin", "x"], cwd=t, input=line, capture_output=True,
                               text=True, timeout=60)
            self.assertNotIn("a rebase is in progress", p.stderr)


if __name__ == "__main__":
    unittest.main()
