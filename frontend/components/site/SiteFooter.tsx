import Link from "next/link";
import { LEGAL_LINKS } from "@/lib/legal";
import TrustStrip from "./TrustStrip";

export default function SiteFooter() {
  return (
    <footer className="mt-16 border-t border-k-line bg-k-card">
      <div className="mx-auto max-w-6xl px-4 py-8">
        <TrustStrip />
        <div className="mt-6 flex flex-col gap-2 text-[12px] text-k-muted sm:flex-row sm:items-center sm:justify-between">
          <p>© Adventure Asia. Every itinerary is reviewed by an Adventure Asia advisor before you book.</p>
          <nav aria-label="Footer" className="flex flex-wrap gap-x-4 gap-y-1">
            <Link href="/plan" className="hover:text-k-accent-deep">Trip planner</Link>
            {/* AA-886: draft legal pages (each page carries a "Draft — pending legal review" banner). */}
            {LEGAL_LINKS.map((l) => (
              <Link key={l.href} href={l.href} className="hover:text-k-accent-deep">
                {l.label}
              </Link>
            ))}
            <a href="https://adventure.asia/" className="hover:text-k-accent-deep" rel="noopener">adventure.asia</a>
          </nav>
        </div>
      </div>
    </footer>
  );
}
