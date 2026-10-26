"""Tests for the post-deploy verify (AA-993 V4, ADR 0016 D3). Run from the repo root:

    python3 -m unittest discover -s .github/scripts -p 'test_*.py'

Stdlib only (this suite runs before Python dependencies are installed), so the workflows are read as text.

- .github/scripts/post_deploy_verify.sh runs for real with the real rows files (.github/verify/web.rows,
  lambdas.rows) and fake `curl`, `aws` and `sleep` first on PATH, which answer from fixture JSON (no network, no
  AWS). All good exits 0; a wrong git_sha or version, an HTTP error, a Lambda update not Successful, a Lambda that
  runs other code than the uploaded artifact, an artifact built from another commit, a missing input and an invalid
  EXPECTED_SHA each exit 1. AWS error text never reaches the log or the summary unscrubbed, and an invalid
  EXPECTED_SHA is echoed nowhere.
- .github/scripts/expected_release.sh reads the manifest version of the expected commit and never echoes a bad SHA.
- The workflows: post-deploy-verify.yml is callable + dispatchable (expected_sha, app) with a per-run concurrency
  group on the call path and no `environment:` on the OIDC job; vercel-deploy.yml verifies production only;
  deploy-lambdas.yml lost its inline smoke, uploads the metadata the verify reads and calls the verify with
  `secrets: inherit`; each `result` step runs here with the `needs` JSON GitHub would pass.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SCRIPT = HERE / "post_deploy_verify.sh"
EXPECTED_RELEASE = HERE / "expected_release.sh"
WORKFLOWS = ROOT / ".github" / "workflows"
VERIFY_WF = (WORKFLOWS / "post-deploy-verify.yml").read_text()
VERCEL_WF = (WORKFLOWS / "vercel-deploy.yml").read_text()
LAMBDAS_WF = (WORKFLOWS / "deploy-lambdas.yml").read_text()

SHA = "3f2a9c1e7b4d6a0f5e8c2b1d9a7f4e6c0b3d5a2e"
OLD_SHA = "9e1b7c3a5d2f4e6a8c0b1d3f5a7e9c2b4d6f8a0c"
VERSION = "0.8.2"
CODE = "q1w2e3r4t5y6u7i8o9p0asdfghjklzxcvbnmQWERTYU="
OTHER_CODE = "ZZZZe3r4t5y6u7i8o9p0asdfghjklzxcvbnmQWERTYU="
FUNCS = {"aa-tripplanner-dev-browse": "browse.zip", "aa-tripplanner-dev-assembly": "assembly.zip"}
ZERO = "00000000-0000-0000-0000-000000000000"
AWS_LEAK = "arn:aws:sts::123456789012:assumed-role/x/y 123456789012"

HAVE_TOOLS = bool(shutil.which("bash") and shutil.which("jq"))

FAKE_CURL = r'''#!/usr/bin/env python3
import json, os, sys
from urllib.parse import urlparse, parse_qs
args = sys.argv[1:]
out = fmt = url = None
i = 0
while i < len(args):
    a = args[i]
    if a in ("-o", "-w", "--max-time", "-H"):
        if a == "-o":
            out = args[i + 1]
        elif a == "-w":
            fmt = args[i + 1]
        i += 2
        continue
    if not a.startswith("-"):
        url = a
    i += 1
with open(os.environ["FAKE_URLS"], "a") as f:
    f.write(url + "\n")
u = urlparse(url)
q = parse_qs(u.query)
key = u.path + ("?resource=" + q["resource"][0] if "resource" in q else "")
routes = json.load(open(os.environ["FAKE_HTTP"]))
route = routes.get(key, {"status": 404, "body": {"detail": "no fixture for " + key}})
if out:
    with open(out, "w") as f:
        json.dump(route["body"], f)
if fmt:
    sys.stdout.write(str(route["status"]))
'''

FAKE_AWS = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
def opt(name):
    return args[args.index(name) + 1]
state = json.load(open(os.environ["FAKE_AWS"]))
if state.get("error"):
    sys.stderr.write(state["error"] + "\n")
    sys.exit(255)
if args[:2] == ["lambda", "get-function-configuration"]:
    fn = state["functions"].get(opt("--function-name"))
    if fn is None:
        sys.stderr.write("ResourceNotFoundException: Function not found\n")
        sys.exit(254)
    print(json.dumps(fn))
elif args[:2] == ["s3api", "head-object"]:
    if opt("--bucket") != os.environ["FAKE_BUCKET"]:
        sys.stderr.write("An error occurred (403) when calling the HeadObject operation: Forbidden\n")
        sys.exit(254)
    obj = state["objects"].get(opt("--key"))
    if obj is None:
        sys.stderr.write("An error occurred (404) when calling the HeadObject operation: Not Found\n")
        sys.exit(254)
    print(json.dumps(obj))
else:
    sys.stderr.write("unexpected aws call: " + " ".join(args) + "\n")
    sys.exit(2)
'''


def _good_http(sha: str = SHA, version: str = VERSION) -> dict:
    return {
        "/api/health": {"status": 200, "body": {"status": "ok", "service": "aa-tripplanner-web", "version": version,
                                                "git_sha": sha}},
        "/api/browse?resource=countries": {"status": 200, "body": {"countries": [{"code": "VN"}]}},
        "/api/trip?resource=coverage": {"status": 404, "body": {"error": "not found"}},
        "/api/trip": {"status": 200, "body": {"itinerary": [], "custom_days": []}},
    }


def _good_aws() -> dict:
    return {
        "functions": {fn: {"FunctionName": fn, "FunctionArn": "arn:aws:lambda:us-west-1:123456789012:function:" + fn,
                           "State": "Active", "LastUpdateStatus": "Successful", "CodeSha256": CODE}
                      for fn in FUNCS},
        "objects": {key: {"ContentLength": 1, "Metadata": {"git-sha": SHA, "app-version": VERSION,
                                                           "code-sha256": CODE}}
                    for key in FUNCS.values()},
    }


@unittest.skipUnless(HAVE_TOOLS, "needs bash and jq")
class VerifyScriptTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        tmp = Path(self.tmp.name)
        self.bin = tmp / "bin"
        self.bin.mkdir()
        for name, body in (("curl", FAKE_CURL), ("aws", FAKE_AWS), ("sleep", "#!/usr/bin/env bash\nexit 0\n")):
            path = self.bin / name
            path.write_text(body)
            path.chmod(path.stat().st_mode | stat.S_IXUSR)
        self.http_file = tmp / "http.json"
        self.aws_file = tmp / "aws.json"
        self.urls = tmp / "urls"
        self.urls.write_text("")
        self.summary = tmp / "summary.md"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_verify(self, app: str, *, http: dict | None = None, aws: dict | None = None, drop: tuple = (),
                   **over: str) -> subprocess.CompletedProcess[str]:
        self.http_file.write_text(json.dumps(http if http is not None else _good_http()))
        self.aws_file.write_text(json.dumps(aws if aws is not None else _good_aws()))
        env = {
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "FAKE_HTTP": str(self.http_file),
            "FAKE_AWS": str(self.aws_file),
            "FAKE_URLS": str(self.urls),
            "FAKE_BUCKET": "fake-artifacts-bucket",
            "EXPECTED_SHA": SHA,
            "EXPECTED_VERSION": VERSION,
            "BASE": "https://prod.example.invalid",
            "AWS_REGION": "us-west-1",
            "ARTIFACTS_BUCKET": "fake-artifacts-bucket",
            "VERIFY_TRIES": "2",
            "VERIFY_INITIAL_WAIT": "0",
            "GITHUB_STEP_SUMMARY": str(self.summary),
        }
        env.update(over)
        for name in drop:
            env.pop(name, None)
        return subprocess.run(["bash", str(SCRIPT), app], env=env, capture_output=True, text=True, timeout=120)

    def output(self, result: subprocess.CompletedProcess[str]) -> str:
        summary = self.summary.read_text() if self.summary.exists() else ""
        return result.stdout + result.stderr + summary

    def assert_red(self, result: subprocess.CompletedProcess[str], needle: str) -> None:
        self.assertEqual(result.returncode, 1, self.output(result))
        self.assertIn("**FAIL**", self.summary.read_text())
        self.assertIn(needle, self.output(result))

    # --- web ----------------------------------------------------------------------------------------------------

    def test_web_green_on_the_expected_commit_and_version(self) -> None:
        result = self.run_verify("web")
        self.assertEqual(result.returncode, 0, self.output(result))
        summary = self.summary.read_text()
        self.assertIn("TripPlanner web post-deploy verify: PASS", summary)
        self.assertIn(SHA, summary)
        self.assertIn("| health | PASS |", summary)

    def test_web_cache_buster_is_filled_in(self) -> None:
        self.run_verify("web")
        urls = self.urls.read_text().splitlines()
        self.assertEqual(len(urls), 1)
        self.assertTrue(urls[0].startswith("https://prod.example.invalid/api/health?_cb="), urls)
        self.assertNotIn("{cb}", urls[0])

    def test_web_red_on_a_stale_git_sha_after_every_try(self) -> None:
        result = self.run_verify("web", http=_good_http(sha=OLD_SHA))
        self.assert_red(result, "assertion is false")
        self.assertEqual(len(self.urls.read_text().splitlines()), 2)  # VERIFY_TRIES

    def test_web_red_on_a_version_mismatch(self) -> None:
        self.assert_red(self.run_verify("web", http=_good_http(version="0.8.1")), "assertion is false")

    def test_web_red_on_http_500(self) -> None:
        http = _good_http()
        http["/api/health"] = {"status": 500, "body": {"error": "boom"}}
        self.assert_red(self.run_verify("web", http=http), "HTTP 500, expected 200")

    def test_web_needs_no_aws(self) -> None:
        (self.bin / "aws").unlink()
        result = self.run_verify("web", drop=("AWS_REGION", "ARTIFACTS_BUCKET"))
        self.assertEqual(result.returncode, 0, self.output(result))

    def test_missing_expected_sha_is_red_and_nothing_runs(self) -> None:
        result = self.run_verify("web", drop=("EXPECTED_SHA",))
        self.assert_red(result, "not set: EXPECTED_SHA")
        self.assertEqual(self.urls.read_text(), "")

    def test_invalid_expected_sha_is_never_echoed(self) -> None:
        result = self.run_verify("web", EXPECTED_SHA="not-a-sha-SENTINEL")
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("SENTINEL", self.output(result))
        self.assertIn("Expected commit `invalid`", self.summary.read_text())
        self.assertEqual(self.urls.read_text(), "")  # nothing ran with it

    def test_invalid_expected_version_is_red(self) -> None:
        self.assert_red(self.run_verify("web", EXPECTED_VERSION="latest"), "EXPECTED_VERSION must be X.Y.Z")

    def test_unknown_app_has_no_rows_and_is_red(self) -> None:
        self.assert_red(self.run_verify("nope"), "rows file not found")

    def test_bad_app_name_exits_2_without_echo(self) -> None:
        result = self.run_verify("BAD;APP-SENTINEL")
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("SENTINEL", result.stdout + result.stderr)

    # --- lambdas ------------------------------------------------------------------------------------------------

    def test_lambdas_green(self) -> None:
        result = self.run_verify("lambdas")
        self.assertEqual(result.returncode, 0, self.output(result))
        summary = self.summary.read_text()
        for row in ("lambda-browse", "lambda-assembly", "browse-countries", "assembly-coverage", "assembly-itinerary"):
            self.assertIn(f"| {row} | PASS |", summary)
        self.assertNotIn("fake-artifacts-bucket", self.output(result))  # the bucket name is never printed
        self.assertNotIn("function:", self.output(result))  # nor a function ARN

    def test_lambdas_browse_url_gets_a_cache_buster(self) -> None:
        self.run_verify("lambdas")
        browse = [u for u in self.urls.read_text().splitlines() if "/api/browse" in u]
        self.assertEqual(len(browse), 1)
        self.assertRegex(browse[0], r"resource=countries&_cb=[^&{}]+$")

    def test_lambda_update_not_successful_is_red(self) -> None:
        aws = _good_aws()
        aws["functions"]["aa-tripplanner-dev-assembly"]["LastUpdateStatus"] = "Failed"
        self.assert_red(self.run_verify("lambdas", aws=aws), "LastUpdateStatus=Failed")

    def test_lambda_inactive_is_red(self) -> None:
        aws = _good_aws()
        aws["functions"]["aa-tripplanner-dev-browse"]["State"] = "Pending"
        self.assert_red(self.run_verify("lambdas", aws=aws), "State=Pending")

    def test_lambda_running_other_code_is_red(self) -> None:
        aws = _good_aws()
        aws["functions"]["aa-tripplanner-dev-browse"]["CodeSha256"] = OTHER_CODE
        self.assert_red(self.run_verify("lambdas", aws=aws), "does not run artifact browse.zip")

    def test_artifact_from_another_commit_is_red(self) -> None:
        aws = _good_aws()
        aws["objects"]["assembly.zip"]["Metadata"]["git-sha"] = OLD_SHA
        self.assert_red(self.run_verify("lambdas", aws=aws), f"was built from {OLD_SHA[:12]}")

    def test_artifact_without_metadata_is_red(self) -> None:
        aws = _good_aws()
        aws["objects"]["browse.zip"]["Metadata"] = {}
        self.assert_red(self.run_verify("lambdas", aws=aws), "no git-sha metadata")

    def test_artifact_version_mismatch_is_red(self) -> None:
        aws = _good_aws()
        aws["objects"]["browse.zip"]["Metadata"]["app-version"] = "0.8.1"
        self.assert_red(self.run_verify("lambdas", aws=aws), "has version 0.8.1")

    def test_aws_error_text_is_scrubbed_everywhere(self) -> None:
        result = self.run_verify("lambdas", aws={"error": AWS_LEAK})
        self.assert_red(result, "get-function-configuration aa-tripplanner-dev-browse failed")
        out = self.output(result)
        self.assertNotIn("arn:aws", out)
        self.assertNotIn("123456789012", out)
        self.assertIn("<arn>", out)

    def test_uuid_tail_survives_the_scrub(self) -> None:
        result = self.run_verify("lambdas")
        self.assertIn(f"trip_id={ZERO} -> HTTP 404", result.stdout)
        self.assertNotIn("<acct>", result.stdout)

    def test_head_object_error_is_scrubbed(self) -> None:
        result = self.run_verify("lambdas", FAKE_BUCKET="aa-other-bucket-123456789012")
        self.assert_red(result, "head-object of artifact browse.zip failed")
        self.assertNotIn("123456789012", self.output(result))

    def test_browse_500_is_red(self) -> None:
        http = _good_http()
        http["/api/browse?resource=countries"] = {"status": 500, "body": {"error": "upstream"}}
        self.assert_red(self.run_verify("lambdas", http=http), "| browse-countries | **FAIL** |")

    def test_empty_country_list_is_red(self) -> None:
        http = _good_http()
        http["/api/browse?resource=countries"] = {"status": 200, "body": {"countries": []}}
        self.assert_red(self.run_verify("lambdas", http=http), "assertion is false")

    def test_lambdas_need_the_bucket(self) -> None:
        self.assert_red(self.run_verify("lambdas", drop=("ARTIFACTS_BUCKET",)), "not set: ARTIFACTS_BUCKET")

    def test_lambdas_need_aws(self) -> None:
        (self.bin / "aws").unlink()
        base = "/usr/bin:/bin"
        if any(Path(d, "aws").exists() for d in base.split(":")) or not Path("/usr/bin/jq").exists():
            self.skipTest("needs jq and no aws in /usr/bin:/bin")
        result = self.run_verify("lambdas", PATH=f"{self.bin}:{base}")
        self.assert_red(result, "aws is not installed")


@unittest.skipUnless(HAVE_TOOLS and shutil.which("git"), "needs bash, jq and git")
class ExpectedReleaseTest(unittest.TestCase):
    def run_script(self, sha: str, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
        env = {"PATH": os.environ["PATH"], "EXPECTED_SHA": sha}
        return subprocess.run(["bash", str(EXPECTED_RELEASE)], env=env, cwd=cwd, capture_output=True, text=True,
                              timeout=60)

    def test_reads_the_manifest_of_the_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            git = ["git", "-C", tmp, "-c", "user.email=t@example.invalid", "-c", "user.name=t"]
            subprocess.run(["git", "init", "-q", tmp], check=True)
            (repo / ".release-please-manifest.json").write_text('{".": "1.2.3"}\n')
            subprocess.run(git + ["add", "."], check=True)
            subprocess.run(git + ["commit", "-qm", "one"], check=True)
            first = subprocess.run(git + ["rev-parse", "HEAD"], check=True, capture_output=True,
                                   text=True).stdout.strip()
            (repo / ".release-please-manifest.json").write_text('{".": "1.3.0"}\n')
            subprocess.run(git + ["commit", "-qam", "two"], check=True)
            result = self.run_script(first, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("version=1.2.3", result.stdout)

    def test_invalid_sha_is_never_echoed(self) -> None:
        result = self.run_script("not-a-sha-SENTINEL")
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("SENTINEL", result.stdout + result.stderr)


def _job(text: str, name: str) -> str:
    match = re.search(rf"\n  {re.escape(name)}:\n(.*?)(?=\n  [A-Za-z0-9_-]+:\n|\Z)", text, re.S)
    assert match, f"job {name} not found"
    return match.group(1)


def _run_block(job: str) -> str:
    """The `run: |` script of the job's last step, dedented."""
    match = re.search(r"\n        run: \|\n((?:          .*\n|\s*\n)+)", job + "\n")
    assert match, "no run block"
    return textwrap.dedent(match.group(1))


class VerifyWorkflowTest(unittest.TestCase):
    def test_callable_and_dispatchable_with_expected_sha_and_app(self) -> None:
        on = VERIFY_WF.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
        self.assertEqual(re.findall(r"^  ([a-z_]+):", on, re.M), ["workflow_call", "workflow_dispatch"])
        for trigger in on.split("  workflow_dispatch:")[0], on.split("  workflow_dispatch:")[1]:
            self.assertIn("expected_sha:", trigger)
            self.assertIn("app:", trigger)
        self.assertIn("options: [web, lambdas]", on)

    def test_call_path_gets_a_concurrency_group_of_its_own_run(self) -> None:
        for job, app in (("web", "web"), ("lambdas", "lambdas")):
            block = _job(VERIFY_WF, job)
            self.assertIn(f"format('post-deploy-verify-tripplanner-{app}-deploy-{{0}}-{{1}}', github.run_id, "
                          "github.run_attempt)", block)
            self.assertIn(f"|| 'post-deploy-verify-tripplanner-{app}' }}}}", block)
            self.assertIn("cancel-in-progress: false", block)
        # Never one of the deploy groups.
        self.assertNotRegex(VERIFY_WF, r"group: *(deploy-lambdas|vercel-deploy)")

    def test_unknown_app_cannot_skip_both_jobs(self) -> None:
        self.assertIn("if: inputs.app != 'lambdas'", _job(VERIFY_WF, "web"))
        self.assertIn("if: inputs.app == 'lambdas'", _job(VERIFY_WF, "lambdas"))

    def test_oidc_only_in_the_lambdas_job_and_no_environment(self) -> None:
        web, lambdas = _job(VERIFY_WF, "web"), _job(VERIFY_WF, "lambdas")
        self.assertNotIn("id-token", web)
        self.assertNotIn("secrets.", web)
        self.assertIn("id-token: write", lambdas)
        # The deploy role trusts only `...:ref:refs/heads/main`; an environment would change the OIDC subject.
        self.assertNotRegex(VERIFY_WF, r"(?m)^\s+environment:")
        self.assertIn("mask-aws-account-id: true", lambdas)

    def test_both_jobs_run_the_script_and_read_the_expected_release(self) -> None:
        for job in ("web", "lambdas"):
            block = _job(VERIFY_WF, job)
            self.assertIn("bash .github/scripts/expected_release.sh", block)
            self.assertIn('bash .github/scripts/post_deploy_verify.sh "$APP"', block)
            self.assertIn("BASE: https://aa-tripplanner.vercel.app", block)
            # Inputs reach the scripts through env, never `${{ }}` inside `run:`.
            for run in re.findall(r"run: (.*)", block):
                self.assertNotIn("${{", run)

    def test_lambda_rows_name_the_deployed_functions(self) -> None:
        rows = (ROOT / ".github" / "verify" / "lambdas.rows").read_text()
        for env_name in ("BROWSE_FUNCTION", "ASSEMBLY_FUNCTION"):
            fn = re.search(rf"{env_name}: (\S+)", LAMBDAS_WF).group(1)
            self.assertRegex(rows, rf"\|lambda\|{re.escape(fn)} [a-z]+\.zip\|Successful\|")


class VercelDeployWorkflowTest(unittest.TestCase):
    def test_verify_is_production_only_on_the_deployed_sha(self) -> None:
        block = _job(VERCEL_WF, "verify")
        self.assertIn("needs: deploy", block)
        self.assertIn("if: needs.deploy.outputs.target == 'production'", block)
        self.assertIn("uses: ./.github/workflows/post-deploy-verify.yml", block)
        self.assertIn("app: web", block)
        self.assertIn("expected_sha: ${{ needs.deploy.outputs.sha }}", block)
        self.assertIn("from_deploy: true", block)
        self.assertNotIn("secrets: inherit", block)
        # deploy.outputs.sha is steps.plan.outputs.sha, the GIT_SHA the deploy passes to Vercel.
        self.assertIn("sha: ${{ steps.plan.outputs.sha }}", VERCEL_WF)
        self.assertIn('--env "GIT_SHA=$SHA"', VERCEL_WF)

    def result(self, needs: dict, event: str, target: str = "") -> subprocess.CompletedProcess[str]:
        job = _job(VERCEL_WF, "result")
        self.assertIn("if: always()", job)
        self.assertIn("needs: [deploy, verify]", job)
        env = {"PATH": os.environ["PATH"], "NEEDS": json.dumps(needs), "EVENT": event, "INPUT_TARGET": target}
        return subprocess.run(["bash", "-c", _run_block(job)], env=env, capture_output=True, text=True, timeout=60)

    @staticmethod
    def needs(deploy: str, verify: str) -> dict:
        return {"deploy": {"result": deploy, "outputs": {}}, "verify": {"result": verify, "outputs": {}}}

    @unittest.skipUnless(HAVE_TOOLS, "needs bash and jq")
    def test_production_green_only_when_deployed_and_verified(self) -> None:
        self.assertEqual(self.result(self.needs("success", "success"), "push").returncode, 0)
        self.assertEqual(self.result(self.needs("success", "success"), "workflow_dispatch", "production").returncode, 0)
        for deploy, verify in (("success", "skipped"), ("success", "failure"), ("failure", "skipped"),
                               ("skipped", "skipped"), ("success", "cancelled")):
            for event, target in (("push", ""), ("workflow_dispatch", "production")):
                with self.subTest(deploy=deploy, verify=verify, event=event):
                    r = self.result(self.needs(deploy, verify), event, target)
                    self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                    self.assertIn("is not deployed and verified", r.stdout)

    @unittest.skipUnless(HAVE_TOOLS, "needs bash and jq")
    def test_production_red_when_verify_result_is_missing(self) -> None:
        needs = self.needs("success", "success")
        del needs["verify"]
        self.assertEqual(self.result(needs, "push").returncode, 1)

    @unittest.skipUnless(HAVE_TOOLS, "needs bash and jq")
    def test_preview_behaviour_is_unchanged(self) -> None:
        self.assertEqual(self.result(self.needs("success", "skipped"), "pull_request").returncode, 0)
        self.assertEqual(self.result(self.needs("skipped", "skipped"), "pull_request").returncode, 0)  # Dependabot
        self.assertEqual(self.result(self.needs("success", "skipped"), "workflow_dispatch", "preview").returncode, 0)
        self.assertEqual(self.result(self.needs("failure", "skipped"), "pull_request").returncode, 1)


class DeployLambdasWorkflowTest(unittest.TestCase):
    def test_inline_smoke_moved_to_the_verify(self) -> None:
        deploy = _job(LAMBDAS_WF, "deploy")
        self.assertNotIn("Smoke test", deploy)
        self.assertNotIn("/api/browse", deploy)

    def test_upload_writes_the_metadata_the_verify_reads(self) -> None:
        deploy = _job(LAMBDAS_WF, "deploy")
        self.assertIn("openssl dgst -sha256 -binary", deploy)
        self.assertIn('{"git-sha": $s, "app-version": $v, "code-sha256": $c}', deploy)
        self.assertIn('--metadata "$meta"', deploy)
        self.assertIn('--arg s "$GITHUB_SHA"', deploy)

    def test_verify_runs_after_deploy_with_the_secret(self) -> None:
        block = _job(LAMBDAS_WF, "verify")
        self.assertIn("needs: deploy", block)
        self.assertNotIn("if:", block)
        self.assertIn("uses: ./.github/workflows/post-deploy-verify.yml", block)
        self.assertIn("app: lambdas", block)
        self.assertIn("expected_sha: ${{ github.sha }}", block)
        self.assertIn("from_deploy: true", block)
        self.assertIn("secrets: inherit", block)
        self.assertIn("id-token: write", block)

    def result(self, needs: dict) -> subprocess.CompletedProcess[str]:
        job = _job(LAMBDAS_WF, "result")
        self.assertIn("if: always()", job)
        self.assertIn("needs: [ci, deploy, verify]", job)
        env = {"PATH": os.environ["PATH"], "NEEDS": json.dumps(needs), "EVENT": "push"}
        return subprocess.run(["bash", "-c", _run_block(job)], env=env, capture_output=True, text=True, timeout=60)

    @unittest.skipUnless(HAVE_TOOLS, "needs bash and jq")
    def test_result_green_only_when_every_job_succeeded(self) -> None:
        ok = {j: {"result": "success", "outputs": {}} for j in ("ci", "deploy", "verify")}
        self.assertEqual(self.result(ok).returncode, 0)
        for over in ({"verify": "skipped"}, {"verify": "failure"}, {"deploy": "failure", "verify": "skipped"},
                     {"ci": "failure", "deploy": "skipped", "verify": "skipped"}, {"verify": "cancelled"}):
            needs = json.loads(json.dumps(ok))
            for job, res in over.items():
                needs[job]["result"] = res
            with self.subTest(**over):
                r = self.result(needs)
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                self.assertIn("::error::Deploy Lambdas (push) is not verified", r.stdout)
        del ok["verify"]
        self.assertEqual(self.result(ok).returncode, 1)


if __name__ == "__main__":
    unittest.main()
