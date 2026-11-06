// AA-1038 R5 slice 2 — the tenant of the planner page open in this tab (/{slug}/plan), on the client.
//
// The /{slug}/plan page passes its slug to PlannerApp, which sets it here once before the trip state loads. Then:
//   * every /api/trip call carries it in x-tp-tenant-slug (lib/api.ts); the BFF resolves and signs it (the browser's
//     slug is only a request: the Lambda trusts the BFF's signed assertion, never this header);
//   * the guest ids live under a key of their own per tenant, so a trip of one agency's planner is never sent to
//     another's (under another tenant the session is invisible and the Lambda answers 401). The bare /plan keeps the
//     key it always had.
// Only the browser sets it (a module value on the server would be shared by concurrent renders).

export const IDS_KEY = "aa-tripplanner:ids"; // guestIds.STORAGE_KEY, pinned equal by a test
export const TENANT_SLUG_HEADER = "x-tp-tenant-slug"; // lib/tenantSite.ts TENANT_SLUG_HEADER (server side)

let current: string | null = null;

export function setPlannerTenant(slug: string | null): void {
  if (typeof window === "undefined") return;
  current = slug || null;
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
