// AA-833: one throttled browse call (429/502/503) gets a single short retry; other errors do not.
// Run: npm test (node --test with type stripping; no extra dependencies).
import { afterEach, beforeEach, test } from "node:test";
import assert from "node:assert/strict";
import { browseGet } from "./catalog.ts";

type Call = { url: string; headers: Record<string, string> };
const realFetch = globalThis.fetch;
let calls: Call[] = [];

// Each queued item is a status code (JSON body {n: index}) or an Error to throw.
function queue(...replies: (number | Error)[]) {
  calls = [];
  globalThis.fetch = (async (url: string, init?: { headers?: Record<string, string> }) => {
    calls.push({ url, headers: { ...(init?.headers ?? {}) } });
    const r = replies.shift();
    if (r === undefined) throw new Error("unexpected extra fetch");
    if (r instanceof Error) throw r;
    return new Response(JSON.stringify({ n: calls.length }), { status: r });
  }) as typeof fetch;
}

beforeEach(() => {
  process.env.BROWSE_API_URL = "https://browse.test";
});

afterEach(() => {
  globalThis.fetch = realFetch;
});

test("a 200 is returned without a retry", async () => {
  queue(200);
  const res = await browseGet<{ n: number }>("/browse/catalog/tours", 0);
  assert.deepEqual(res, { ok: true, data: { n: 1 } });
  assert.equal(calls.length, 1);
});

for (const status of [429, 502, 503]) {
  test(`a ${status} is retried once and the retry's result is returned`, async () => {
    queue(status, 200);
    const res = await browseGet<{ n: number }>("/browse/catalog/destinations/vietnam", 0);
    assert.deepEqual(res, { ok: true, data: { n: 2 } });
    assert.equal(calls.length, 2);
    assert.equal(calls[1].url, "https://browse.test/browse/catalog/destinations/vietnam");
    // The retry must not share the first call's fetch memoisation key.
    assert.equal(calls[0].headers["x-browse-retry"], undefined);
    assert.equal(calls[1].headers["x-browse-retry"], "1");
  });
}

test("a network error is retried like a 502", async () => {
  queue(new Error("socket hang up"), 200);
  const res = await browseGet("/browse/catalog/sitemap", 0);
  assert.equal(res.ok, true);
  assert.equal(calls.length, 2);
});

test("only one retry: a second throttle is returned as the error", async () => {
  queue(503, 503);
  const res = await browseGet("/browse/catalog/tours", 0);
  assert.deepEqual(res, { ok: false, status: 503, error: "browse 503" });
  assert.equal(calls.length, 2);
});

for (const status of [400, 403, 404, 500]) {
  test(`a ${status} is returned at once`, async () => {
    queue(status);
    const res = await browseGet("/browse/catalog/tours/nope", 0);
    assert.deepEqual(res, { ok: false, status, error: `browse ${status}` });
    assert.equal(calls.length, 1);
  });
}

test("a missing BROWSE_API_URL is not retried", async () => {
  delete process.env.BROWSE_API_URL;
  queue();
  const res = await browseGet("/browse/catalog/tours", 0);
  assert.equal(res.ok, false);
  assert.equal(calls.length, 0);
});
