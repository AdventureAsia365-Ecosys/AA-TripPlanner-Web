// AA-995 — the title band of the customer pages that have no photo hero (/destinations, /trips, /sign-in, the legal
// pages, not-found and the error page): navy (--tp-band) under the solid header, the logo's mountain lines, an
// optional breadcrumb, an optional Satisfy kicker (titles only, <= 6 words, >= 28 px), the page's one h1, a lead and
// optional actions. One component, so every utility page opens the same way (consistent, not one-off).
// Contrast on navy (lib/contrast.test.ts): on-band 13.67 / 16.62, on-band-muted 9.98 / 10.55, band-accent 8.06 / 9.30.
import Link from "next/link";
import type { ReactNode } from "react";
import { MountainMotif } from "./NoPhotoTile";

export interface Crumb {
  href: string;
  label: string;
}

export default function PageIntro({
  title,
  kicker,
  lead,
  crumbs,
  children,
  narrow = false,
  leadRole,
}: {
  title: string;
  kicker?: string;
  lead?: ReactNode;
  // Breadcrumb trail before the current page (the current page is the h1).
  crumbs?: Crumb[];
  // Actions or extra lines under the lead.
  children?: ReactNode;
  // Narrow text column (sign-in, status pages) instead of the page width.
  narrow?: boolean;
  // role="alert" on the lead for error pages (what ErrorState gave them before).
  leadRole?: "alert" | "status";
}) {
  return (
    <section className="tp-on-dark relative isolate overflow-hidden border-b border-k-band-edge bg-k-band text-k-on-band">
      <MountainMotif className="-bottom-2 right-0 -z-10 w-3/4 text-k-band-accent md:w-2/5" />
      <div className={`mx-auto px-gutter py-10 md:py-14 ${narrow ? "max-w-2xl text-center" : "max-w-page"}`}>
        {crumbs && crumbs.length > 0 && (
          <nav aria-label="Breadcrumb" className="mb-3 text-caption font-normal text-k-on-band-muted">
            {crumbs.map((c) => (
              <span key={c.href}>
                <Link href={c.href} className="underline-offset-2 hover:text-k-band-accent hover:underline">{c.label}</Link>
                <span aria-hidden> / </span>
              </span>
            ))}
            <span className="text-k-on-band">{title}</span>
          </nav>
        )}
        {kicker && <p className="font-script text-script-sm text-k-band-accent">{kicker}</p>}
        <h1 className={`text-h1 !text-k-on-band ${kicker ? "mt-1" : ""}`}>{title}</h1>
        {lead && (
          <div role={leadRole} className={`mt-3 max-w-[60ch] text-lead text-k-on-band-muted ${narrow ? "mx-auto" : ""}`}>
            {lead}
          </div>
        )}
        {children && <div className="mt-6">{children}</div>}
      </div>
    </section>
  );
}
