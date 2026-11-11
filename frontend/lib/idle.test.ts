// AA-1041 (first-load performance) — lib/idle.ts runs deferred work after a paint, when the main thread is idle; and
// the planner keeps its heavy parts (the Mapbox map, the planning chat) off the first load. Run: npm test.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { IDLE_TIMEOUT_MS, whenIdle, type IdleHost } from "./idle.ts";

const read = (p: string) => readFileSync(new URL(p, import.meta.url), "utf8");

/** A host whose callbacks run only when the test says so. */
function fakeHost(withIdle: boolean) {
  const idle: { cb: () => void; timeout?: number; cancelled: boolean }[] = [];
  const frames: { cb: () => void; cancelled: boolean }[] = [];
  const timers: { cb: () => void; cancelled: boolean }[] = [];
  const host: IdleHost = {
    requestAnimationFrame: (cb) => frames.push({ cb, cancelled: false }) - 1,
    cancelAnimationFrame: (id) => {
      frames[id].cancelled = true;
    },
    setTimeout: (cb) => timers.push({ cb, cancelled: false }) - 1,
    clearTimeout: (id) => {
      timers[id as number].cancelled = true;
    },
  };
  if (withIdle) {
    host.requestIdleCallback = (cb, opts) => idle.push({ cb, timeout: opts?.timeout, cancelled: false }) - 1;
    host.cancelIdleCallback = (id) => {
      idle[id].cancelled = true;
    };
  }
  // Runs every queued callback not cancelled and not run yet (entries stay, so an id still names its entry).
  const flush = (q: { cb: () => void; cancelled: boolean; ran?: boolean }[]) => {
    for (const x of q) {
      if (x.cancelled || x.ran) continue;
      x.ran = true;
      x.cb();
    }
  };
  return { host, idle, frames, timers, flush };
}

test("with requestIdleCallback: runs once, when idle, with the timeout", () => {
  const f = fakeHost(true);
  let runs = 0;
  whenIdle(() => runs++, f.host);
  assert.equal(runs, 0, "never inside the calling task");
  assert.equal(f.idle.length, 1);
  assert.equal(f.idle[0].timeout, IDLE_TIMEOUT_MS);
  f.flush(f.idle);
  assert.equal(runs, 1);
});

test("with requestIdleCallback: cancel before idle means it never runs", () => {
  const f = fakeHost(true);
  let runs = 0;
  const cancel = whenIdle(() => runs++, f.host);
  cancel();
  cancel();
  f.flush(f.idle);
  assert.equal(runs, 0);
});

test("without requestIdleCallback: the next frame, then a macrotask (after one paint)", () => {
  const f = fakeHost(false);
  let runs = 0;
  whenIdle(() => runs++, f.host);
  assert.equal(runs, 0);
  assert.equal(f.timers.length, 0, "no timer before the frame");
  f.flush(f.frames);
  assert.equal(runs, 0, "not in the frame callback itself");
  f.flush(f.timers);
  assert.equal(runs, 1);
});

test("without requestIdleCallback: cancel before the frame or before the timer", () => {
  const a = fakeHost(false);
  let runs = 0;
  whenIdle(() => runs++, a.host)();
  a.flush(a.frames);
  a.flush(a.timers);
  const b = fakeHost(false);
  const cancel = whenIdle(() => runs++, b.host);
  b.flush(b.frames);
  cancel();
  b.flush(b.timers);
  assert.equal(runs, 0);
});

test("the planner loads the map and the chat lazily, off its first load", () => {
  const src = read("../app/plan/PlannerApp.tsx");
  // No static import of the two heavy parts (a static import puts them back in the planner's first chunk).
  assert.doesNotMatch(src, /^import\s+MapView\b/m);
  assert.doesNotMatch(src, /^import\s+PlanChat\b/m);
  assert.match(src, /dynamic\(\(\) => import\("@\/components\/MapView"\)/);
  assert.match(src, /dynamic\(\(\) => import\("@\/components\/PlanChat"\)/);
  // The map is asked for only after an idle moment (whenIdle), and the chat only once opened.
  assert.match(src, /whenIdle\(/);
  assert.match(src, /chatOpened && <PlanChat \/>/);
  // mapbox-gl is imported by the map component only, so no other planner module drags it into the first load.
  for (const f of ["../components/TripPanel.tsx", "../components/DayList.tsx", "../components/FilterChips.tsx", "../lib/useTrip.tsx"]) {
    assert.doesNotMatch(read(f), /from "mapbox-gl"/, f);
  }
});
