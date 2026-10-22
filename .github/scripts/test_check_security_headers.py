"""Tests for check_security_headers.sh (AA-879). Run from the repo root:

    python3 -m unittest discover -s .github/scripts -p 'test_*.py'

The script is run for real with fake `curl`, `npx` and `sleep` first on PATH. Each fake writes a fixture
header block into the file given after `-D` (what curl does), so no network is used. The cases pin the
review finding of AA-879: a preview behind Vercel Deployment Protection (no automation bypass on the
project yet) must not turn the Vercel deploy run red while ALLOW_PROTECTED=1, but a page the app itself
answers without a header must still fail, and so must no answer at all.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "check_security_headers.sh"
WORKFLOW = HERE.parent / "workflows" / "vercel-deploy.yml"

GOOD = textwrap.dedent(
    """\
    HTTP/2 200
    content-security-policy: default-src 'self'; frame-ancestors 'none'
    x-content-type-options: nosniff
    x-frame-options: DENY
    referrer-policy: strict-origin-when-cross-origin
    strict-transport-security: max-age=63072000; includeSubDomains; preload
    permissions-policy: camera=(), microphone=()
    """
)
NO_FRAME_OPTIONS = "\n".join(line for line in GOOD.splitlines() if not line.startswith("x-frame-options")) + "\n"
VERCEL_401 = "HTTP/2 401\ncontent-type: text/html; charset=utf-8\nserver: Vercel\n"
VERCEL_SSO_REDIRECT = "HTTP/2 302\nlocation: https://vercel.com/sso-api?url=https%3A%2F%2Fx.vercel.app%2F\nserver: Vercel\n"
APP_REDIRECT = "HTTP/2 307\nlocation: /en\nserver: Vercel\n"

# Fake curl / npx: copy $FAKE_HEADERS into the file after -D, print $FAKE_STDERR on stderr, count calls.
FAKE_FETCH = textwrap.dedent(
    """\
    #!/usr/bin/env bash
    echo x >>"$FAKE_CALLS"
    out=""
    while [ $# -gt 0 ]; do
      if [ "$1" = "-D" ]; then out="$2"; shift; fi
      shift
    done
    [ -n "${FAKE_STDERR:-}" ] && printf '%s\\n' "$FAKE_STDERR" >&2
    if [ -n "$out" ] && [ -n "${FAKE_HEADERS:-}" ]; then printf '%s' "$FAKE_HEADERS" | sed 's/$/\\r/' >"$out"; fi
    exit "${FAKE_EXIT:-0}"
    """
)


class CheckSecurityHeadersTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.bin = Path(self.tmp.name) / "bin"
        self.bin.mkdir()
        for name, body in (("curl", FAKE_FETCH), ("npx", FAKE_FETCH), ("sleep", "#!/usr/bin/env bash\nexit 0\n")):
            path = self.bin / name
            path.write_text(body)
            path.chmod(path.stat().st_mode | stat.S_IXUSR)
        self.calls = Path(self.tmp.name) / "calls"
        self.calls.write_text("")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_script(self, headers: str, *, allow: str | None = None, token: bool = False, stderr: str = "",
                   exit_code: int = 0) -> subprocess.CompletedProcess[str]:
        env = {
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "FAKE_HEADERS": headers,
            "FAKE_STDERR": stderr,
            "FAKE_EXIT": str(exit_code),
            "FAKE_CALLS": str(self.calls),
        }
        if allow is not None:
            env["ALLOW_PROTECTED"] = allow
        if token:
            env["VERCEL_TOKEN"] = "fake-team-token"
        return subprocess.run(["bash", str(SCRIPT), "https://preview.example.vercel.app", "/"],
                              env=env, capture_output=True, text=True, timeout=60)

    def fetches(self) -> int:
        return len(self.calls.read_text().splitlines())

    # --- the app answered -------------------------------------------------------------------------------

    def test_all_six_headers_pass(self) -> None:
        result = self.run_script(GOOD)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("carries all six security headers", result.stdout)
        self.assertEqual(self.fetches(), 1)

    def test_missing_header_fails_even_with_allow_protected(self) -> None:
        result = self.run_script(NO_FRAME_OPTIONS, allow="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing security headers: x-frame-options", result.stdout)
        self.assertEqual(self.fetches(), 3)  # retried

    def test_app_redirect_is_not_protection(self) -> None:
        result = self.run_script(APP_REDIRECT, allow="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing security headers", result.stdout)

    def test_no_answer_fails_even_with_allow_protected(self) -> None:
        result = self.run_script("", allow="1", stderr="curl: (6) Could not resolve host", exit_code=6)
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing security headers", result.stdout)

    # --- Vercel Deployment Protection answered ----------------------------------------------------------

    def test_protected_401_fails_by_default(self) -> None:
        result = self.run_script(VERCEL_401)
        self.assertEqual(result.returncode, 1)
        self.assertIn("::error::", result.stdout)
        self.assertIn("Deployment Protection", result.stdout)
        self.assertEqual(self.fetches(), 1)  # no pointless retries

    def test_protected_401_warns_with_allow_protected(self) -> None:
        result = self.run_script(VERCEL_401, allow="1", token=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("::warning::", result.stdout)
        self.assertIn("NOT checked", result.stdout)

    def test_sso_redirect_warns_with_allow_protected(self) -> None:
        result = self.run_script(VERCEL_SSO_REDIRECT, allow="1")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("::warning::", result.stdout)

    def test_cli_without_bypass_token_warns_with_allow_protected(self) -> None:
        result = self.run_script("", allow="1", token=True, stderr="Not creating an automation bypass token",
                                 exit_code=1)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("::warning::", result.stdout)

    def test_cli_without_bypass_token_fails_by_default(self) -> None:
        result = self.run_script("", token=True, stderr="Not creating an automation bypass token", exit_code=1)
        self.assertEqual(result.returncode, 1)

    def test_cli_stderr_is_not_echoed(self) -> None:
        secret = "Not creating an automation bypass token SECRET-VALUE-123"
        result = self.run_script("", allow="1", token=True, stderr=secret, exit_code=1)
        self.assertNotIn("SECRET-VALUE-123", result.stdout + result.stderr)


class PreviewStepGuardTest(unittest.TestCase):
    """The preview step must stay green on a protected preview until the bypass exists (AA-879 review)."""

    def step_block(self) -> str:
        text = WORKFLOW.read_text()
        match = re.search(r"- name: Check the preview security headers\n(.*?)(?=\n      - name: |\Z)", text, re.S)
        self.assertIsNotNone(match, "preview header step not found in vercel-deploy.yml")
        return match.group(1)

    def test_step_runs_the_script_with_allow_protected(self) -> None:
        block = self.step_block()
        self.assertIn("check_security_headers.sh", block)
        self.assertRegex(block, r'ALLOW_PROTECTED: "1"')

    def test_guard_carries_a_todo(self) -> None:
        text = WORKFLOW.read_text()
        self.assertIn("TODO(AA-879)", text)

    def test_step_is_not_blanket_continue_on_error(self) -> None:
        # A missing header on a page the app answered must still turn the run red.
        self.assertNotIn("continue-on-error", self.step_block())


if __name__ == "__main__":
    unittest.main()
