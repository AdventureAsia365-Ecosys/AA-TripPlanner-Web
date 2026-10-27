// AA-886 — shell of the legal pages (/privacy, /terms): kit header and footer, a draft banner that is
// always visible while counsel has not approved the text, and a readable single column.
// AA-995: opens with the Golden Hour title band (PageIntro: breadcrumb, h1, version line).
import type { ReactNode } from "react";
import Link from "next/link";
import PageIntro from "@/components/site/PageIntro";
import SiteFooter from "@/components/site/SiteFooter";
import SiteHeader from "@/components/site/SiteHeader";
import { LEGAL_CONTACT_EMAIL, LEGAL_DRAFT_LABEL, LEGAL_UPDATED, LEGAL_VERSION } from "@/lib/legal";
import { Icon } from "@/ui";

export function DraftBanner() {
  return (
    <div
      role="note"
      data-testid="legal-draft-banner"
      className="flex items-start gap-3 rounded-card border border-k-accent-border bg-k-accent-tint px-4 py-3 text-small text-k-body"
    >
      <Icon name="info" className="mt-0.5 h-5 w-5 shrink-0 text-k-accent-deep" />
      <div>
        <p className="font-semibold uppercase tracking-[0.04em] text-k-accent-deep">{LEGAL_DRAFT_LABEL}</p>
        <p className="mt-1">
          This is a working draft. Adventure Asia&apos;s legal counsel has not reviewed or approved it, and it
          may change before the Trip Planner launches. Parts marked [to be confirmed] are still open. Questions:{" "}
          <a href={`mailto:${LEGAL_CONTACT_EMAIL}`} className="font-semibold text-k-accent-deep hover:underline">
            {LEGAL_CONTACT_EMAIL}
          </a>
          .
        </p>
      </div>
    </div>
  );
}

/** One numbered section; `id` gives it a link target. */
export function LegalSection({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <section id={id} aria-labelledby={`${id}-title`} className="scroll-mt-[calc(var(--tp-header-h)+16px)] pt-8">
      <h2 id={`${id}-title`} className="text-h3 text-k-ink">
        {title}
      </h2>
      <div className="mt-3 space-y-3 text-body text-k-body [&_a]:font-semibold [&_a]:text-k-accent-deep [&_a:hover]:underline [&_li]:ml-5 [&_li]:list-disc [&_li]:pl-1 [&_ul]:space-y-1.5">
        {children}
      </div>
    </section>
  );
}

export default function LegalPage({
  title,
  intro,
  otherPage,
  children,
}: {
  title: string;
  intro: ReactNode;
  otherPage: { href: string; label: string };
  children: ReactNode;
}) {
  return (
    <div className="aa-site">
      <SiteHeader />
      <main id="main">
        <PageIntro title={title} crumbs={[{ href: "/", label: "Home" }]} lead={`${LEGAL_VERSION} · last updated ${LEGAL_UPDATED}`} />
        <div className="mx-auto max-w-3xl px-gutter pb-section pt-10">
          <DraftBanner />
          <div className="mt-6 text-lead text-k-body">{intro}</div>
          {children}
          <p className="mt-10 border-t border-k-line pt-4 text-small text-k-muted">
            See also our{" "}
            <Link href={otherPage.href} className="font-semibold text-k-accent-deep hover:underline">
              {otherPage.label}
            </Link>
            .
          </p>
        </div>
      </main>
      <SiteFooter />
    </div>
  );
}
