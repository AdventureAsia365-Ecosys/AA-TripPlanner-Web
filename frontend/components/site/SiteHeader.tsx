// AA-812 — header of the kit pages (home, tour, destination). The AI-assisted label from AA-805
// stays next to the AA mark.
import Link from "next/link";
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
          <span
            title="Suggestions, search and some narration are AI-assisted. An Adventure Asia advisor reviews every trip."
            className="shrink-0 rounded-full border border-k-accent-border px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-k-accent-deep"
          >
            AI-assisted
          </span>
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
