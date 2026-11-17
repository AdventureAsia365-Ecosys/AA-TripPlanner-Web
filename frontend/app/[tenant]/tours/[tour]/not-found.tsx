"use client";
// AA-1115 — the 404 of an agency's tour page (a tour the agency did not make its own, or one AA no longer serves). It
// must not be Adventure Asia's 404 (app/not-found.tsx: AA's bar, footer and catalogue links), because an agency's own
// domain is rewritten onto these paths: a plain kit empty state with no brand and one way back, the agency's planner.
// A not-found file gets no params, so the planner path comes from the address bar (lib/tenantTours.ts).
import { usePathname } from "next/navigation";
import { tenantPlanPathFromTourPath } from "@/lib/tenantTours";
import { ButtonLink, EmptyState } from "@/ui";

export default function TenantTourNotFound() {
  const planHref = tenantPlanPathFromTourPath(usePathname());
  return (
    <main id="main" className="mx-auto max-w-md px-gutter py-section" data-testid="tenant-not-found">
      <EmptyState title="We couldn't find that tour">
        It may no longer be offered, or the link is not complete.
        <div className="mt-4 flex justify-center">
          <ButtonLink href={planHref} variant="primary" size="sm">Plan a trip</ButtonLink>
        </div>
      </EmptyState>
    </main>
  );
}
