// AA-1041 (R7 item 6) — /{slug}/plan/shared#…: an agency tenant's read-only share / print view of a trip. Same tenant
// resolution as /{slug}/plan (lib/tenantSite.ts: only a served tenant; unknown, suspended or no TripPlanner add-on is
// the 404 page; a lookup that cannot answer throws to app/[tenant]/error.tsx), same brand-token layer, so the shared
// trip carries the agency's colours and name.
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import Header from "@/components/Header";
import SharedTripView from "@/components/SharedTripView";
import { isTenantSlug, resolveTenant } from "@/lib/tenantSite";
import { brandThemeCss } from "@/ui/core/brandTheme";

type Props = { params: Promise<{ tenant: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { tenant } = await params;
  const site = isTenantSlug(tenant) ? await resolveTenant(tenant).catch(() => null) : null;
  return {
    title: site ? `Shared trip · ${site.name}` : "Shared trip",
    robots: { index: false, follow: false },
  };
}

export default async function TenantSharedTripPage({ params }: Props) {
  const { tenant } = await params;
  if (!isTenantSlug(tenant)) notFound();
  const site = await resolveTenant(tenant);
  if (!site) notFound();
  const brand = brandThemeCss(site.theme);
  return (
    <div className="min-h-full bg-k-bg">
      {brand && <style data-testid="tenant-brand">{brand}</style>}
      <div className="tp-no-print">
        <Header />
      </div>
      <SharedTripView planHref={`/${site.slug}/plan`} agencyName={site.name} />
    </div>
  );
}
