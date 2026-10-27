// AA-886 — shell of the legal pages (/privacy, /terms): kit header and footer, a draft banner that is
// always visible while counsel has not approved the text, and a readable single column.
import type { ReactNode } from "react";
import Link from "next/link";
import SiteFooter from "@/components/site/SiteFooter";
import SiteHeader from "@/components/site/SiteHeader";
import { LEGAL_CONTACT_EMAIL, LEGAL_DRAFT_LABEL, LEGAL_UPDATED, LEGAL_VERSION } from "@/lib/legal";
import { Icon } from "@/ui";

export function DraftBanner() {
  return (
    <div
      role="note"
      data-testid="legal-draft-banner"
      className="flex items-start gap-3 rounded-2xl border border-k-accent-border bg-k-accent-tint px-4 py-3 text-[13px] leading-relaxed text-k-body"
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
    <section id={id} aria-labelledby={`${id}-title`} className="scroll-mt-20 pt-8">
      <h2 id={`${id}-title`} className="text-[20px] font-semibold text-k-ink">
        {title}
      </h2>
      <div className="mt-3 space-y-3 text-[14px] leading-relaxed text-k-body [&_a]:font-semibold [&_a]:text-k-accent-deep [&_a:hover]:underline [&_li]:ml-5 [&_li]:list-disc [&_li]:pl-1 [&_ul]:space-y-1.5">
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
      <main id="main" className="mx-auto max-w-3xl px-4 pb-6 pt-10">
        <nav aria-label="Breadcrumb" className="text-[12px] text-k-muted">
          <Link href="/" className="hover:text-k-accent-deep">Home</Link> / {title}
        </nav>
        <h1 className="mt-2 text-[30px] font-semibold text-k-ink sm:text-[36px]">{title}</h1>
        <p className="mt-1 text-[12px] text-k-muted">
          {LEGAL_VERSION} · last updated {LEGAL_UPDATED}
        </p>
        <div className="mt-5">
          <DraftBanner />
        </div>
        <div className="mt-5 text-[15px] leading-relaxed text-k-body">{intro}</div>
        {children}
        <p className="mt-10 border-t border-k-line pt-4 text-[13px] text-k-muted">
          See also our{" "}
          <Link href={otherPage.href} className="font-semibold text-k-accent-deep hover:underline">
            {otherPage.label}
          </Link>
          .
        </p>
      </main>
      <SiteFooter />
    </div>
  );
}
