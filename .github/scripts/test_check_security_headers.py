"""Tests for check_security_headers.sh (AA-879). Run from the repo root:

    python3 -m unittest discover -s .github/scripts -p 'test_*.py'

The script is run for real with fake `curl` and `sleep` first on PATH. The fake writes a fixture header block
into the file given after `-D` (what curl does), so no network is used. A page without a header fails, and so does
no answer at all. AA-1038: the Vercel Deployment Protection branches (bypass header, `vercel curl`, ALLOW_PROTECTED)
were removed with the Vercel project; a 401 is now just a page without the headers.
"""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "check_security_headers.sh"
WORKFLOWS = HERE.parent / "workflows"

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
UNAUTHORIZED = "HTTP/2 401\ncontent-type: text/html; charset=utf-8\n"
APP_REDIRECT = "HTTP/2 307\nlocation: /en\n"

# Fake curl: copy $FAKE_HEADERS into the file after -D, print $FAKE_STDERR on stderr, count calls.
FAKE_FETCH = textwrap.dedent(
    """\
    #!/usr/bin/env bash
    echo x >>"$FAKE_CALLS"
    [ -n "${FAKE_ARGS:-}" ] && printf '%s\n' "$*" >>"$FAKE_ARGS"
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
        for name, body in (("curl", FAKE_FETCH), ("sleep", "#!/usr/bin/env bash\nexit 0\n")):
            path = self.bin / name
            path.write_text(body)
            path.chmod(path.stat().st_mode | stat.S_IXUSR)
        self.calls = Path(self.tmp.name) / "calls"
        self.calls.write_text("")
        self.args = Path(self.tmp.name) / "args"
        self.args.write_text("")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_script(self, headers: str, *, stderr: str = "", exit_code: int = 0,
                   resolve: str | None = None) -> subprocess.CompletedProcess[str]:
        env = {
            "FAKE_ARGS": str(self.args),
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "FAKE_HEADERS": headers,
            "FAKE_STDERR": stderr,
            "FAKE_EXIT": str(exit_code),
            "FAKE_CALLS": str(self.calls),
        }
        if resolve is not None:
            env["RESOLVE"] = resolve
        return subprocess.run(["bash", str(SCRIPT), "https://planner.example.test", "/"],
                              env=env, capture_output=True, text=True, timeout=60)

    def fetches(self) -> int:
        return len(self.calls.read_text().splitlines())

    # --- the app answered -------------------------------------------------------------------------------

    def test_all_six_headers_pass(self) -> None:
        result = self.run_script(GOOD)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("carries all six security headers", result.stdout)
        self.assertEqual(self.fetches(), 1)

    def test_missing_header_fails(self) -> None:
        result = self.run_script(NO_FRAME_OPTIONS)
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing security headers: x-frame-options", result.stdout)
        self.assertEqual(self.fetches(), 3)  # retried

    def test_app_redirect_without_headers_fails(self) -> None:
        result = self.run_script(APP_REDIRECT)
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing security headers", result.stdout)

    def test_no_answer_fails(self) -> None:
        result = self.run_script("", stderr="curl: (6) Could not resolve host", exit_code=6)
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing security headers", result.stdout)

    def test_resolve_pins_the_host_on_plain_curl(self) -> None:
        # AA-1038: the ECS deploy checks the public host through the CloudFront distribution (--resolve).
        result = self.run_script(GOOD, resolve="tripplanner.example.test:443:192.0.2.10")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("--resolve tripplanner.example.test:443:192.0.2.10", self.args.read_text())

    def test_no_resolve_by_default(self) -> None:
        self.run_script(GOOD)
        self.assertNotIn("--resolve", self.args.read_text())

    # --- no Vercel protection branch any more (AA-1038) -----------------------------------------------

    def test_a_401_is_a_page_without_headers_and_fails_after_the_retries(self) -> None:
        result = self.run_script(UNAUTHORIZED)
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing security headers", result.stdout)
        self.assertEqual(self.fetches(), 3)

    def test_curl_stderr_is_not_echoed(self) -> None:
        result = self.run_script("", stderr="curl: (35) SECRET-VALUE-123", exit_code=35)
        self.assertNotIn("SECRET-VALUE-123", result.stdout + result.stderr)

    def test_script_has_no_vercel_branch(self) -> None:
        text = SCRIPT.read_text()
        code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
        for gone in ("x-vercel-protection-bypass", "VERCEL_TOKEN", "vercel curl", "ALLOW_PROTECTED", "npx"):
            self.assertNotIn(gone, code)


class CallersTest(unittest.TestCase):
    """The deploy workflow checks the headers of the image in the runner and of the public host through CloudFront."""

    def test_deploy_web_dev_runs_the_script_on_the_preview_image_and_through_the_distribution(self) -> None:
        text = (WORKFLOWS / "deploy-web-dev.yml").read_text()
        self.assertIn("bash .github/scripts/check_security_headers.sh http://127.0.0.1:3000 /privacy", text)
        self.assertIn('RESOLVE="${PUBLIC_HOST}:443:${IP}" bash .github/scripts/check_security_headers.sh', text)
        self.assertFalse((WORKFLOWS / "vercel-deploy.yml").exists())


if __name__ == "__main__":
    unittest.main()
