// AA-995 (spec §3.9) — navy footer: Satisfy column titles, light links, the TrustStrip on the band. No top margin (each
// page ends with its own section padding) and a light hairline on top, so a navy CTA band right above it reads apart.
// Legal links (and every footer link) use prefetch={false}: four RSC prefetches per view were measured
// on every page (research §2.6) and Vercel challenged them. The bottom row carries the "Reduce motion" toggle.
import Link from "next/link";
import { LEGAL_LINKS } from "@/lib/legal";
import TrustStrip from "./TrustStrip";
import MotionToggle from "./MotionToggle";

const EXPLORE = [
  { href: "/destinations", label: "Destinations" },
  { href: "/#tours", label: "Tours" },
  { href: "/plan", label: "Trip planner" },
];

const link = "inline-flex min-h-8 items-center text-small text-k-on-band-muted transition-colors duration-fast ease-std hover:text-k-band-accent";

export default function SiteFooter() {
  return (
    <footer className="tp-on-dark border-t border-k-band-line bg-k-band text-k-on-band-muted">
      <div className="mx-auto max-w-page px-gutter py-section">
        <TrustStrip tone="band" />
        <div className="mt-10 grid gap-8 sm:grid-cols-3">
          <nav aria-labelledby="footer-explore">
            <h2 id="footer-explore" className="font-script text-script-sm text-k-band-accent">Explore</h2>
            <ul className="mt-3 space-y-1">
              {EXPLORE.map((l) => (
                <li key={l.href}>
                  <Link href={l.href} prefetch={false} className={link}>{l.label}</Link>
                </li>
              ))}
            </ul>
          </nav>
          <div>
            <h2 className="font-script text-script-sm text-k-band-accent">Adventure Asia</h2>
            <p className="mt-3 text-small">Every itinerary is reviewed by an Adventure Asia advisor before you book.</p>
            <a href="https://adventure.asia/" rel="noopener" className={`${link} mt-2`}>adventure.asia</a>
          </div>
          <nav aria-labelledby="footer-legal">
            <h2 id="footer-legal" className="font-script text-script-sm text-k-band-accent">Legal</h2>
            <ul className="mt-3 space-y-1">
              {/* AA-886: draft legal pages (each page carries a "Draft — pending legal review" banner). */}
              {LEGAL_LINKS.map((l) => (
                <li key={l.href}>
                  <Link href={l.href} prefetch={false} className={link}>{l.label}</Link>
                </li>
              ))}
            </ul>
          </nav>
        </div>
        {/* The viewer's "Reduce motion" choice (spec §1.4, optional toggle): the only client island in the footer. */}
        <div className="mt-10 flex flex-col-reverse gap-4 border-t border-k-band-line pt-6 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-caption font-normal">© Adventure Asia</p>
          <MotionToggle />
        </div>
      </div>
    </footer>
  );
}
