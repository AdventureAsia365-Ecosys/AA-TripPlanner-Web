// AA-1038 R5 slice 2 (root ADR 0019 d10) — /{slug}: an agency tenant's site on the existing host. Its only page in
// this slice is the planner, so a served tenant's bare slug goes to /{slug}/plan; any other slug is the 404 page
// (static routes such as /plan, /tours or /destinations win over this dynamic segment, and lib/tenantSlug.ts refuses
// those names); a lookup that cannot answer throws to app/[tenant]/error.tsx (retryable, never the 404).
import { notFound, redirect } from "next/navigation";
import { isTenantSlug, resolveTenant } from "@/lib/tenantSite";

type Props = { params: Promise<{ tenant: string }> };

export default async function TenantHome({ params }: Props) {
  const { tenant } = await params;
  if (!isTenantSlug(tenant)) notFound();
  const site = await resolveTenant(tenant);
  if (!site) notFound();
  redirect(`/${site.slug}/plan`);
}
