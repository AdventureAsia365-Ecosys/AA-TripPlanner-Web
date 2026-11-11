// AA-1041 (R7 item 6) — the share view prints in the light theme (lib/printTheme.ts).
import { test } from "node:test";
import assert from "node:assert/strict";
import { PRINT_THEME_ATTR, printInLightTheme, type PrintWindow } from "./printTheme.ts";

function fakeWindow(theme: string | null) {
  const attrs = new Map<string, string>();
  if (theme) attrs.set("data-theme", theme);
  const listeners = new Map<string, Set<() => void>>();
  const mqListeners = new Set<() => void>();
  const mq = {
    matches: false,
    addEventListener: (_t: "change", fn: () => void) => mqListeners.add(fn),
    removeEventListener: (_t: "change", fn: () => void) => mqListeners.delete(fn),
  };
  const win: PrintWindow = {
    document: {
      documentElement: {
        getAttribute: (n) => attrs.get(n) ?? null,
        setAttribute: (n, v) => void attrs.set(n, v),
        removeAttribute: (n) => void attrs.delete(n),
      },
    },
    addEventListener: (t, fn) => {
      if (!listeners.has(t)) listeners.set(t, new Set());
      listeners.get(t)!.add(fn);
    },
    removeEventListener: (t, fn) => void listeners.get(t)?.delete(fn),
    matchMedia: () => mq,
  };
  return {
    win,
    attrs,
    fire: (t: string) => listeners.get(t)?.forEach((fn) => fn()),
    media: (matches: boolean) => {
      mq.matches = matches;
      mqListeners.forEach((fn) => fn());
    },
    listenerCount: () => [...listeners.values()].reduce((n, s) => n + s.size, 0) + mqListeners.size,
  };
}

test("dark theme: printing switches to light and puts dark back afterwards", () => {
  const f = fakeWindow("dark");
  printInLightTheme(f.win);
  f.fire("beforeprint");
  assert.equal(f.attrs.get("data-theme"), "light");
  assert.equal(f.attrs.get(PRINT_THEME_ATTR), "light");
  assert.ok(f.attrs.has("data-theme-switching"));
  f.fire("afterprint");
  assert.equal(f.attrs.get("data-theme"), "dark");
  assert.equal(f.attrs.has(PRINT_THEME_ATTR), false);
  assert.equal(f.attrs.has("data-theme-switching"), false);
});

test("the print media query alone (print emulation) also switches", () => {
  const f = fakeWindow("dark");
  printInLightTheme(f.win);
  f.media(true);
  assert.equal(f.attrs.get("data-theme"), "light");
  // beforeprint after the media change does not save "light" as the viewer's theme.
  f.fire("beforeprint");
  f.media(false);
  f.fire("afterprint");
  assert.equal(f.attrs.get("data-theme"), "dark");
});

test("light theme and no theme are left alone", () => {
  for (const theme of ["light", null]) {
    const f = fakeWindow(theme);
    printInLightTheme(f.win);
    f.fire("beforeprint");
    assert.equal(f.attrs.get("data-theme") ?? null, theme);
    assert.equal(f.attrs.has(PRINT_THEME_ATTR), false);
    f.fire("afterprint");
    assert.equal(f.attrs.get("data-theme") ?? null, theme);
  }
});

test("a theme changed while printing is not overwritten", () => {
  const f = fakeWindow("dark");
  printInLightTheme(f.win);
  f.fire("beforeprint");
  f.attrs.set("data-theme", "dark"); // another tab picked dark again
  f.fire("afterprint");
  assert.equal(f.attrs.get("data-theme"), "dark");
  assert.equal(f.attrs.has(PRINT_THEME_ATTR), false);
});

test("cleanup removes the listeners and restores the theme", () => {
  const f = fakeWindow("dark");
  const stop = printInLightTheme(f.win);
  f.fire("beforeprint");
  stop();
  assert.equal(f.attrs.get("data-theme"), "dark");
  assert.equal(f.listenerCount(), 0);
});
