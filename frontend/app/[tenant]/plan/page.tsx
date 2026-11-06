// AA-1038 R5 slice 2 (root ADR 0019 d10) — /{slug}/plan: the map planner of one agency tenant, on the existing host.
// The slug is resolved on the server (lib/tenantSite.ts, CIS view catalog.v_tenant_tripplanner_v1): only a served
// tenant opens the planner; an unknown slug, a tenant without the TripPlanner add-on or a suspended one is the 404
// page; a lookup that cannot answer throws to app/[tenant]/error.tsx (retryable, never the 404). The planner then
// names the tenant on every /api/trip call and the BFF signs it for the Lambda. The bare /plan stays the demo tenant.
// Theme tokens and the tenant's own copy come with the kit brand token slice.
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import PlannerApp from "../../plan/PlannerApp";
import { parseIntent } from "@/lib/planIntent";
import { isTenantSlug, resolveTenant } from "@/lib/tenantSite";

type Props = {
  params: Promise<{ tenant: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { tenant } = await params;
  // A lookup that is down gives the generic title here; the page itself throws it to app/[tenant]/error.tsx.
  const site = isTenantSlug(tenant) ? await resolveTenant(tenant).catch(() => null) : null;
  return {
    title: site ? `Trip planner · ${site.name}` : "Trip planner",
    // Personal planning state: keep it out of search results (as /plan).
    robots: { index: false, follow: false },
  };
}

export default async function TenantPlanPage({ params, searchParams }: Props) {
  const { tenant } = await params;
  if (!isTenantSlug(tenant)) notFound();
  const site = await resolveTenant(tenant);
  if (!site) notFound();
  return <PlannerApp intent={parseIntent(await searchParams)} tenantSlug={site.slug} />;
}
