"use client";
// AA-995 (spec §3.6, WCAG 2.2.2) — the ribbon's client island: the pause toggle (44 px; a toggle button with
// a constant name "Pause moving photos" and aria-pressed, the ARIA toggle pattern — the icon switches between
// pause and play), the viewer's choice kept in localStorage
// tp_motion_paused for every ribbon (immersive graft), and an IntersectionObserver that pauses the track
// while it is off screen. It only sets data-paused / data-offscreen on the section; CSS does the rest.
// The button is shown only when the ribbon can move (CSS: html[data-motion="ok"] .tp-ribbon[data-animate]).
import { useEffect, useSyncExternalStore } from "react";
import { Icon } from "@/ui";

export const RIBBON_PAUSED_KEY = "tp_motion_paused";

const listeners = new Set<() => void>();
let memoryPaused = false;

function readPaused(): boolean {
  try {
    return window.localStorage.getItem(RIBBON_PAUSED_KEY) === "1";
  } catch {
    return memoryPaused;
  }
}

function setPaused(v: boolean): void {
  memoryPaused = v;
  try {
    if (v) window.localStorage.setItem(RIBBON_PAUSED_KEY, "1");
    else window.localStorage.removeItem(RIBBON_PAUSED_KEY);
  } catch {
    /* storage blocked: the in-memory value holds for this page view */
  }
  listeners.forEach((l) => l());
}

function subscribe(cb: () => void): () => void {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

export default function RibbonControl({ ribbonId }: { ribbonId: string }) {
  const paused = useSyncExternalStore(subscribe, readPaused, () => false);

  useEffect(() => {
    document.getElementById(ribbonId)?.toggleAttribute("data-paused", paused);
  }, [ribbonId, paused]);

  useEffect(() => {
    const section = document.getElementById(ribbonId);
    if (!section) return;
    const viewport = section.querySelector<HTMLElement>(".tp-ribbon-viewport");
    // Keyboard focus switches the track to the still row (CSS); bring the focused tile into view.
    const onFocus = (e: FocusEvent) => {
      const t = e.target as HTMLElement;
      requestAnimationFrame(() => t.scrollIntoView({ block: "nearest", inline: "nearest" }));
    };
    viewport?.addEventListener("focusin", onFocus);
    let io: IntersectionObserver | undefined;
    if ("IntersectionObserver" in window) {
      io = new IntersectionObserver(([e]) => section.toggleAttribute("data-offscreen", !e.isIntersecting), { threshold: 0.1 });
      io.observe(section);
    }
    return () => {
      viewport?.removeEventListener("focusin", onFocus);
      io?.disconnect();
    };
  }, [ribbonId]);

  return (
    <button
      type="button"
      aria-pressed={paused}
      aria-label="Pause moving photos"
      title={paused ? "Play moving photos" : "Pause moving photos"}
      onClick={() => setPaused(!paused)}
      className="tp-ribbon-pause h-11 w-11 shrink-0 items-center justify-center rounded-full border border-k-accent-border text-k-accent-deep transition-colors duration-fast ease-std hover:bg-k-accent-tint"
    >
      <Icon name={paused ? "play" : "pause"} className="h-5 w-5" />
    </button>
  );
}
