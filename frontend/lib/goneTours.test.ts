// AA-986 L6: the proxy's 410 for retired tours (lib/goneTours.ts). Run: npm test.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  GONE_MAX_STALE_MS,
  GONE_RETRY_MS,
  GONE_TTL_MS,
  createGoneCache,
  gonePage,
  tourPathSuffix,
} from "./goneTours.ts";

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

// ── AA-987: a retired tour answers 410 within 2 minutes on every instance ─────────────────────────────────────────

test("the list refreshes every minute and is never older than two minutes when answered", () => {
  assert.equal(GONE_TTL_MS, 60_000);
  assert.equal(GONE_MAX_STALE_MS, 120_000);
  assert.ok(GONE_RETRY_MS <= GONE_MAX_STALE_MS);
});

test("check waits for the first load of an instance, then answers from memory", async () => {
  let calls = 0;
  const cache = createGoneCache(async () => {
    calls += 1;
    return ["abcdef12"];
  });
  assert.equal(await cache.check("/tours/old-trip-abcdef12", 0), true); // first request waited for the list
  assert.equal(await cache.check("/tours/live-trip-3f0c2a8e", 1), false);
  assert.equal(calls, 1);
});

test("a tour retired while an instance was idle is gone on its next request", async () => {
  const lists = [["aaaaaaaa"], ["aaaaaaaa", "abcdef12"]];
  let calls = 0;
  const cache = createGoneCache(async () => lists[Math.min(calls++, lists.length - 1)]);
  assert.equal(await cache.check("/tours/x-abcdef12", 0), false);
  // Idle for an hour, the tour was retired meanwhile: the list is too old, so this request waits for the refresh.
  assert.equal(await cache.check("/tours/x-abcdef12", 60 * 60 * 1000), true);
  assert.equal(calls, 2);
});

test("between one and two minutes old the request does not wait (background refresh)", async () => {
  let release: (v: string[]) => void = () => {};
  let calls = 0;
  const cache = createGoneCache((): Promise<string[]> => {
    calls += 1;
    if (calls === 1) return Promise.resolve(["aaaaaaaa"]);
    return new Promise<string[]>((resolve) => {
      release = resolve;
    });
  });
  await cache.check("/tours/x-abcdef12", 0);
  const t = GONE_TTL_MS + 1; // due, not too old
  assert.equal(await cache.check("/tours/x-abcdef12", t, 10_000), false); // answered at once from the old list
  assert.notEqual(cache.pending(), null);
  release(["abcdef12"]);
  await cache.settled();
  assert.equal(await cache.check("/tours/x-abcdef12", t + 1), true);
  assert.equal(calls, 2);
});

test("a slow or failing browse call bounds the wait and keeps the last list", async () => {
  let calls = 0;
  const cache = createGoneCache((): Promise<string[] | null> => {
    calls += 1;
    if (calls === 1) return Promise.resolve(["abcdef12"]);
    if (calls === 2) return new Promise<string[] | null>(() => {}); // never answers
    return Promise.resolve(null);
  });
  await cache.check("/tours/x-abcdef12", 0);
  const started = Date.now();
  assert.equal(await cache.check("/tours/x-abcdef12", GONE_MAX_STALE_MS, 20), true); // waited ≤ 20 ms, old list
  assert.ok(Date.now() - started < 1000);
  assert.equal(calls, 2);
});

test("after a failed refresh the next requests do not wait until the retry is due", async () => {
  let calls = 0;
  const cache = createGoneCache(async () => (calls++ === 0 ? ["abcdef12"] : null));
  await cache.check("/tours/x-abcdef12", 0);
  assert.equal(await cache.check("/tours/x-abcdef12", GONE_MAX_STALE_MS), true); // waited, failed, last list kept
  assert.equal(calls, 2);
  assert.equal(await cache.check("/tours/x-abcdef12", GONE_MAX_STALE_MS + 1), true);
  assert.equal(cache.pending(), null); // no new call before GONE_RETRY_MS
  assert.equal(calls, 2);
});

test("check on a path that is not a tour page reads nothing", async () => {
  let calls = 0;
  const cache = createGoneCache(async () => {
    calls += 1;
    return [];
  });
  assert.equal(await cache.check("/tours/no-suffix", 0), false);
  assert.equal(calls, 0);
});

test("the 410 page is not indexed and links home and to the planner", () => {
  const html = gonePage();
  assert.match(html, /<meta name="robots" content="noindex">/);
  assert.match(html, /no longer offered/);
  assert.match(html, /href="\/"/);
  assert.match(html, /href="\/plan"/);
});
