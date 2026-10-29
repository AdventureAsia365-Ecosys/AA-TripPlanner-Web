"""AA-994 S5b: the visual run is wired after every deploy and stays report-only (offline, reads the workflow text)."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[1] / "workflows"
DEPLOY = (WORKFLOWS / "vercel-deploy.yml").read_text()
VISUAL = (WORKFLOWS / "visual.yml").read_text()


def _job(text: str, name: str) -> str:
    m = re.search(rf"^  {name}:\n(.*?)(?=^  [\w-]+:\n|\Z)", text, re.S | re.M)
    assert m, f"job {name} not found"
    return m.group(1)


class VisualWiringTest(unittest.TestCase):
    def test_visual_runs_after_a_successful_deploy_on_the_public_host_or_the_preview(self) -> None:
        job = _job(DEPLOY, "visual")
        self.assertIn("needs: deploy", job)
        self.assertIn("uses: ./.github/workflows/visual.yml", job)
        self.assertIn("'https://aa-tripplanner.vercel.app' || needs.deploy.outputs.url", job)
        self.assertIn("sha: ${{ needs.deploy.outputs.sha }}", job)

    def test_the_deploy_verdict_does_not_wait_on_the_visual_run(self) -> None:
        self.assertIn("needs: [deploy, verify]", _job(DEPLOY, "result"))

    def test_visual_inputs_reach_scripts_through_env_only(self) -> None:
        for run in re.findall(r"run: \|\n((?:\s{10}.*\n)+)", VISUAL):
            self.assertNotIn("${{", run)

    def test_a_protected_preview_without_the_bypass_is_skipped_not_red(self) -> None:
        self.assertIn('[ "$TARGET" = "preview" ] && [ -z "$BYPASS" ]', VISUAL)
        self.assertIn("ok=false", VISUAL)


if __name__ == "__main__":
    unittest.main()
