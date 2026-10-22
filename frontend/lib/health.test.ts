// AA-891: GET /api/health reports the release version next to the git SHA. Run: npm test.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { healthBody } from "./health.ts";

const manifestVersion: string = JSON.parse(
  readFileSync(new URL("../../.release-please-manifest.json", import.meta.url), "utf8"),
)["."];

test("health carries the version and the git sha", () => {
  const body = healthBody({ APP_VERSION: manifestVersion, GIT_SHA: "abc123" });
  assert.deepEqual(body, {
    status: "ok",
    service: "aa-tripplanner-web",
    version: manifestVersion,
    git_sha: "abc123",
  });
  assert.match(body.version, /^\d+\.\d+\.\d+$/);
});

test("the deploy sets APP_VERSION from the release-please manifest", () => {
  const wf = readFileSync(new URL("../../.github/workflows/vercel-deploy.yml", import.meta.url), "utf8");
  assert.match(wf, /APP_VERSION=\$\(jq -r '\."\."' \.release-please-manifest\.json\)/);
  assert.match(wf, /--env "APP_VERSION=\$APP_VERSION"/);
  assert.match(wf, /--env "GIT_SHA=\$SHA"/);
});

test("missing values read unknown; VERCEL_GIT_COMMIT_SHA is the sha fallback", () => {
  assert.deepEqual(healthBody({}), {
    status: "ok",
    service: "aa-tripplanner-web",
    version: "unknown",
    git_sha: "unknown",
  });
  assert.equal(healthBody({ VERCEL_GIT_COMMIT_SHA: "def456", GIT_SHA: " " }).git_sha, "def456");
});
