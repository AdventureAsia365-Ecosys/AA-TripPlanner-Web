"use client";

import Link from "next/link";
import AiLabel, { AI_SITE_EXPLANATION, AI_SITE_LABEL } from "@/components/AiLabel";

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
        {/* AA-805: AI Phase 0 — say plainly, next to the AA mark, that the planner uses AI.
            AA-819: a button with its explanation (touch + keyboard), naming only what is AI. */}
        <AiLabel label={AI_SITE_LABEL} explanation={AI_SITE_EXPLANATION} variant="outline" />
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
