// AA-995 (spec §1.4) — the viewer's "Reduce motion" choice, the client side of the motion gate.
// The inline restore script (ui/restoreTheme.ts) sets <html data-motion="ok|reduce"> before paint from the OS
// setting (prefers-reduced-motion) and localStorage tp_motion; this module changes that choice after load (the
// footer toggle) and applies it to the same attribute, so every rule gated on html[data-motion="ok"] follows.
// No React here (unit-tested under node); components/site/MotionToggle.tsx wraps it in useSyncExternalStore.
import { MOTION_STORAGE_KEY } from "./restoreTheme.ts";

export type MotionGate = "ok" | "reduce";

// Same rule as the inline script: the OS setting or a stored "reduce" reduces motion.
export function resolveMotion(osReduce: boolean, stored: string | null): MotionGate {
  return osReduce || stored === "reduce" ? "reduce" : "ok";
}

type Win = {
  localStorage?: Pick<Storage, "getItem" | "setItem" | "removeItem">;
  matchMedia?: (q: string) => { matches: boolean };
  document: { documentElement: { setAttribute(name: string, value: string): void } };
};

const QUERY = "(prefers-reduced-motion: reduce)";
// The choice made in this page view, kept in memory so it still holds when storage is blocked.
let memo: boolean | null = null;
const listeners = new Set<() => void>();

function win(): Win | null {
  return typeof window === "undefined" ? null : (window as unknown as Win);
}

/** True when the OS asks for reduced motion. No matchMedia at all counts as "reduce" (the script fails static). */
export function osReducesMotion(w: Win | null = win()): boolean {
  if (!w) return false;
  try {
    return w.matchMedia ? w.matchMedia(QUERY).matches : true;
  } catch {
    return true;
  }
}

/** The viewer's own choice (this page view first, then localStorage; blocked storage = no choice). */
export function viewerReducesMotion(w: Win | null = win()): boolean {
  if (memo !== null) return memo;
  try {
    return w?.localStorage?.getItem(MOTION_STORAGE_KEY) === "reduce";
  } catch {
    return false;
  }
}

export function applyMotionGate(w: Win | null = win()): MotionGate {
  const gate = resolveMotion(osReducesMotion(w), viewerReducesMotion(w) ? "reduce" : null);
  w?.document.documentElement.setAttribute("data-motion", gate);
  return gate;
}

/** Store the choice (try/catch: storage may be blocked) and apply it to <html data-motion> right away. */
export function setViewerReducesMotion(on: boolean, w: Win | null = win()): void {
  memo = on;
  try {
    if (on) w?.localStorage?.setItem(MOTION_STORAGE_KEY, "reduce");
    else w?.localStorage?.removeItem(MOTION_STORAGE_KEY);
  } catch {
    /* storage blocked: the choice still holds for this page view */
  }
  applyMotionGate(w);
  listeners.forEach((l) => l());
}

/** For useSyncExternalStore: notified on a toggle and when the OS setting changes. */
export function subscribeMotion(cb: () => void): () => void {
  listeners.add(cb);
  const mq = typeof window !== "undefined" && window.matchMedia ? window.matchMedia(QUERY) : null;
  const onMedia = () => {
    applyMotionGate(); // keeps a memory-only choice (blocked storage) applied after the script's own listener
    cb();
  };
  mq?.addEventListener?.("change", onMedia);
  return () => {
    listeners.delete(cb);
    mq?.removeEventListener?.("change", onMedia);
  };
}

/** Snapshot as one primitive: "os" (the OS reduces, the toggle cannot undo it), "viewer" or "none". */
export function motionSnapshot(): "os" | "viewer" | "none" {
  if (osReducesMotion()) return "os";
  return viewerReducesMotion() ? "viewer" : "none";
}

/** Test hook: forget the in-memory choice. */
export function resetMotionMemo(): void {
  memo = null;
}
