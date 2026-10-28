"use client";
// AA-995 (spec §1.4) — the optional footer "Reduce motion" toggle. It sets the same gate prefers-reduced-motion sets
// (<html data-motion="reduce">, ui/motion.ts), stored per viewer in localStorage tp_motion. A toggle button with a
// constant name and aria-pressed (as the ribbon's pause button). When the OS already asks for reduced motion the toggle
// shows pressed and disabled, with the reason next to it: the site cannot switch the OS setting off.
import { useSyncExternalStore } from "react";
import { motionSnapshot, setViewerReducesMotion, subscribeMotion } from "@/ui/motion";

export default function MotionToggle() {
  // Server snapshot "none": the button renders unpressed, then follows the real state after hydration.
  const state = useSyncExternalStore(subscribeMotion, motionSnapshot, () => "none" as const);
  const pressed = state !== "none";
  const byDevice = state === "os";
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
      <button
        type="button"
        aria-pressed={pressed}
        disabled={byDevice}
        aria-describedby={byDevice ? "motion-by-device" : undefined}
        onClick={() => setViewerReducesMotion(!pressed)}
        className="inline-flex min-h-11 items-center gap-2 rounded-pill border border-k-band-line px-4 text-small text-k-on-band transition-colors duration-fast ease-std hover:border-k-band-accent hover:text-k-band-accent disabled:cursor-default disabled:hover:border-k-band-line disabled:hover:text-k-on-band"
      >
        {/* A small switch: the knob moves right and turns gold when motion is reduced. */}
        <span
          aria-hidden
          className={`relative inline-block h-4 w-7 shrink-0 rounded-pill border transition-colors duration-fast ease-std ${
            pressed ? "border-k-band-accent bg-k-band-accent" : "border-k-band-line"
          }`}
        >
          <span
            className={`absolute top-1/2 h-2.5 w-2.5 -translate-y-1/2 rounded-full transition-[left] duration-fast ease-std ${
              pressed ? "left-[14px] bg-k-band" : "left-0.5 bg-k-on-band-muted"
            }`}
          />
        </span>
        Reduce motion
      </button>
      {byDevice && (
        <span id="motion-by-device" className="text-caption font-normal text-k-on-band-muted">
          Already reduced by your device settings
        </span>
      )}
    </div>
  );
}
