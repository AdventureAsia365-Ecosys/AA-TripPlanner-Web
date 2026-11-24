"""AA-1119: the merge queue ruleset copy and the workflows the queue waits on. Run from the repo root:

    python3 -m unittest discover -s .github/scripts -p 'test_merge_queue_ruleset.py'

Stdlib only (workflows read as text). Each required check of main (classic branch protection) must report on the
queue's merge_group commit, or every queued PR waits for the check timeout and is dropped.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
RULESET = json.loads((ROOT / ".github" / "rulesets" / "main-merge-queue.json").read_text())
# Required checks of main and the workflow that reports each (read back with
# `gh api repos/AdventureAsia365-Ecosys/AA-TripPlanner-Web/branches/main/protection`).
REQUIRED = {
    "Backend (pytest)": "ci.yml",
    "Frontend (lint + build)": "ci.yml",
    "Migrations (lint, PG 15 replay)": "ci.yml",
    "Security Audit": "ci.yml",
    "Settings Lint": "ci.yml",
    "gitleaks": "secret-scan.yml",
}
# secret-scan.yml gets its merge_group trigger in a second PR: the gitleaks job runs the BASE branch's copy, and the
# guard before this change refused a second trigger (the same two steps as AA-Booking).
PENDING_TRIGGER = {"secret-scan.yml"}
MERGE_GROUP = "  merge_group:\n    types: [checks_requested]"


def on_block(text: str) -> str:
    start = text.index("\non:\n")
    end = re.compile(r"\n(permissions|concurrency|jobs|env):").search(text, start).start()
    return text[start:end]


class RulesetTest(unittest.TestCase):
    def test_merge_queue_rule(self):
        self.assertEqual(RULESET["target"], "branch")
        self.assertEqual(RULESET["enforcement"], "active")
        self.assertEqual(RULESET["conditions"]["ref_name"]["include"], ["~DEFAULT_BRANCH"])
        (rule,) = RULESET["rules"]
        self.assertEqual(rule["type"], "merge_queue")
        self.assertEqual(rule["parameters"]["merge_method"], "SQUASH")
        self.assertEqual(rule["parameters"]["grouping_strategy"], "ALLGREEN")
        # Admins bypass only through a pull request, never by a direct push.
        self.assertTrue(all(a["bypass_mode"] == "pull_request" for a in RULESET["bypass_actors"]))

    def test_required_checks_report_on_merge_group(self):
        for check, wf in REQUIRED.items():
            with self.subTest(check=check):
                text = (WORKFLOWS / wf).read_text()
                self.assertRegex(text, rf"name: {re.escape(check)}\n", f"{check} is not a job name of {wf}")
                if wf not in PENDING_TRIGGER:
                    self.assertIn(MERGE_GROUP, on_block(text), wf)

    def test_pr_title_reports_on_merge_group(self):
        text = (WORKFLOWS / "pr-title.yml").read_text()
        self.assertIn(MERGE_GROUP, on_block(text))
        self.assertIn("if: github.event_name == 'merge_group'", text)


if __name__ == "__main__":
    unittest.main()
