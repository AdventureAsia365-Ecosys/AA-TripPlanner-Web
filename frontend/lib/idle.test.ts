// AA-1041 (first-load performance) — lib/idle.ts runs deferred work after a paint, when the main thread is idle.
// Run: npm test.
import { test } from "node:test";
import assert from "node:assert/strict";
import { IDLE_TIMEOUT_MS, whenIdle, type IdleHost } from "./idle.ts";

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
