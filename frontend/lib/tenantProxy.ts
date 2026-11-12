// AA-1038 R5 slice 2 — the tenant half of the /api/trip BFF (app/api/trip/route.ts): from the slug the planner sends
// (header x-tp-tenant-slug, lib/plannerTenant.ts) to the signed header the assembly Lambda checks. Pure, so
// `npm test` runs it; the route passes the resolver and the key in.
//
//   no slug header          -> no tenant header: the Lambda keeps the default (demo) tenant, as before slice 2;
//   a slug that resolves    -> x-tp-tenant: a fresh assertion for the resolved tenant id (never the browser's word);
//   the lookup is down      -> 503 tenant_lookup_unavailable (retryable; never reported as an unknown tenant);
//   anything else           -> 404 unknown_tenant, and the Lambda is not called;
//   no edge key configured  -> 503 (the Lambda could not check an assertion either).
// Used by every BFF route that calls the assembly Lambda for a traveller: /api/trip, /api/me and the /me/claim of
// /api/auth/verify (lib/accountProxy.ts).

import { TENANT_ASSERTION_HEADER, normaliseEdgeKey, signTenantAssertion } from "./tenantAssertion.ts";
import { TenantLookupUnavailable, type TenantSite } from "./tenantSite.ts";

export { TENANT_SLUG_HEADER } from "./tenantSite.ts";

export type TenantPick =
  | { ok: true; headers: Record<string, string> }
  | { ok: false; status: number; error: string };

export async function tenantHeaders(
  slugHeader: string | null,
  keyMaterial: string,
  resolve: (slug: string) => Promise<TenantSite | null>,
  nowS?: number,
): Promise<TenantPick> {
  if (slugHeader === null || slugHeader === "") return { ok: true, headers: {} };
  let site: TenantSite | null;
  try {
    site = await resolve(slugHeader);
  } catch (e) {
    if (e instanceof TenantLookupUnavailable) return { ok: false, status: 503, error: "tenant_lookup_unavailable" };
    throw e;
  }
  if (!site) return { ok: false, status: 404, error: "unknown_tenant" };
  if (!normaliseEdgeKey(keyMaterial)) return { ok: false, status: 503, error: "tenant_assertion_unavailable" };
  return {
    ok: true,
    headers: { [TENANT_ASSERTION_HEADER]: signTenantAssertion(keyMaterial, site.tenant_id, site.slug, nowS) },
  };
}
