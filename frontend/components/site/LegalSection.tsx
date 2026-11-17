// AA-886 — one numbered section of a legal page; `id` gives it a link target. AA-1115: its own file, shared by AA's
// legal pages (./LegalPage.tsx) and an agency's (components/AgencyLegalPage.tsx).
import type { ReactNode } from "react";

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
