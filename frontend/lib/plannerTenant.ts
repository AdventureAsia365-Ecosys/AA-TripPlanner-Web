// AA-1038 R5 slice 2 — the tenant of the planner page open in this tab (/{slug}/plan), on the client.
//
// The /{slug}/plan page passes its slug to PlannerApp, which sets it here once before the trip state loads. Then:
//   * every /api/trip call carries it in x-tp-tenant-slug (lib/api.ts); the BFF resolves and signs it (the browser's
//     slug is only a request: the Lambda trusts the BFF's signed assertion, never this header);
//   * the guest ids live under a key of their own per tenant, so a trip of one agency's planner is never sent to
//     another's (under another tenant the session is invisible and the Lambda answers 401). The bare /plan keeps the
//     key it always had.
// Only the browser sets it (a module value on the server would be shared by concurrent renders).

import { isTenantSlug } from "./tenantSlug.ts";

export const IDS_KEY = "aa-tripplanner:ids"; // guestIds.STORAGE_KEY, pinned equal by a test
export const TENANT_SLUG_HEADER = "x-tp-tenant-slug"; // lib/tenantSite.ts TENANT_SLUG_HEADER (server side)

let current: string | null = null;
// AA-1038 slice 3: what the planner shows of the agency (its trading name on the lead form, the BO-8 badge flag).
export interface PlannerAgency {
  name: string;
  brandingBadge: boolean;
}
let agency: PlannerAgency | null = null;

export function setPlannerTenant(slug: string | null, site: PlannerAgency | null = null): void {
  if (typeof window === "undefined") return;
  current = slug || null;
  agency = slug ? site : null;
}

export function plannerAgency(): PlannerAgency | null {
  return agency;
}

export function plannerTenant(): string | null {
  return current;
}

export function idsStorageKey(slug: string | null = current): string {
  return slug ? `${IDS_KEY}:${slug}` : IDS_KEY;
}

export function tenantRequestHeaders(slug: string | null = current): Record<string, string> {
  return slug ? { [TENANT_SLUG_HEADER]: slug } : {};
}

// fetch() for the /api/trip BFF with the tenant header added (a no-op on the bare path).
export function tenantFetch(input: string, init: RequestInit = {}): Promise<Response> {
  const extra = tenantRequestHeaders();
  if (!Object.keys(extra).length) return fetch(input, init);
  return fetch(input, { ...init, headers: { ...(init.headers as Record<string, string> | undefined), ...extra } });
}

// The tenant a same-site path belongs to: "/{slug}" or "/{slug}/..." (a servable slug shape, lib/tenantSlug.ts), or a
// "?tenant={slug}" on any path (My Trips opened from a tenant's planner, /trips?tenant={slug}). Null for the bare
// paths. Used for the account calls (sign-in claim, My Trips) so they carry the same x-tp-tenant-slug as the planner;
// the BFF resolves and signs it, so a wrong value only ever reaches a 404 or the demo tenant's view of nothing.
export function tenantOfPath(path: string | null | undefined): string | null {
  if (typeof path !== "string" || !path.startsWith("/") || path.startsWith("//")) return null;
  const [pathname, query = ""] = path.split("?", 2);
  const first = pathname.split("/")[1] ?? "";
  if (isTenantSlug(first)) return first;
  const fromQuery = new URLSearchParams(query.split("#")[0]).get("tenant");
  return isTenantSlug(fromQuery) ? fromQuery : null;
}

// Sign-out forgets every trip id this browser holds: the bare key and each tenant's key (aa-tripplanner:ids:<slug>).
export function clearGuestIdKeys(storage: Pick<Storage, "length" | "key" | "removeItem">): void {
  const keys: string[] = [];
  for (let i = 0; i < storage.length; i += 1) {
    const k = storage.key(i);
    if (k === IDS_KEY || (k !== null && k.startsWith(`${IDS_KEY}:`))) keys.push(k);
  }
  for (const k of keys) storage.removeItem(k);
}

// The account links of the planner header (components/AccountLink.tsx): on a tenant's planner they keep the tenant,
// so the sign-in claims that tenant's trips and My Trips lists them; on the bare paths they are unchanged.
export function accountLinks(slug: string | null = current): { signIn: string; trips: string } {
  if (!slug) return { signIn: "/sign-in", trips: "/trips" };
  return { signIn: `/sign-in?next=${encodeURIComponent(`/${slug}/plan`)}`, trips: `/trips?tenant=${slug}` };
}

// Where My Trips sends a traveller (components/MyTrips.tsx) for the tenant named in its URL (?tenant=) or none.
export function myTripsTargets(slug: string | null): { idsKey: string; planner: string; signIn: string } {
  if (!slug) return { idsKey: IDS_KEY, planner: "/plan", signIn: "/sign-in?next=/trips" };
  return {
    idsKey: idsStorageKey(slug),
    planner: `/${slug}/plan`,
    signIn: `/sign-in?next=${encodeURIComponent(`/trips?tenant=${slug}`)}`,
  };
}
