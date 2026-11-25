// AA-1111 (S294 ZAP triage): the BFF's error shape (lib/apiError.ts), the HTML error reference (lib/errorReference.ts)
// and that every /api route answers through them. Fixture text is invented. Run: npm test.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import {
  ERROR_CODE_RE,
  INTERNAL_ERROR,
  UPSTREAM_ERROR,
  errorBody,
  errorLogLine,
  errorResponse,
  upstreamErrorBody,
  withApiErrors,
} from "./apiError.ts";
import { errorReference } from "./errorReference.ts";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const RID = "req-quillmere-1";
// What no error body may carry: exception text, a stack, an internal path, a framework or runtime name.
const LEAK_RE = /traceback|stack|\bat \w+ \(|\/var\/task|\/app\/|node_modules|\.py\b|\.tsx?\b|next\.js|asyncpg|python|internal server error|exception/i;

function headers(h: Record<string, string> = {}) {
  const m = new Headers(h);
  return { get: (k: string) => m.get(k) };
}

test("an upstream answer below 500 passes unchanged (the Lambdas' own codes and validation messages)", () => {
  for (const [status, text] of [[200, '{"ok":true}'], [400, '{"error": "ids required"}'], [404, '{"error": "tour not found"}'],
    [429, '{"error": "rate_limited", "retry_after": 30}']] as const) {
    assert.equal(upstreamErrorBody(status, text, RID), text);
  }
});

test("an upstream 5xx keeps only a short error code, adds the request id and drops everything else", () => {
  assert.deepEqual(JSON.parse(upstreamErrorBody(502, '{"error": "narration_failed", "detail": "KeyError: \'day\' at /var/task/x.py"}', RID)),
    { error: "narration_failed", request_id: RID });
  assert.deepEqual(JSON.parse(upstreamErrorBody(503, '{"error": "tenant tours unavailable"}', RID)),
    { error: "tenant tours unavailable", request_id: RID });
});

test("an upstream 5xx without a code of ours is upstream_error (API Gateway's own body, text, a long message)", () => {
  for (const text of ['{"message":"Internal Server Error"}', "Traceback (most recent call last):\n  File \"/var/task/h.py\"",
    "", "null", '{"error": "ValueError: invalid literal for int() with base 10: \'quill\'"}', `{"error": "${"x".repeat(60)}"}`,
    '{"error": {"nested": true}}']) {
    const out = upstreamErrorBody(500, text, RID);
    assert.deepEqual(JSON.parse(out), { error: UPSTREAM_ERROR, request_id: RID }, text);
    assert.doesNotMatch(out, LEAK_RE);
  }
});

test("errorBody never passes a code that is not one", () => {
  assert.deepEqual(JSON.parse(errorBody("Error: boom at foo.ts:3", RID)), { error: INTERNAL_ERROR, request_id: RID });
  assert.ok(ERROR_CODE_RE.test("not_configured"));
});

test("errorResponse: JSON, no-store, the request id header", async () => {
  const res = errorResponse("not_configured", 503, RID);
  assert.equal(res.status, 503);
  assert.equal(res.headers.get("content-type"), "application/json");
  assert.equal(res.headers.get("cache-control"), "no-store");
  assert.equal(res.headers.get("x-request-id"), RID);
  assert.deepEqual(await res.json(), { error: "not_configured", request_id: RID });
});

test("withApiErrors: a throw answers 500 internal with the request id and logs names only", async () => {
  const lines: string[] = [];
  const handler = withApiErrors("trip", async (_req: { headers: ReturnType<typeof headers> }) => {
    throw new TypeError("fetch failed: connect ECONNREFUSED 10.0.0.1:443 /var/task/secret_value");
  }, (line) => lines.push(line));
  const res = await handler({ headers: headers({ "x-request-id": RID }) });
  assert.equal(res.status, 500);
  assert.equal(res.headers.get("x-request-id"), RID);
  const text = await res.text();
  assert.deepEqual(JSON.parse(text), { error: "internal", request_id: RID });
  assert.doesNotMatch(text, LEAK_RE);
  assert.deepEqual(lines.map((l) => JSON.parse(l)), [{ evt: "bff_error", route: "trip", error: "TypeError", request_id: RID }]);
  assert.doesNotMatch(lines[0], /ECONNREFUSED|secret_value/);
});

test("withApiErrors: an answer passes unchanged; a thrown non-Error is logged as Error", async () => {
  const ok = withApiErrors("browse", async (_req: { headers: ReturnType<typeof headers> }) => new Response("{}", { status: 200 }));
  assert.equal((await ok({ headers: headers() })).status, 200);
  assert.equal(JSON.parse(errorLogLine("browse", "a string with C:\\path", RID)).error, "Error");
});

test("errorReference: a digest-shaped value only, never a message", () => {
  assert.equal(errorReference("1234567890"), "1234567890");
  assert.equal(errorReference("NEXT_HTTP_ERROR_FALLBACK-404"), "NEXT_HTTP_ERROR_FALLBACK-404");
  for (const bad of [undefined, null, 42, "", "Error: boom", "a b", "x".repeat(65), "<script>"]) {
    assert.equal(errorReference(bad), null, String(bad));
  }
});

// Every /api route handler answers through withApiErrors (or is the same handler under another path), and no route
// writes an env variable name or a raw upstream body into its answer any more.
function routeFiles(): string[] {
  return (readdirSync(join(ROOT, "app/api"), { recursive: true }) as string[])
    .map((f) => f.split("\\").join("/"))
    .filter((f) => f.endsWith("/route.ts"))
    .map((f) => `app/api/${f}`)
    .sort();
}

const DELEGATES = new Set(["app/api/browse/search/route.ts", "app/api/trip/chat/route.ts", "app/api/trip/narrate/route.ts",
  "app/api/trip/send/route.ts"]);
const NO_UPSTREAM = new Set(["app/api/health/route.ts"]);

test("every /api route answers errors through lib/apiError.ts", () => {
  const files = routeFiles();
  assert.ok(files.includes("app/api/trip/route.ts") && files.includes("app/api/browse/route.ts"));
  for (const f of files) {
    const src = readFileSync(join(ROOT, f), "utf8");
    assert.doesNotMatch(src, /not configured/, `${f}: answer errorResponse(NOT_CONFIGURED, ...)`);
    if (NO_UPSTREAM.has(f)) continue;
    if (DELEGATES.has(f)) {
      assert.match(src, /from "\.\.\/route"/, `${f}: delegates to a wrapped handler`);
      continue;
    }
    assert.doesNotMatch(src, /export async function (GET|POST|PUT|PATCH|DELETE)\b/, `${f}: wrap it in withApiErrors`);
    assert.match(src, /withApiErrors\(/, f);
    if (/await fetch\(/.test(src)) assert.match(src, /upstreamErrorBody\(/, `${f}: pass the upstream body through upstreamErrorBody`);
  }
});

test("the global error page exists and shows the reference, not the message", () => {
  const src = readFileSync(join(ROOT, "app/global-error.tsx"), "utf8");
  assert.match(src, /ErrorReference digest=\{error\.digest\}/);
  assert.doesNotMatch(src, /error\.(message|stack)/);
  for (const f of ["ui/RouteError.tsx", "app/[tenant]/error.tsx"]) {
    const page = readFileSync(join(ROOT, f), "utf8");
    assert.match(page, /<ErrorReference digest=\{error\.digest\}/, f);
    assert.doesNotMatch(page, /\{error\.(message|stack)\}/, f);
  }
});
