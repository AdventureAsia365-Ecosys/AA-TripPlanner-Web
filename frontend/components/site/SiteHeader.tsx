// AA-812 — header of the kit pages (home, tour, destination). The AI-assisted label from AA-805
// stays next to the AA mark (AA-819: a button that explains itself on tap or keyboard).
import Link from "next/link";
import AiLabel, { AI_BRAND_SHORT, AI_SITE_EXPLANATION, AI_SITE_LABEL } from "@/components/AiLabel";
import { ThemeToggle } from "@/ui/ThemeToggle";
import { buttonClass } from "@/ui";

export default function SiteHeader() {
  return (
    <header className="sticky top-0 z-30 border-b border-k-line bg-k-card">
      <div className="mx-auto flex h-14 max-w-6xl items-center justify-between gap-3 px-4">
        <div className="flex min-w-0 items-center gap-2.5">
          <Link href="/" aria-label="Adventure Asia Trip Planner — home" className="shrink-0">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src="/brand/logo.png" alt="Adventure Asia" className="h-8 w-auto" />
          </Link>
          <AiLabel tone="kit" variant="outline" label={AI_SITE_LABEL} shortLabel={AI_BRAND_SHORT} explanation={AI_SITE_EXPLANATION} />
        </div>
        <nav aria-label="Main" className="flex items-center gap-1.5 sm:gap-3">
          <Link href="/#destinations" className="hidden px-2 text-[13px] font-medium text-k-body hover:text-k-accent-deep sm:inline">
            Destinations
          </Link>
          <Link href="/#tours" className="hidden px-2 text-[13px] font-medium text-k-body hover:text-k-accent-deep sm:inline">
            Tours
          </Link>
          <ThemeToggle />
          <Link href="/plan" className={buttonClass("primary", "sm")}>
            Plan a trip
          </Link>
        </nav>
      </div>
    </header>
  );
}
