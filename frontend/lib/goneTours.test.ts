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
  assert.equal(await cache.isRetired("/tours/old-trip-abcdef12", 0), true);
  assert.equal(await cache.isRetired("/tours/live-trip-3f0c2a8e", 1), false);
  assert.equal(calls, 1);
  await cache.isRetired("/tours/old-trip-abcdef12", GONE_TTL_MS);
  assert.equal(calls, 2);
});

test("a path that is not a tour page never reads the list", async () => {
  let calls = 0;
  const cache = createGoneCache(async () => {
    calls += 1;
    return [];
  });
  assert.equal(await cache.isRetired("/tours/no-suffix", 0), false);
  assert.equal(calls, 0);
});

test("a failed refresh keeps the last list and retries after GONE_RETRY_MS", async () => {
  const answers: (string[] | null)[] = [["abcdef12"], null, ["bcdef123"]];
  let calls = 0;
  const cache = createGoneCache(async () => answers[calls++] ?? null);
  assert.equal(await cache.isRetired("/tours/x-abcdef12", 0), true);
  assert.equal(await cache.isRetired("/tours/x-abcdef12", GONE_TTL_MS), true); // refresh failed: last list kept
  assert.equal(calls, 2);
  assert.equal(await cache.isRetired("/tours/x-abcdef12", GONE_TTL_MS + 1), true); // no retry yet
  assert.equal(calls, 2);
  assert.equal(await cache.isRetired("/tours/x-abcdef12", GONE_TTL_MS + GONE_RETRY_MS), false);
  assert.equal(await cache.isRetired("/tours/x-bcdef123", GONE_TTL_MS + GONE_RETRY_MS), true);
  assert.equal(calls, 3);
});

test("no list at all (browse unreachable) answers not gone", async () => {
  const cache = createGoneCache(async () => null);
  assert.equal(await cache.isRetired("/tours/x-abcdef12", 0), false);
});

test("the 410 page is not indexed and links home and to the planner", () => {
  const html = gonePage();
  assert.match(html, /<meta name="robots" content="noindex">/);
  assert.match(html, /no longer offered/);
  assert.match(html, /href="\/"/);
  assert.match(html, /href="\/plan"/);
});
