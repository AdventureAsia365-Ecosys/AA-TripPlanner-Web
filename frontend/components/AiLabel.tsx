"use client";

// AA-819 (AUP 2026-11, scope item 4): the label on every block of AI-generated text a traveller
// sees. It is a button: a tap, a click or Enter/Space toggles a short explanation (aria-expanded),
// Escape or a tap elsewhere closes it. A `title` tooltip alone is unreachable on touch and by
// keyboard (S239 audit, defect D5).
//
// Wording must describe only what is really AI: catalog text (tour pages, experience
// descriptions, "AA itinerary" narration) is drafted by the CIS writer model and reviewed by
// Adventure Asia; "AI narration" is written by the model on request; suggestions are rule-based
// (backend/assembly/suggestions.py) and are not AI.

import { useEffect, useId, useRef, useState } from "react";

export type AiLabelTone = "planner" | "kit";

// The planner keeps its light-only aa-* palette; kit pages (home, tour, destination) use the
// theme-aware k-* tokens (AA-812).
const STYLE: Record<AiLabelTone, { solid: string; outline: string; panel: string; focus: string }> = {
  planner: {
    solid: "border border-transparent bg-aa-gold-soft text-aa-gold-dark hover:border-aa-gold/40",
    outline: "border border-aa-gold/40 bg-white text-aa-gold-dark hover:bg-aa-gold-soft",
    panel: "border-aa-line bg-white text-aa-ink-soft",
    focus: "aa-focus",
  },
  kit: {
    solid: "border border-transparent bg-k-accent-tint text-k-accent-deep hover:border-k-accent-border",
    outline: "border border-k-accent-border bg-k-card text-k-accent-deep hover:bg-k-accent-tint",
    panel: "border-k-line bg-k-card text-k-body",
    focus: "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-k-accent",
  },
};

// Shared wording for catalog text written by the CIS writer model and reviewed (tour pages, map
// experience descriptions).
export const AI_REVIEWED_LABEL = "AI-assisted, reviewed by Adventure Asia";
export const AI_REVIEWED_EXPLANATION =
  "Drafted by an AI writing model from Adventure Asia's own tour notes, then reviewed by our team. " +
  "Your advisor confirms every detail before you book.";

// The site-wide pill next to the AA mark (Header, SiteHeader).
export const AI_SITE_LABEL = "AI-assisted";
export const AI_SITE_EXPLANATION =
  "Tour descriptions and day-by-day itineraries are drafted with AI and reviewed by Adventure Asia. " +
  "Trip narration labelled “AI narration” or “AA + AI” is written by AI when you ask for it, and the " +
  "planner's place search uses AI to match your words. An Adventure Asia advisor reviews every trip.";

interface Props {
  label: string;
  explanation: string;
  tone?: AiLabelTone;
  variant?: "solid" | "outline";
  // popover: the explanation floats under the label (headers, toolbars).
  // inline: it opens in the flow below the label (inside clipped or scrolling boxes).
  placement?: "popover" | "inline";
  className?: string;
}

export default function AiLabel({
  label,
  explanation,
  tone = "planner",
  variant = "solid",
  placement = "popover",
  className = "",
}: Props) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const root = useRef<HTMLSpanElement>(null);
  const style = STYLE[tone];

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    const onPointer = (e: PointerEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onPointer);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onPointer);
    };
  }, [open]);

  const panelPos =
    placement === "popover"
      ? "absolute left-0 top-full z-50 mt-1 w-64 max-w-[calc(100vw-2rem)] shadow-aa-sm"
      : "mt-1 w-full max-w-sm";

  return (
    <span ref={root} className={`relative inline-flex flex-col items-start ${className}`}>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((v) => !v)}
        className={`${style.focus} ${style[variant]} inline-flex min-h-6 shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-left text-[10px] font-bold uppercase leading-tight tracking-wide transition`}
      >
        {label}
        <span
          aria-hidden
          className="inline-flex h-3 w-3 shrink-0 items-center justify-center rounded-full border border-current text-[8px] font-bold normal-case leading-none"
        >
          {open ? "×" : "i"}
        </span>
      </button>
      <span
        id={panelId}
        role="note"
        className={`${open ? "block" : "hidden"} ${panelPos} rounded-lg border px-2.5 py-2 text-[11px] font-normal normal-case leading-relaxed tracking-normal ${style.panel}`}
      >
        {explanation}
      </span>
    </span>
  );
}
