"use client";
// AA-1115 (root ADR 0019 d10) — the brand words and legal links the planner's shared components show (trip panel, map
// popups, stop activities, planning chat), from the page that renders the planner: an agency's planner provides its own
// (lib/brand.ts brandCopy(name), lib/legal.ts legalPaths(slug)); AA's own planner and the AA-hosted demo tenant keep the
// default, Adventure Asia's. A context, not the tab's module state (lib/plannerTenant.ts), so the server render of an
// agency's planner already carries the agency's words (the module state is set only in the browser).
import { createContext, useContext, useMemo, type ReactNode } from "react";
import { brandCopy, type BrandCopy } from "@/lib/brand";
import { legalPaths, type LegalPaths } from "@/lib/legal";

export interface PlannerBrand {
  copy: BrandCopy;
  legal: LegalPaths;
}

// Adventure Asia's own planner (no provider). lib/agencyBrand.test.ts allows brandCopy(null) here only.
const AA_PLANNER_BRAND: PlannerBrand = { copy: brandCopy(null), legal: legalPaths(null) };

const BrandContext = createContext<PlannerBrand>(AA_PLANNER_BRAND);

/** `agency` / `agencySlug`: the agency's trading name and slug, or null for Adventure Asia's own planner. */
export function BrandProvider({
  agency,
  agencySlug,
  children,
}: {
  agency: string | null;
  agencySlug: string | null;
  children: ReactNode;
}) {
  const value = useMemo<PlannerBrand>(
    () => (agency && agencySlug ? { copy: brandCopy(agency), legal: legalPaths(agencySlug) } : AA_PLANNER_BRAND),
    [agency, agencySlug],
  );
  return <BrandContext.Provider value={value}>{children}</BrandContext.Provider>;
}

export function useBrand(): PlannerBrand {
  return useContext(BrandContext);
}
