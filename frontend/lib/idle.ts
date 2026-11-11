// AA-1041 (first-load performance) — run work after the page has painted and the main thread is free. The planner
// starts its heaviest work (fetching and building the Mapbox map) this way, so the first paint of the search box and the
// trip panel, and their first answer to a tap, never wait behind it.
//
// requestIdleCallback where the browser has it (with a timeout, so a busy page still gets its map), else the next frame
// plus a macrotask: after one paint, never inside the current task. DOM-free apart from the two globals it is given.

export interface IdleHost {
  requestIdleCallback?: (cb: () => void, opts?: { timeout: number }) => number;
  cancelIdleCallback?: (id: number) => void;
  requestAnimationFrame: (cb: () => void) => number;
  cancelAnimationFrame: (id: number) => void;
  setTimeout: (cb: () => void, ms: number) => unknown;
  clearTimeout: (id: unknown) => void;
}

/** Longest wait for an idle moment before the work runs anyway (ms). */
export const IDLE_TIMEOUT_MS = 1500;

/** Runs `fn` once, after a paint, when the main thread is idle. Returns a cancel function (safe to call twice). */
export function whenIdle(fn: () => void, host: IdleHost = globalThis as unknown as IdleHost, timeout = IDLE_TIMEOUT_MS): () => void {
  let done = false;
  const run = () => {
    if (done) return;
    done = true;
    fn();
  };
  if (typeof host.requestIdleCallback === "function") {
    const id = host.requestIdleCallback(run, { timeout });
    return () => {
      done = true;
      host.cancelIdleCallback?.(id);
    };
  }
  let timer: unknown = null;
  const frame = host.requestAnimationFrame(() => {
    timer = host.setTimeout(run, 0);
  });
  return () => {
    done = true;
    host.cancelAnimationFrame(frame);
    if (timer !== null) host.clearTimeout(timer);
  };
}
