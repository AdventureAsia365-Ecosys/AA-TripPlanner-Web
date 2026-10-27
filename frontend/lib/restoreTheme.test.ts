// AA-995 — the inline restore script (ui/restoreTheme.ts) sets the theme AND the motion gate before paint.
// Run against a fake document / window: no browser needed.
import { test } from "node:test";
import assert from "node:assert/strict";
import { RESTORE_THEME_SCRIPT } from "../ui/restoreTheme.ts";

type Env = { dark?: boolean; reduce?: boolean; storage?: Record<string, string> | "throws"; noMatchMedia?: boolean };

function run(env: Env) {
  const attrs: Record<string, string> = {};
  const listeners: (() => void)[] = [];
  const media = { reduce: !!env.reduce };
  const localStorage = {
    getItem(k: string) {
      if (env.storage === "throws") throw new Error("blocked");
      return env.storage?.[k] ?? null;
    },
  };
  const window = {
    matchMedia: env.noMatchMedia
      ? undefined
      : (q: string) => ({
          get matches() {
            return q.includes("color-scheme") ? !!env.dark : media.reduce;
          },
          addEventListener: (_: string, cb: () => void) => listeners.push(cb),
        }),
  };
  const document = { documentElement: { setAttribute: (k: string, v: string) => (attrs[k] = v) } };
  new Function("window", "document", "localStorage", RESTORE_THEME_SCRIPT)(window, document, localStorage);
  return { attrs, media, listeners };
}

test("motion is ok by default and the theme follows the OS", () => {
  assert.deepEqual(run({}).attrs, { "data-theme": "light", "data-motion": "ok" });
  assert.equal(run({ dark: true }).attrs["data-theme"], "dark");
});

test("prefers-reduced-motion sets data-motion=reduce, and a live change is followed", () => {
  assert.equal(run({ reduce: true }).attrs["data-motion"], "reduce");
  const r = run({});
  r.media.reduce = true;
  r.listeners.forEach((cb) => cb());
  assert.equal(r.attrs["data-motion"], "reduce");
});

test("a stored tp_motion=reduce wins over the OS, a stored theme wins over the OS", () => {
  const r = run({ dark: true, storage: { tp_motion: "reduce", tp_theme: "light" } });
  assert.deepEqual(r.attrs, { "data-theme": "light", "data-motion": "reduce" });
});

test("blocked storage still sets both attributes", () => {
  assert.deepEqual(run({ storage: "throws" }).attrs, { "data-theme": "light", "data-motion": "ok" });
});

test("no matchMedia at all: light theme, motion reduced (fail static)", () => {
  assert.deepEqual(run({ noMatchMedia: true }).attrs, { "data-theme": "light", "data-motion": "reduce" });
});
