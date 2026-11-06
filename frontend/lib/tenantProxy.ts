// AA-1038 R5 slice 2 — the tenant half of the /api/trip BFF (app/api/trip/route.ts): from the slug the planner sends
// (header x-tp-tenant-slug, lib/plannerTenant.ts) to the signed header the assembly Lambda checks. Pure, so
// `npm test` runs it; the route passes the resolver and the key in.
//
//   no slug header          -> no tenant header: the Lambda keeps the default (demo) tenant, as before slice 2;
//   a slug that resolves    -> x-tp-tenant: a fresh assertion for the resolved tenant id (never the browser's word);
//   anything else           -> 404 unknown_tenant, and the Lambda is not called;
//   no edge key configured  -> 503 (the Lambda could not check an assertion either).

import { TENANT_ASSERTION_HEADER, signTenantAssertion } from "./tenantAssertion.ts";
import type { TenantSite } from "./tenantSite.ts";

export type TenantPick =
  | { ok: true; headers: Record<string, string> }
  | { ok: false; status: number; error: string };

export async function tenantHeaders(
  slugHeader: string | null,
  edgeKey: string,
  resolve: (slug: string) => Promise<TenantSite | null>,
  nowS?: number,
): Promise<TenantPick> {
  if (slugHeader === null || slugHeader === "") return { ok: true, headers: {} };
  const site = await resolve(slugHeader);
  if (!site) return { ok: false, status: 404, error: "unknown_tenant" };
  if (!edgeKey) return { ok: false, status: 503, error: "tenant_assertion_unavailable" };
  return { ok: true, headers: { [TENANT_ASSERTION_HEADER]: signTenantAssertion(edgeKey, site.tenant_id, site.slug, nowS) } };
}
