"use client";
// AA-1115 — the 404 of every page under /{slug} (an agency's site; its own domain is rewritten onto these paths), shown
// by the not-found files of app/[tenant]/ (lib/agencyBrand.test.ts checks each page that can call notFound() has one).
// It must not be Adventure Asia's 404 (app/not-found.tsx: AA's bar, footer and catalogue links): a plain kit empty
// state with no brand and one way back, the agency's planner. A not-found file gets no params, so the planner path
// comes from the address bar (lib/tenantTours.ts tenantPlanPathFromPath).
import { usePathname } from "next/navigation";
import { tenantPlanPathFromPath } from "@/lib/tenantTours";
import { ButtonLink, EmptyState } from "@/ui";

export default function TenantNotFound({ title, children }: { title: string; children: React.ReactNode }) {
  const planHref = tenantPlanPathFromPath(usePathname());
  return (
    <main id="main" className="mx-auto max-w-md px-gutter py-section" data-testid="tenant-not-found">
      <EmptyState title={title}>
        {children}
        <div className="mt-4 flex justify-center">
          <ButtonLink href={planHref} variant="primary" size="sm">Plan a trip</ButtonLink>
        </div>
      </EmptyState>
    </main>
  );
}
