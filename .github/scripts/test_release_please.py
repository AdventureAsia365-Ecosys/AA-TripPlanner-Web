"""Lint of the release-please set-up (AA-891). Run from the repo root:

    python3 -m unittest discover -s .github/scripts -p 'test_*.py'

Stdlib only (this suite runs before Python dependencies are installed), so the workflows are read as text.
Pinned here:
  - the release-please config / manifest (manifest mode, bump-minor-pre-major, v-prefixed tags, CHANGELOG.md);
  - .github/workflows/release-please.yml: push to main + workflow_dispatch only, environment "release",
    GITHUB_TOKEN read-only, the release PR opened with an aa-ecosys-ci-bot App token (so required checks run on it);
  - a release commit (CHANGELOG.md + the manifest, repo root) deploys nothing: the push `paths:` filters of the two
    deploy workflows match none of the files release-please writes.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
CONFIG = ROOT / "release-please-config.json"
MANIFEST = ROOT / ".release-please-manifest.json"
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
# What a release commit touches (release-type "simple" also updates version.txt when one exists).
RELEASE_FILES = ["CHANGELOG.md", ".release-please-manifest.json", "release-please-config.json", "version.txt"]
DEPLOY_WORKFLOWS = ["vercel-deploy.yml", "deploy-lambdas.yml"]


def _semver(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def _glob_re(pattern: str) -> re.Pattern[str]:
    """GitHub `paths:` glob -> regex (`**` any depth, `*` one segment, `?` one char)."""
    out = ""
    i = 0
    while i < len(pattern):
        if pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile(f"^{out}$")


def _push_paths(text: str) -> list[str] | None:
    """The `paths:` list under `on.push` (None when push has no paths filter)."""
    lines = text.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if re.match(r"^  push:\s*$", ln))
    except StopIteration:
        return None
    paths: list[str] | None = None
    for ln in lines[start + 1:]:
        if re.match(r"^  \S", ln) or re.match(r"^\S", ln):
            break
        if re.match(r"^    paths:\s*$", ln):
            paths = []
            continue
        m = re.match(r"^      - [\"']?([^\"']+)[\"']?\s*$", ln)
        if paths is not None and m:
            paths.append(m.group(1))
        elif paths is not None and re.match(r"^    \S", ln):
            break
    return paths


class ReleasePleaseConfig(unittest.TestCase):
    def test_manifest_mode_options(self) -> None:
        cfg = json.loads(CONFIG.read_text())
        self.assertIs(cfg.get("bump-minor-pre-major"), True)
        self.assertIs(cfg.get("include-v-in-tag"), True)
        self.assertEqual(list(cfg["packages"]), ["."])
        pkg = cfg["packages"]["."]
        self.assertEqual(pkg.get("changelog-path"), "CHANGELOG.md")
        self.assertEqual(pkg.get("release-type"), "simple")

    def test_manifest_holds_one_semver(self) -> None:
        manifest = json.loads(MANIFEST.read_text())
        self.assertEqual(list(manifest), ["."])
        self.assertRegex(manifest["."], SEMVER)

    def test_release_as_only_moves_forward(self) -> None:
        """`release-as` pins the first release (0.4.0) and is deleted after that release PR merges. It may equal
        the manifest (the release PR itself bumps the manifest to it) but never sit below it."""
        release_as = json.loads(CONFIG.read_text())["packages"]["."].get("release-as")
        if release_as is None:
            return
        self.assertRegex(release_as, SEMVER)
        self.assertGreaterEqual(_semver(release_as), _semver(json.loads(MANIFEST.read_text())["."]))


class ReleaseWorkflow(unittest.TestCase):
    text = (WORKFLOWS / "release-please.yml").read_text()

    def test_triggers_only_push_to_main_and_dispatch(self) -> None:
        on_block = self.text.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
        events = re.findall(r"^  ([a-z_]+):", on_block, flags=re.M)
        self.assertEqual(events, ["push", "workflow_dispatch"])
        self.assertRegex(on_block, r"branches: \[main\]")

    def test_github_token_is_read_only(self) -> None:
        self.assertRegex(self.text, r"\npermissions:\n  contents: read\n")
        self.assertEqual(self.text.count("permissions:"), 1)

    def test_environment_release(self) -> None:
        self.assertRegex(self.text, r"\n    environment: release\n")

    def test_app_token_opens_the_release_pr(self) -> None:
        self.assertIn("uses: actions/create-github-app-token@v2", self.text)
        self.assertIn("app-id: ${{ vars.AA_CI_BOT_APP_ID }}", self.text)
        self.assertIn("private-key: ${{ secrets.AA_CI_BOT_PRIVATE_KEY }}", self.text)
        self.assertIn("uses: googleapis/release-please-action@v4", self.text)
        self.assertIn("token: ${{ steps.app-token.outputs.token }}", self.text)
        self.assertNotIn("secrets.GITHUB_TOKEN", self.text)
        self.assertNotIn("github.token", self.text)


class ReleaseCommitDeploysNothing(unittest.TestCase):
    def test_push_paths_skip_release_files(self) -> None:
        for name in DEPLOY_WORKFLOWS:
            with self.subTest(workflow=name):
                paths = _push_paths((WORKFLOWS / name).read_text())
                self.assertTrue(paths, f"{name}: push needs a paths filter")
                for pattern in paths:
                    for f in RELEASE_FILES:
                        self.assertFalse(_glob_re(pattern.lstrip("!")).match(f), f"{name}: {pattern} matches {f}")

    def test_glob_helper(self) -> None:
        self.assertTrue(_glob_re("frontend/**").match("frontend/app/page.tsx"))
        self.assertFalse(_glob_re("frontend/**").match("CHANGELOG.md"))
        self.assertTrue(_glob_re("**").match("CHANGELOG.md"))


if __name__ == "__main__":
    unittest.main()
