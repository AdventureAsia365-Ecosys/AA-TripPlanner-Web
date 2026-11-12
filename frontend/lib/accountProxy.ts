// AA-1038 R5 slice 2 — the account calls of the BFF to the assembly Lambda (POST /me/claim from /api/auth/verify,
// GET /me/trips from /api/me) with the same tenant step as /api/trip (lib/tenantProxy.ts): when the browser names a
// tenant (x-tp-tenant-slug, sent by the sign-in form and My Trips when they were opened from /{slug}/plan,
// lib/plannerTenant.ts tenantOfPath), the Lambda gets a signed tenant assertion and binds that tenant, so the claim
// links that tenant's guest trips and My Trips lists them. No slug = the bare path = the demo tenant, as before.
// Pure (fetch and the resolver are passed in), so `npm test` runs it; the routes pass the real ones.

import { tripHeaders } from "./tripProxy.ts";
import { tenantHeaders } from "./tenantProxy.ts";
import type { TenantSite } from "./tenantSite.ts";

export interface AccountCall {
  tripApiUrl: string;
  edgeKey: string;
  // AA-1092: the tenant assertion's key material (assertionKeyMaterialFromEnv), not the edge key.
  assertionKey: string;
  requestId: string;
  accessToken: string;
  tenantSlug: string | null;
  resolve: (slug: string) => Promise<TenantSite | null>;
  fetchImpl?: typeof fetch;
  nowS?: number;
}

// POST /me/claim. Best effort, as before: null when the claim did not run or failed (a tenant that does not resolve,
// or a lookup that is down, skips the claim instead of claiming into the demo tenant; the next signed-in edit on the
// tenant's planner links its session anyway), else the number of trips claimed. `status` is for the log line.
export async function claimGuestTrips(
  call: AccountCall & { guestToken: string | null },
): Promise<{ claimed: number | null; status: number }> {
  const tenant = await tenantHeaders(call.tenantSlug, call.assertionKey, call.resolve, call.nowS);
  if (!tenant.ok) return { claimed: null, status: tenant.status };
  const upstream = await (call.fetchImpl ?? fetch)(`${call.tripApiUrl}/me/claim`, {
    method: "POST",
    headers: { ...tripHeaders(call.edgeKey, call.guestToken, call.requestId, call.accessToken), ...tenant.headers },
    body: "{}",
  });
  if (!upstream.ok) return { claimed: null, status: upstream.status };
  return { claimed: Number((await upstream.json())?.claimed ?? 0), status: upstream.status };
}

// GET /me/trips: the upstream answer, or the tenant step's refusal ({status, error}) when the tenant cannot be signed.
export async function fetchMeTrips(
  call: AccountCall,
): Promise<{ refused: { status: number; error: string } } | { upstream: Response }> {
  const tenant = await tenantHeaders(call.tenantSlug, call.assertionKey, call.resolve, call.nowS);
  if (!tenant.ok) return { refused: { status: tenant.status, error: tenant.error } };
  const upstream = await (call.fetchImpl ?? fetch)(`${call.tripApiUrl}/me/trips`, {
    method: "GET",
    headers: { ...tripHeaders(call.edgeKey, null, call.requestId, call.accessToken), ...tenant.headers },
  });
  return { upstream };
}
