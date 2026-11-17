// AA-1115 (root ADR 0019 d10 / d11) — the shell of an agency's own legal pages (/{slug}/privacy, /{slug}/terms; on the
// agency's own domain /privacy and /terms, which proxy.ts rewrites here). The agency's bar (its trading name, its brand
// tokens), the page's title band, a draft banner that is always visible while the text is not approved, and a readable
// column. No Adventure Asia logo, footer or contact: the agency is responsible for the trips its travellers send it.
// Same section component as AA's legal pages (components/site/LegalPage.tsx LegalSection), so both read alike.
import type { ReactNode } from "react";
import Header from "@/components/Header";
import PageIntro from "@/components/site/PageIntro";
import { LegalSection } from "@/components/site/LegalSection";
import { LEGAL_DRAFT_LABEL, LEGAL_UPDATED, LEGAL_VERSION } from "@/lib/legal";
import { Icon } from "@/ui";

export { LegalSection };

export interface AgencyLegalSite {
  slug: string;
  name: string;
}

function AgencyDraftBanner({ agency }: { agency: string }) {
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
          This is a working draft that {agency} has not yet reviewed or approved, and it may change. Parts marked [to
          be confirmed] are still open. For any question, contact {agency}.
        </p>
      </div>
    </div>
  );
}

export default function AgencyLegalPage({
  site,
  title,
  intro,
  otherPage,
  children,
}: {
  site: AgencyLegalSite;
  title: string;
  intro: ReactNode;
  otherPage: { href: string; label: string };
  children: ReactNode;
}) {
  return (
    <div className="min-h-screen bg-k-bg text-k-ink" data-testid="agency-legal-page">
      <Header agency={{ slug: site.slug, name: site.name }} />
      <main id="main">
        <PageIntro title={title} lead={`${LEGAL_VERSION} · last updated ${LEGAL_UPDATED}`} />
        <div className="mx-auto max-w-3xl px-gutter pb-section pt-10">
          {/* A document load, as every way into the planner (lib/plannerNav.ts). */}
          <a
            href={`/${site.slug}/plan`}
            className="aa-focus mb-6 inline-flex items-center gap-1 text-small text-k-muted hover:text-k-accent-deep"
          >
            <Icon name="chevron-left" className="h-4 w-4" />
            Back to the trip planner
          </a>
          <AgencyDraftBanner agency={site.name} />
          <div className="mt-6 text-lead text-k-body">{intro}</div>
          {children}
          <p className="mt-10 border-t border-k-line pt-4 text-small text-k-muted">
            See also the{" "}
            <a href={otherPage.href} className="font-semibold text-k-accent-deep hover:underline">
              {otherPage.label}
            </a>
            .
          </p>
        </div>
      </main>
    </div>
  );
}
