"""AA-994 S5b: the visual run is wired after every deploy and stays report-only (offline, reads the workflow text).

AA-1038: the frontend deploys only to ECS (deploy-web-dev.yml); the Vercel deploy, its previews and the bypass secret
were retired, so the visual run captures the public host and reads no secret.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[1] / "workflows"
DEPLOY = (WORKFLOWS / "deploy-web-dev.yml").read_text()
VISUAL = (WORKFLOWS / "visual.yml").read_text()
PUBLIC = "https://tripplanner.lumiguides.it.com"


def _job(text: str, name: str) -> str:
    m = re.search(rf"^  {name}:\n(.*?)(?=^  [\w-]+:\n|\Z)", text, re.S | re.M)
    assert m, f"job {name} not found"
    return m.group(1)


class VisualWiringTest(unittest.TestCase):
    def test_visual_runs_after_a_verified_deploy_on_the_public_host(self) -> None:
        job = _job(DEPLOY, "visual")
        self.assertIn("needs: verify", job)
        self.assertIn("if: needs.verify.result == 'success'", job)
        self.assertIn("uses: ./.github/workflows/visual.yml", job)
        self.assertIn(f"base_url: {PUBLIC}", job)
        self.assertIn("sha: ${{ github.sha }}", job)
        self.assertNotIn("secrets: inherit", job)

    def test_the_deploy_verdict_does_not_wait_on_the_visual_run(self) -> None:
        self.assertIn("needs: [config, deploy, verify, release]", _job(DEPLOY, "result"))

    def test_visual_inputs_reach_scripts_through_env_only(self) -> None:
        for run in re.findall(r"run: \|\n((?:\s{10}.*\n)+)", VISUAL):
            self.assertNotIn("${{", run)

    def test_no_vercel_left_and_no_secret_read(self) -> None:
        self.assertFalse((WORKFLOWS / "vercel-deploy.yml").exists())
        self.assertNotIn("secrets.", VISUAL)
        self.assertNotIn("pull-requests: write", VISUAL)  # the caller grants contents: read only
        self.assertIn(f"default: {PUBLIC}", VISUAL)
        code = "\n".join(line for line in VISUAL.splitlines() if not line.lstrip().startswith("#"))
        self.assertNotIn("vercel", code.lower())


if __name__ == "__main__":
    unittest.main()
