"use client";

import Link from "next/link";
import AiLabel, { AI_AGENCY_EXPLANATION, AI_AGENCY_LABEL } from "@/components/AiLabel";
// AA-1115: Adventure Asia's words come from lib/brand.ts and only in the AA bar below (lib/agencyBrand.test.ts allows
// this import here only because an agency's planner returns the agency bar first).
import { AA_AI_REPORT, AA_BAR, AA_SITE_AI } from "@/lib/brand";
import { ThemeToggle } from "@/ui/ThemeToggle";
import { legalPaths } from "@/lib/legal";
import { isAgencyPlanner } from "@/lib/leadForm";
import { usePlannerLang, usePlannerT } from "@/components/PlannerLang";
import { PLANNER } from "@/lib/planner.messages";

// Top brand bar — Adventure Asia wordmark + tagline. Kept slim so the map
// dominates the viewport (map-first product).
// AA-995 (spec §4.4, light touch): the same navy chrome as the customer pages' header (--tp-band, light text, the
// band AI chip, account link and theme toggle) and the new tagline; height and layout unchanged, so the planner's
// flows and its 56 px offset stay as they are. Poppins like the site since the R10 slice.
// AA-1094 (S278 audit M21, root ADR 0019 d10 / d11): on an agency's planner (/{slug}/plan) the bar is the agency's: its
// trading name (its brand colours come from the page's brand-token block), linking back to its own planner, never the
// Adventure Asia logo or a link to AA's home. No account link there: travellers of an agency are guests (d11).
// AA-1130 (S283 audit T1): the agency bar carries the agency's own Privacy and Terms links (lib/legal.ts legalPaths),
// so every /{slug} page (planner, shared trip, tour page, the legal pages) keeps them in view; the planner is
// full-height and has no footer of its own.
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
        <Link href="/" prefetch={false} aria-label={AA_BAR.homeLabel} className="aa-focus">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src="/brand/logo-h108.png"
            alt={AA_BAR.logoAlt}
            className="h-9 w-auto"
          />
        </Link>
        {/* AA-805: AI Phase 0 — say plainly, next to the AA mark, that the planner uses AI.
            AA-819: a button with its explanation (touch + keyboard), naming only what is AI. */}
        <AiLabel label={AA_SITE_AI.label} shortLabel={AA_SITE_AI.shortLabel} explanation={AA_SITE_AI.explanation} variant="band" report={AA_AI_REPORT} />
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
        {/* AA-837: the planner follows the site theme too, so it carries the same toggle. */}
        <ThemeToggle tone="band" />
      </nav>
    </header>
  );
}

function AgencyHeader({ agency }: { agency: HeaderAgency }) {
  const t = usePlannerT(PLANNER);
  const lang = usePlannerLang();
  return (
    <header
      data-testid="agency-header"
      className="tp-on-dark z-30 flex items-center justify-between gap-3 border-b border-k-band-edge bg-k-band px-5 text-k-on-band"
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
        <AiLabel label={AI_AGENCY_LABEL} explanation={lang === "vi" ? t("aiAgencyExplanation") : AI_AGENCY_EXPLANATION}
          variant="band" />
        {/* AA-1160: --tp-band-accent follows the agency's theme on its pages (lib/tenantBrand.ts plannerBrandCss). */}
        <span className="hidden text-[11px] font-medium uppercase tracking-[0.18em] text-k-band-accent sm:inline"
          data-testid="agency-planner-label">
          {t("tripPlanner")}
        </span>
      </div>
      <nav className="flex shrink-0 items-center gap-2 text-sm" aria-label={t("legalAndDisplay")}>
        <ul className="flex items-center gap-2.5 text-[11px] text-k-on-band-muted" data-testid="agency-legal-links">
          {/* The AA-hosted demo tenant keeps AA's own pages, as its planner's forms do (PlannerApp plannerBrand). */}
          {legalPaths(isAgencyPlanner(agency.slug) ? agency.slug : null, lang).links.map((l) => (
            <li key={l.href}>
              <a href={l.href} className="aa-focus underline-offset-2 hover:text-k-on-band hover:underline">
                {l.label}
              </a>
            </li>
          ))}
        </ul>
        <ThemeToggle tone="band" />
      </nav>
    </header>
  );
}
