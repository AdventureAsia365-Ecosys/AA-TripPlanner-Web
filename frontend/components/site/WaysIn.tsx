// AA-995 — three ways to start (destinations, the map, a tour) as lifted cards, on the cream surface. The status pages
// (not-found, the route error page) end with it, so a lost visitor lands somewhere useful and the navy title band does
// not run straight into the navy footer.
import Link from "next/link";
import { Icon } from "@/ui";

const WAYS = [
  { href: "/destinations", icon: "compass", title: "Browse destinations", body: "Pick a country and see its tours." },
  { href: "/plan", icon: "map", title: "Plan on the map", body: "Pin places and build the days yourself." },
  { href: "/#tours", icon: "route", title: "Start from a tour", body: "Take a real route and make it yours." },
];

export default function WaysIn() {
  return (
    <section aria-label="Ways to start" className="bg-k-bg py-section">
      <ul className="mx-auto grid max-w-page gap-3 px-gutter md:grid-cols-3 md:gap-6">
        {WAYS.map((w) => (
          <li key={w.href}>
            <Link href={w.href} prefetch={false}
                  className="tp-lift group flex h-full items-start gap-4 rounded-card border border-k-line bg-k-card p-5 shadow-e1 hover:border-k-accent-border">
              <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-k-accent-tint">
                <Icon name={w.icon} className="h-5 w-5 text-k-accent-deep" />
              </span>
              <span>
                <span className="block text-body font-semibold text-k-ink"><span className="tp-link">{w.title}</span></span>
                <span className="mt-1 block text-small text-k-muted">{w.body}</span>
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
