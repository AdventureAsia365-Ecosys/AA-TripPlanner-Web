// AA-812 hotfix (D1): the planner map must never fan out more than a few tile requests per view.
// Run: npm test (node --test with type stripping; no extra dependencies).
import { test } from "node:test";
import assert from "node:assert/strict";
import { MAX_TILES, TileLoader, planTiles, tileId, type Bounds } from "./tileLoader.ts";

// The view that caused the incident: /plan at 1440x900 (map 1040x844 px), zoom 3, center 105,15.
const WORLD_Z3: Bounds = { west: 59.3, south: -21.4, east: 150.7, north: 46.2 };
// Zoom 0-1 with world copies on both sides.
const WHOLE_WORLD: Bounds = { west: -540, south: -85, east: 540, north: 85 };
// A city view (Hanoi).
const CITY: Bounds = { west: 105.6, south: 20.8, east: 106.1, north: 21.2 };
// Crossing the antimeridian (Fiji / Pacific).
const PACIFIC: Bounds = { west: 170.5, south: -20.2, east: 190.5, north: -10.1 };

function parse(id: string) {
  const [lat, lng, size = "1"] = id.split("_");
  return { lat: Number(lat), lng: Number(lng), size: Number(size) };
}

function covers(ids: string[], lat: number, lng: number) {
  return ids.some((id) => {
    const t = parse(id);
    return lat >= t.lat && lat < t.lat + t.size && lng >= t.lng && lng < t.lng + t.size;
  });
}

test("a world view at zoom 3 needs at most MAX_TILES requests (was ~6,350)", () => {
  const plan = planTiles(WORLD_Z3);
  assert.ok(plan.ids.length <= MAX_TILES, `${plan.ids.length} tiles`);
  assert.ok(plan.size >= 10);
  // The whole view is still covered: corners and centre.
  for (const [lat, lng] of [[-21.4, 59.3], [46.1, 150.6], [15, 105]]) {
    assert.ok(covers(plan.ids, lat, lng), `${lat},${lng} not covered`);
  }
});

test("any view stays bounded, including world copies", () => {
  for (const b of [WORLD_Z3, WHOLE_WORLD, CITY, PACIFIC]) {
    const plan = planTiles(b);
    assert.ok(plan.ids.length > 0 && plan.ids.length <= MAX_TILES, JSON.stringify(plan));
    for (const id of plan.ids) {
      const t = parse(id);
      assert.ok(t.lat % t.size === 0 && t.lng % t.size === 0, `${id} off the grid`);
      assert.ok(!id.includes("-0_") && !id.endsWith("-0"), `${id} has a negative zero`);
      assert.ok(t.lat >= -90 && t.lat + t.size <= 90, id);
      assert.ok(t.lng >= -180 && t.lng + t.size <= 180, id);
    }
  }
});

test("a city view keeps the historic 1-degree ids (same CDN keys as before)", () => {
  const plan = planTiles(CITY);
  assert.equal(plan.size, 1);
  assert.deepEqual(plan.ids.sort(), ["20_105", "20_106", "21_105", "21_106"].sort());
  assert.equal(tileId(20, 105, 1), "20_105");
  assert.equal(tileId(0, 90, 30), "0_90_30");
});

test("the antimeridian view wraps instead of asking for lng >= 180", () => {
  const plan = planTiles(PACIFIC);
  assert.ok(covers(plan.ids, -15, 175));
  assert.ok(covers(plan.ids, -15, -175)); // 185 E == 175 W
});

function fakeFetcher(log: string[], opts: { fail?: Set<string>; delay?: number } = {}) {
  let inFlight = 0;
  let maxInFlight = 0;
  const fetcher = async (id: string, signal: AbortSignal) => {
    log.push(id);
    inFlight++;
    maxInFlight = Math.max(maxInFlight, inFlight);
    try {
      await new Promise((r) => setTimeout(r, opts.delay ?? 1));
      if (signal.aborted) throw new Error("aborted");
      if (opts.fail?.has(id)) throw new TypeError("Failed to fetch");
      return [{ id: `pin-${id}` }];
    } finally {
      inFlight--;
    }
  };
  return { fetcher, maxInFlight: () => maxInFlight };
}

test("loader caps concurrency, caches, and a failed tile does not drop the others", async () => {
  const log: string[] = [];
  const f = fakeFetcher(log, { fail: new Set(["0_90_30"]) });
  const loader = new TileLoader(f.fetcher, { concurrency: 4 });
  const plan = planTiles(WORLD_Z3);
  const pins = await loader.load(plan.ids, "");
  assert.ok(pins);
  assert.ok(f.maxInFlight() <= 4, `max in flight ${f.maxInFlight()}`);
  assert.equal(log.length, plan.ids.length);
  assert.equal(pins.length, plan.ids.filter((id) => id !== "0_90_30").length);

  // Same view again (e.g. a moveend with no real move, or a re-render): only the failed tile
  // is retried, everything else comes from the cache.
  await loader.load(plan.ids, "");
  assert.deepEqual(log.slice(plan.ids.length), plan.ids.includes("0_90_30") ? ["0_90_30"] : []);

  // A different filter set is a different cache scope.
  await loader.load(plan.ids, "activity=trekking");
  assert.ok(log.length > plan.ids.length + 1);
});

test("a newer view aborts the previous one", async () => {
  const log: string[] = [];
  const f = fakeFetcher(log, { delay: 20 });
  const loader = new TileLoader(f.fetcher, { concurrency: 2 });
  const first = loader.load(planTiles(WORLD_Z3).ids);
  const second = loader.load(planTiles(CITY).ids);
  assert.equal(await first, null);
  const pins = await second;
  assert.equal(pins?.length, 4);
  // The first view started at most `concurrency` requests before it was superseded.
  assert.ok(log.length <= 2 + 4, `${log.length} requests`);
});

test("loop guard: a refresh loop cannot exceed the request budget", async () => {
  const log: string[] = [];
  const f = fakeFetcher(log);
  let exceeded = 0;
  const loader = new TileLoader(f.fetcher, {
    budget: 20,
    budgetWindowMs: 10_000,
    now: () => 1_000, // frozen clock: everything happens inside one window
    onBudgetExceeded: () => exceeded++,
  });
  // 50 refreshes over 50 different filter scopes (worst case: nothing is cached).
  for (let i = 0; i < 50; i++) await loader.load(planTiles(WORLD_Z3).ids, `scope-${i}`);
  assert.equal(log.length, 20);
  assert.ok(exceeded > 0);
});
