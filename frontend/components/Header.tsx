"use client";

import Link from "next/link";
import AiLabel, {
  AI_AGENCY_EXPLANATION,
  AI_AGENCY_LABEL,
  AI_BRAND_SHORT,
  AI_SITE_EXPLANATION,
  AI_SITE_LABEL,
} from "@/components/AiLabel";
import { ThemeToggle } from "@/ui/ThemeToggle";
import AccountLink from "@/components/AccountLink";

// Top brand bar — Adventure Asia wordmark + tagline. Kept slim so the map
// dominates the viewport (map-first product).
// AA-995 (spec §4.4, light touch): the same navy chrome as the customer pages' header (--tp-band, light text, the
// band AI chip, account link and theme toggle) and the new tagline; height and layout unchanged, so the planner's
// flows and its 56 px offset stay as they are. Poppins like the site since the R10 slice.
// AA-1094 (S278 audit M21, root ADR 0019 d10 / d11): on an agency's planner (/{slug}/plan) the bar is the agency's: its
// trading name (its brand colours come from the page's brand-token block), linking back to its own planner, never the
// Adventure Asia logo or a link to AA's home. No account link there: travellers of an agency are guests (d11).
export type HeaderAgency = { slug: string; name: string };

export default function Header({ agency = null }: { agency?: HeaderAgency | null }) {
  if (agency) return <AgencyHeader agency={agency} />;
  return (
    <header
      className="tp-on-dark z-30 flex items-center justify-between border-b border-k-band-edge bg-k-band px-5 text-k-on-band"
      style={{ height: "56px", flexShrink: 0 }}
    >
      <div className="flex items-center gap-3">
        {/* Official Adventure Asia logo — AA-812: links back to the search-first home. */}
        {/* No prefetch (S255): a prefetch of "/" runs the home hero preload next to the map's own loads. */}
        <Link href="/" prefetch={false} aria-label="Adventure Asia Trip Planner — home" className="aa-focus">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src="/brand/logo-h108.png"
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
        {/* AA-908: My trips for a traveller who still holds a session (AA-1094: no Sign in, travellers are guests). */}
        <AccountLink tone="band" />
        {/* AA-837: the planner follows the site theme too, so it carries the same toggle. */}
        <ThemeToggle tone="band" />
      </nav>
    </header>
  );
}

function AgencyHeader({ agency }: { agency: HeaderAgency }) {
  return (
    <header
      data-testid="agency-header"
      className="tp-on-dark z-30 flex items-center justify-between border-b border-k-band-edge bg-k-band px-5 text-k-on-band"
      style={{ height: "56px", flexShrink: 0 }}
    >
      <div className="flex min-w-0 items-center gap-3">
        {/* A document load (no prefetch): the planner gets its WAF token on a document load (lib/plannerNav.ts). */}
        <a
          href={`/${agency.slug}/plan`}
          className="aa-focus truncate text-base font-semibold text-k-on-band"
          data-testid="agency-name"
        >
          {agency.name}
        </a>
        <AiLabel label={AI_AGENCY_LABEL} explanation={AI_AGENCY_EXPLANATION} variant="band" />
        <span className="hidden text-[11px] font-medium uppercase tracking-[0.18em] text-k-band-accent sm:inline">
          Trip Planner
        </span>
      </div>
      <nav className="flex items-center gap-2 text-sm">
        <ThemeToggle tone="band" />
      </nav>
    </header>
  );
}
