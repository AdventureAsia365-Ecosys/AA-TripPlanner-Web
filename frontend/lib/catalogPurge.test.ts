// AA-987: the CIS lifecycle event → TripPlanner purge path (lib/catalogPurge.ts). Run: npm test.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import {
  BROWSER_MAX_AGE_S,
  CATALOG_CACHE_TAG,
  MAX_BODY_BYTES,
  PURGE_PATHS,
  browseCacheHeaders,
  handlePurge,
  parseNotice,
  verifySignature,
  type PurgeDeps,
} from "./catalogPurge.ts";

// The cross-repo vector: AA-CIS-App tests/unit/test_aa987_tripplanner_purge.py signs this exact body with this
// secret and timestamp and pins the same signature (services/jobs/tripplanner_purge_job.sign).
const VECTOR_SECRET = "aa987-test-secret";
const VECTOR_TS = 1761200000;
const VECTOR_BODY =
  '{"contract":"catalog.tour.lifecycle.v1","events":[{"event":"retired","event_id":42,' +
  '"idempotency_key":"trash_master:t1:retired:abc","occurred_at":"2026-10-23T14:33:41+00:00",' +
  '"reason":"master_trashed","replaced_by_tour_id":null,"source_id":"3f0c2a8e-0000-4000-8000-' +
  '000000000001","source_system":"cis","tour_id":"00000000-0000-4000-8000-0000000000t1"}]}';
const VECTOR_SIGNATURE = "t=1761200000,v1=0ea3fd2093eca674137f8046bd84405dcd77280f4d7b2dad4ae8a52bff80d41a";

function sign(secret: string, body: string, ts: number): string {
  return `t=${ts},v1=${createHmac("sha256", secret).update(`${ts}.${body}`).digest("hex")}`;
}

function recorder(overrides: Partial<PurgeDeps> = {}) {
  const calls = { tags: [] as string[], paths: [] as [string, string | undefined][], cdn: [] as string[], logs: [] as string[] };
  const deps: PurgeDeps = {
    secret: VECTOR_SECRET,
    nowS: VECTOR_TS + 10,
    revalidateTag: (t) => void calls.tags.push(t),
    revalidatePath: (p, type) => void calls.paths.push([p, type]),
    deleteCdnTag: async (t) => void calls.cdn.push(t),
    log: (l) => void calls.logs.push(l),
    ...overrides,
  };
  return { deps, calls };
}

test("the CIS signature of the shared vector verifies", () => {
  assert.equal(sign(VECTOR_SECRET, VECTOR_BODY, VECTOR_TS), VECTOR_SIGNATURE);
  assert.deepEqual(verifySignature(VECTOR_SECRET, VECTOR_SIGNATURE, VECTOR_BODY, VECTOR_TS), { ok: true });
});

test("a wrong secret, a changed body, a stale or a malformed signature is refused", () => {
  assert.deepEqual(verifySignature("other", VECTOR_SIGNATURE, VECTOR_BODY, VECTOR_TS), { ok: false, reason: "mismatch" });
  assert.deepEqual(verifySignature(VECTOR_SECRET, VECTOR_SIGNATURE, VECTOR_BODY + " ", VECTOR_TS), {
    ok: false,
    reason: "mismatch",
  });
  assert.deepEqual(verifySignature(VECTOR_SECRET, VECTOR_SIGNATURE, VECTOR_BODY, VECTOR_TS + 301), {
    ok: false,
    reason: "stale",
  });
  assert.deepEqual(verifySignature(VECTOR_SECRET, VECTOR_SIGNATURE, VECTOR_BODY, VECTOR_TS - 301), {
    ok: false,
    reason: "stale",
  });
  assert.equal(verifySignature(VECTOR_SECRET, VECTOR_SIGNATURE, VECTOR_BODY, VECTOR_TS + 300).ok, true);
  assert.deepEqual(verifySignature(VECTOR_SECRET, null, VECTOR_BODY, VECTOR_TS), { ok: false, reason: "missing" });
  for (const bad of ["t=1761200000", "v1=abc", "t=x,v1=" + "0".repeat(64), "t=1761200000,v1=zz"]) {
    assert.deepEqual(verifySignature(VECTOR_SECRET, bad, VECTOR_BODY, VECTOR_TS), { ok: false, reason: "malformed" });
  }
  // A signature of another length never reaches timingSafeEqual with unequal buffers.
  assert.equal(verifySignature(VECTOR_SECRET, "t=1761200000,v1=" + "a".repeat(62), VECTOR_BODY, VECTOR_TS).ok, false);
});

test("the notice body is checked field by field", () => {
  const ok = parseNotice(VECTOR_BODY);
  assert.deepEqual(ok, {
    ok: true,
    events: [{ event_id: 42, source_id: "3f0c2a8e-0000-4000-8000-000000000001", event: "retired" }],
  });
  const ev = (e: Record<string, unknown>) => JSON.stringify({ contract: "catalog.tour.lifecycle.v1", events: [e] });
  assert.deepEqual(parseNotice("{"), { ok: false, error: "invalid_json" });
  assert.deepEqual(parseNotice(JSON.stringify({ contract: "x", events: [] })), { ok: false, error: "unknown_contract" });
  assert.deepEqual(parseNotice(JSON.stringify({ contract: "catalog.tour.lifecycle.v1", events: [] })), {
    ok: false,
    error: "invalid_events",
  });
  assert.equal(parseNotice(ev({ event_id: 0, source_id: "s", event: "retired" })).ok, false);
  assert.equal(parseNotice(ev({ event_id: 1.5, source_id: "s", event: "retired" })).ok, false);
  assert.equal(parseNotice(ev({ event_id: 1, source_id: "", event: "retired" })).ok, false);
  assert.equal(parseNotice(ev({ event_id: 1, source_id: "s", event: "deleted" })).ok, false);
  for (const kind of ["retired", "paused", "restored", "superseded"]) {
    assert.equal(parseNotice(ev({ event_id: 1, source_id: "s", event: kind })).ok, true);
  }
});

test("a valid notice purges the catalog tag, the page routes and the CDN tag, and logs ids only", async () => {
  const { deps, calls } = recorder();
  const answer = await handlePurge(VECTOR_SIGNATURE, VECTOR_BODY, deps);
  assert.equal(answer.status, 200);
  assert.equal(answer.body.ok, true);
  assert.deepEqual(answer.body.event_ids, [42]);
  assert.deepEqual(calls.tags, [CATALOG_CACHE_TAG]);
  assert.deepEqual(calls.paths, PURGE_PATHS.map(([p, t]) => [p, t]));
  assert.ok(calls.paths.some(([p]) => p === "/tours/[slug]") && calls.paths.some(([p]) => p === "/destinations/[country]"));
  assert.deepEqual(calls.cdn, [CATALOG_CACHE_TAG]);
  const line = JSON.parse(calls.logs[0]);
  assert.equal(line.evt, "catalog_purge");
  assert.equal(line.result, "ok");
  assert.deepEqual(line.kinds, ["retired"]);
  assert.ok(!calls.logs[0].includes(VECTOR_SECRET) && !calls.logs[0].includes("3f0c2a8e"));
});

test("a replay of the same notice gives the same answer and the same purges (idempotent)", async () => {
  const { deps, calls } = recorder();
  const first = await handlePurge(VECTOR_SIGNATURE, VECTOR_BODY, deps);
  const second = await handlePurge(VECTOR_SIGNATURE, VECTOR_BODY, deps);
  assert.deepEqual(first, second);
  assert.deepEqual(calls.tags, [CATALOG_CACHE_TAG, CATALOG_CACHE_TAG]);
  assert.deepEqual(calls.cdn, [CATALOG_CACHE_TAG, CATALOG_CACHE_TAG]);
});

test("refusals purge nothing", async () => {
  const cases: [string | null, string, Partial<PurgeDeps>, number][] = [
    [VECTOR_SIGNATURE, VECTOR_BODY, { secret: "" }, 503],
    [VECTOR_SIGNATURE, VECTOR_BODY, { secret: "wrong" }, 401],
    [null, VECTOR_BODY, {}, 401],
    [VECTOR_SIGNATURE, VECTOR_BODY, { nowS: VECTOR_TS + 3600 }, 401], // a captured notice replayed an hour later
    [sign(VECTOR_SECRET, "{}", VECTOR_TS), "{}", {}, 400],
    [VECTOR_SIGNATURE, "x".repeat(MAX_BODY_BYTES + 1), {}, 413],
  ];
  for (const [sig, body, over, status] of cases) {
    const { deps, calls } = recorder(over);
    const answer = await handlePurge(sig, body, deps);
    assert.equal(answer.status, status, JSON.stringify({ status, over }));
    assert.deepEqual([calls.tags, calls.paths, calls.cdn], [[], [], []]);
    assert.equal(calls.logs.length, 1);
  }
});

test("a failed purge answers 502 so CIS retries", async () => {
  const cdn = recorder({
    deleteCdnTag: async () => {
      throw new Error("purge api down");
    },
  });
  const a = await handlePurge(VECTOR_SIGNATURE, VECTOR_BODY, cdn.deps);
  assert.equal(a.status, 502);
  assert.equal(a.body.step, "cdn");
  const next = recorder({
    revalidateTag: () => {
      throw new Error("no store");
    },
  });
  const b = await handlePurge(VECTOR_SIGNATURE, VECTOR_BODY, next.deps);
  assert.equal(b.status, 502);
  assert.equal(b.body.step, "next");
  assert.deepEqual(next.calls.cdn, []);
});

test("a cacheable browse answer: CDN keeps the Lambda TTL under the tag, the browser at most a minute", () => {
  assert.deepEqual(browseCacheHeaders("public, max-age=1800"), {
    "cache-control": `public, max-age=${BROWSER_MAX_AGE_S}`,
    "vercel-cdn-cache-control": "public, max-age=1800",
    "vercel-cache-tag": CATALOG_CACHE_TAG,
  });
  assert.deepEqual(browseCacheHeaders("public, max-age=30"), {
    "cache-control": "public, max-age=30",
    "vercel-cdn-cache-control": "public, max-age=30",
    "vercel-cache-tag": CATALOG_CACHE_TAG,
  });
  for (const cc of ["no-store", "private, max-age=60", "public, max-age=0", "max-age=60", "public, no-cache, max-age=60"]) {
    assert.deepEqual(browseCacheHeaders(cc), { "cache-control": cc });
  }
  assert.deepEqual(browseCacheHeaders(null), { "cache-control": "no-store" });
});
