// AA-986 L6: the proxy's 410 for retired tours (lib/goneTours.ts). Run: npm test.
import { test } from "node:test";
import assert from "node:assert/strict";
import { GONE_RETRY_MS, GONE_TTL_MS, createGoneCache, gonePage, tourPathSuffix } from "./goneTours.ts";

test("only a tour page path with a source-id suffix has a suffix", () => {
  assert.equal(tourPathSuffix("/tours/hanoi-and-ha-long-bay-vietnam-3f0c2a8e"), "3f0c2a8e");
  assert.equal(tourPathSuffix("/tours/Hanoi-And-Ha-Long-Bay-Vietnam-3F0C2A8E"), "3f0c2a8e");
  assert.equal(tourPathSuffix("/tours/no-suffix"), null);
  assert.equal(tourPathSuffix("/tours/a/b-3f0c2a8e"), null);
  assert.equal(tourPathSuffix("/plan"), null);
  assert.equal(tourPathSuffix("/destinations/vietnam-3f0c2a8e"), null);
});

test("a retired suffix is gone, a live one is not; the list is read once per TTL", async () => {
  let calls = 0;
  const cache = createGoneCache(async () => {
    calls += 1;
    return ["abcdef12"];
  });
  assert.equal(cache.isRetired("/tours/old-trip-abcdef12", 0), false); // first request: empty list, refresh starts
  await cache.settled();
  assert.equal(cache.isRetired("/tours/old-trip-abcdef12", 1), true);
  assert.equal(cache.isRetired("/tours/live-trip-3f0c2a8e", 2), false);
  assert.equal(calls, 1);
  cache.isRetired("/tours/old-trip-abcdef12", GONE_TTL_MS);
  await cache.settled();
  assert.equal(calls, 2);
});

test("isRetired never waits for a slow fetcher, and starts one refresh at a time", async () => {
  let release: (v: string[]) => void = () => {};
  let calls = 0;
  const cache = createGoneCache(() => {
    calls += 1;
    return new Promise<string[]>((resolve) => {
      release = resolve;
    });
  });
  // Synchronous answers while the fetch is still pending: the request path never awaits the browse call.
  assert.equal(cache.isRetired("/tours/x-abcdef12", 0), false);
  assert.notEqual(cache.pending(), null);
  assert.equal(cache.isRetired("/tours/x-abcdef12", 5), false);
  assert.equal(calls, 1);
  release(["abcdef12"]);
  await cache.settled();
  assert.equal(cache.pending(), null);
  assert.equal(cache.isRetired("/tours/x-abcdef12", 10), true);
  assert.equal(calls, 1);
});

test("a path that is not a tour page never reads the list", async () => {
  let calls = 0;
  const cache = createGoneCache(async () => {
    calls += 1;
    return [];
  });
  assert.equal(cache.isRetired("/tours/no-suffix", 0), false);
  assert.equal(cache.pending(), null);
  assert.equal(calls, 0);
});

test("a failed refresh keeps the last list and retries after GONE_RETRY_MS", async () => {
  const answers: (string[] | null)[] = [["abcdef12"], null, ["bcdef123"]];
  let calls = 0;
  const cache = createGoneCache(async () => answers[calls++] ?? null);
  cache.isRetired("/tours/x-abcdef12", 0);
  await cache.settled();
  assert.equal(cache.isRetired("/tours/x-abcdef12", 1), true);
  cache.isRetired("/tours/x-abcdef12", GONE_TTL_MS);
  await cache.settled();
  assert.equal(cache.isRetired("/tours/x-abcdef12", GONE_TTL_MS), true); // refresh failed: last list kept
  assert.equal(calls, 2);
  assert.equal(cache.isRetired("/tours/x-abcdef12", GONE_TTL_MS + 1), true); // no retry yet
  assert.equal(cache.pending(), null);
  assert.equal(calls, 2);
  cache.isRetired("/tours/x-abcdef12", GONE_TTL_MS + GONE_RETRY_MS);
  await cache.settled();
  assert.equal(cache.isRetired("/tours/x-abcdef12", GONE_TTL_MS + GONE_RETRY_MS), false);
  assert.equal(cache.isRetired("/tours/x-bcdef123", GONE_TTL_MS + GONE_RETRY_MS), true);
  assert.equal(calls, 3);
});

test("no list at all (browse unreachable) or a throwing fetcher answers not gone", async () => {
  const cache = createGoneCache(async () => null);
  assert.equal(cache.isRetired("/tours/x-abcdef12", 0), false);
  await cache.settled();
  assert.equal(cache.isRetired("/tours/x-abcdef12", 1), false);
  const throwing = createGoneCache(async () => {
    throw new Error("boom");
  });
  assert.equal(throwing.isRetired("/tours/x-abcdef12", 0), false);
  await throwing.settled(); // no unhandled rejection
  assert.equal(throwing.isRetired("/tours/x-abcdef12", 1), false);
});

test("the 410 page is not indexed and links home and to the planner", () => {
  const html = gonePage();
  assert.match(html, /<meta name="robots" content="noindex">/);
  assert.match(html, /no longer offered/);
  assert.match(html, /href="\/"/);
  assert.match(html, /href="\/plan"/);
});
