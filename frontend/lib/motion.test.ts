// AA-995 — the footer "Reduce motion" toggle (ui/motion.ts) drives the same gate as the inline restore script.
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { RESTORE_THEME_SCRIPT } from "../ui/restoreTheme.ts";
import {
  applyMotionGate,
  osReducesMotion,
  resetMotionMemo,
  resolveMotion,
  setViewerReducesMotion,
  viewerReducesMotion,
} from "../ui/motion.ts";

type Opts = { reduce?: boolean; storage?: Record<string, string>; blocked?: boolean; noMatchMedia?: boolean };

function fakeWindow(o: Opts = {}) {
  const attrs: Record<string, string> = {};
  const store: Record<string, string> = { ...(o.storage ?? {}) };
  const guard = () => {
    if (o.blocked) throw new Error("blocked");
  };
  const w = {
    attrs,
    store,
    localStorage: {
      getItem: (k: string) => (guard(), store[k] ?? null),
      setItem: (k: string, v: string) => (guard(), void (store[k] = v)),
      removeItem: (k: string) => (guard(), void delete store[k]),
    },
    matchMedia: o.noMatchMedia ? undefined : (q: string) => ({ matches: q.includes("reduced-motion") && !!o.reduce }),
    document: { documentElement: { setAttribute: (k: string, v: string) => void (attrs[k] = v) } },
  };
  return w;
}

// What the inline script decides for the same environment.
function scriptGate(o: Opts): string {
  const w = fakeWindow(o);
  const win = { matchMedia: w.matchMedia && ((q: string) => ({ ...w.matchMedia!(q), addEventListener() {} })) };
  new Function("window", "document", "localStorage", RESTORE_THEME_SCRIPT)(win, w.document, w.localStorage);
  return w.attrs["data-motion"];
}

beforeEach(() => resetMotionMemo());

test("resolveMotion: the OS or a stored 'reduce' reduces motion", () => {
  assert.equal(resolveMotion(false, null), "ok");
  assert.equal(resolveMotion(false, "ok"), "ok");
  assert.equal(resolveMotion(true, null), "reduce");
  assert.equal(resolveMotion(false, "reduce"), "reduce");
});

test("applyMotionGate agrees with the inline restore script in every case", () => {
  for (const reduce of [false, true])
    for (const storage of [{}, { tp_motion: "reduce" }, { tp_motion: "other" }] as Record<string, string>[])
      for (const noMatchMedia of [false, true]) {
        resetMotionMemo();
        const o = { reduce, storage, noMatchMedia };
        const w = fakeWindow(o);
        assert.equal(applyMotionGate(w), scriptGate(o), JSON.stringify(o));
        assert.equal(w.attrs["data-motion"], scriptGate(o));
      }
});

test("toggling on stores tp_motion=reduce and sets data-motion=reduce; off removes it", () => {
  const w = fakeWindow();
  setViewerReducesMotion(true, w);
  assert.equal(w.store.tp_motion, "reduce");
  assert.equal(w.attrs["data-motion"], "reduce");
  assert.equal(viewerReducesMotion(w), true);
  setViewerReducesMotion(false, w);
  assert.equal(w.store.tp_motion, undefined);
  assert.equal(w.attrs["data-motion"], "ok");
  assert.equal(viewerReducesMotion(w), false);
});

test("the OS setting still wins when the viewer turns the toggle off", () => {
  const w = fakeWindow({ reduce: true, storage: { tp_motion: "reduce" } });
  assert.equal(osReducesMotion(w), true);
  setViewerReducesMotion(false, w);
  assert.equal(w.attrs["data-motion"], "reduce");
});

test("blocked storage: the choice still applies for this page view", () => {
  const w = fakeWindow({ blocked: true });
  assert.equal(viewerReducesMotion(w), false);
  setViewerReducesMotion(true, w);
  assert.equal(w.attrs["data-motion"], "reduce");
  assert.equal(viewerReducesMotion(w), true);
});
