"use client";

import Link from "next/link";

// Top brand bar — Adventure Asia wordmark + tagline. Kept slim so the map
// dominates the viewport (map-first product).
export default function Header() {
  return (
    <header
      className="z-30 flex items-center justify-between border-b border-aa-line bg-white px-5"
      style={{ height: "56px", flexShrink: 0 }}
    >
      <div className="flex items-center gap-3">
        {/* Official Adventure Asia logo — AA-812: links back to the search-first home. */}
        <Link href="/" aria-label="Adventure Asia Trip Planner — home" className="aa-focus">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src="/brand/logo.png"
            alt="Adventure Asia"
            className="h-9 w-auto"
          />
        </Link>
        {/* AA-805: AI Phase 0 — say plainly, next to the AA mark, that the planner uses AI. */}
        <span
          title="Suggestions, search and some narration are AI-assisted. An Adventure Asia advisor reviews every trip."
          className="shrink-0 rounded-full border border-aa-gold/40 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-aa-gold-dark"
        >
          AI-assisted
        </span>
        <div className="hidden leading-tight sm:block">
          <span className="text-[11px] font-medium uppercase tracking-[0.18em] text-aa-gold">
            Trip Planner
          </span>
          <p className="hidden text-[11px] text-aa-muted md:block">
            Discreet executive adventures, crafted day by day
          </p>
        </div>
      </div>

      <nav className="flex items-center gap-2 text-sm">
        <span className="hidden text-aa-muted md:inline">
          Pin experiences · build your trip · hand off to an advisor
        </span>
      </nav>
    </header>
  );
}
