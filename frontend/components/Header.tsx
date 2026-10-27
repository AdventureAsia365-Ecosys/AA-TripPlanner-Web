"use client";

import Link from "next/link";
import AiLabel, { AI_BRAND_SHORT, AI_SITE_EXPLANATION, AI_SITE_LABEL } from "@/components/AiLabel";
import { ThemeToggle } from "@/ui/ThemeToggle";
import AccountLink from "@/components/AccountLink";

// Top brand bar — Adventure Asia wordmark + tagline. Kept slim so the map
// dominates the viewport (map-first product).
// AA-995 (spec §4.4, light touch): the same navy chrome as the customer pages' header (--tp-band, light text, the
// band AI chip, account link and theme toggle) and the new tagline; height and layout unchanged, so the planner's
// flows and its 56 px offset stay as they are. The planner font stays Inter (its own slice, spec R10).
export default function Header() {
  return (
    <header
      className="tp-on-dark z-30 flex items-center justify-between border-b border-k-band-edge bg-k-band px-5 text-k-on-band"
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
        <AiLabel label={AI_SITE_LABEL} shortLabel={AI_BRAND_SHORT} explanation={AI_SITE_EXPLANATION} variant="band" />
        <div className="hidden leading-tight sm:block">
          <span className="text-[11px] font-medium uppercase tracking-[0.18em] text-k-band-accent">
            Trip Planner
          </span>
          <p className="hidden text-[11px] text-k-on-band-muted md:block">
            Real tours. Shaped by you. Checked by an advisor.
          </p>
        </div>
      </div>

      <nav className="flex items-center gap-2 text-sm">
        <span className="hidden text-k-on-band-muted lg:inline">
          Pin experiences · build your trip · hand off to an advisor
        </span>
        {/* AA-908: Sign in / My trips (signing in keeps this trip in My Trips). */}
        <AccountLink tone="band" />
        {/* AA-837: the planner follows the site theme too, so it carries the same toggle. */}
        <ThemeToggle tone="band" />
      </nav>
    </header>
  );
}
