// AA-812 — header of the kit pages (home, tour, destination). The AI-assisted label from AA-805
// stays next to the AA mark (AA-819: a button that explains itself on tap or keyboard).
// AA-995 (spec §3.1 + immersive grafts) — navy header. Solid `--tp-band` by default (tour, legal, sign-in
// pages, and every page without JS). With `overHero` (home) it sits transparent on a dark scrim over the
// photo hero and turns solid once the hero has scrolled away (HeaderScrollState). Below md the links move
// into a native <dialog> menu (MobileMenu); a "Skip to content" link comes first in the tab order.
import Link from "next/link";
import AiLabel, { AI_BRAND_SHORT, AI_SITE_EXPLANATION, AI_SITE_LABEL } from "@/components/AiLabel";
import { ThemeToggle } from "@/ui/ThemeToggle";
import { buttonClass } from "@/ui";
import AccountLink from "@/components/AccountLink";
import HeaderScrollState from "./HeaderScrollState";
import MobileMenu from "./MobileMenu";

export const SITE_NAV = [
  // The destinations index (same target as the breadcrumbs and the footer); the home keeps its own in-page way in.
  { href: "/destinations", label: "Destinations" },
  { href: "/#tours", label: "Tours" },
  { href: "/#how-it-works", label: "How it works" },
];

export default function SiteHeader({ overHero = false }: { overHero?: boolean }) {
  return (
    <>
      <a href="#main" className="tp-skip">
        Skip to content
      </a>
      <header
        data-over-hero={overHero ? "" : undefined}
        className="tp-header tp-on-dark sticky top-0 z-header h-header text-k-on-band"
      >
        <div className="mx-auto flex h-full max-w-page items-center justify-between gap-3 px-gutter">
          <div className="flex min-w-0 items-center gap-2.5">
            <Link href="/" aria-label="Adventure Asia Trip Planner — home" className="shrink-0">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src="/brand/logo.png" alt="Adventure Asia" width={50} height={32} className="h-8 w-auto lg:h-9" />
            </Link>
            <AiLabel tone="kit" variant="band" label={AI_SITE_LABEL} shortLabel={AI_BRAND_SHORT} explanation={AI_SITE_EXPLANATION} />
          </div>
          <nav aria-label="Main" className="flex items-center gap-1.5 md:gap-2">
            {SITE_NAV.map((l) => (
              <Link
                key={l.href}
                href={l.href}
                className="hidden px-2 text-small font-medium text-k-on-band transition-colors duration-fast ease-std hover:text-k-band-accent md:inline"
              >
                {l.label}
              </Link>
            ))}
            {/* AA-908: Sign in / My trips, fetched by the browser (this header is on CDN-cached pages). */}
            <AccountLink tone="band" className="hidden md:inline" />
            <span className="hidden md:inline-flex">
              <ThemeToggle tone="band" />
            </span>
            <Link href="/plan" className={buttonClass("primary", "sm", "min-h-11 px-4 md:min-h-10")}>
              Plan a trip
            </Link>
            <MobileMenu links={SITE_NAV} />
          </nav>
        </div>
        {overHero && <HeaderScrollState />}
      </header>
    </>
  );
}
