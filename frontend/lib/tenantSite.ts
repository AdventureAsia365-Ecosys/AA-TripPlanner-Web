// AA-1038 R5 slice 2 (root ADR 0019 d10) — which tenant a /{slug} path is. Server-only: it calls the browse Lambda
// (GET /browse/tenant/{slug}, CIS view catalog.v_tenant_tripplanner_v1) with the shared key, from the /{slug} pages
// and from the /api/trip BFF before it signs a tenant assertion (lib/tenantAssertion.ts).
//
// Only a SERVED tenant resolves (in service, tp.hosted live); unknown, add-on-less, suspended and badly shaped slugs
// are all null, so the page answers 404 and the BFF refuses the request. The bare paths (/plan, /api/trip without a
// tenant slug) never call this: they keep the default (demo) tenant, as before.
//
// Cache: per server instance, a served answer for TENANT_TTL_MS and a miss (a 404 from browse) for TENANT_MISS_TTL_MS,
// so a planner session costs one lookup a minute, not one per request, and a suspension or a new tenant shows within a
// minute. Any other answer (5xx, a network error, an unexpected status) is NOT a miss: resolve() throws
// TenantLookupUnavailable, nothing is cached, and the caller shows a retryable error (the pages: app/[tenant]/error.tsx;
// the BFF: 503), never the 404 page. No next/* import, so `npm test` runs it.

import { isTenantSlug } from "./tenantSlug.ts";

export { RESERVED_TENANT_SLUGS, TENANT_SLUG_RE, isTenantSlug } from "./tenantSlug.ts";

export const TENANT_TTL_MS = 60_000;
export const TENANT_MISS_TTL_MS = 30_000;
export const TENANT_CACHE_MAX = 500;
// The planner's own requests name the page's tenant in this header (lib/plannerTenant.ts); the BFF reads it.
export const TENANT_SLUG_HEADER = "x-tp-tenant-slug";

export interface TenantSite {
  tenant_id: string;
  slug: string;
  name: string;
  kind: string;
  launcher: boolean;
  branding_badge: boolean;
  theme: Record<string, string>;
  locale: string;
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

function asTenant(body: unknown, slug: string): TenantSite | null {
  const t = (body as { tenant?: Partial<TenantSite> } | null)?.tenant;
  if (!t || typeof t.tenant_id !== "string" || !UUID_RE.test(t.tenant_id) || t.slug !== slug) return null;
  return {
    tenant_id: t.tenant_id,
    slug: t.slug,
    name: typeof t.name === "string" && t.name ? t.name : slug,
    kind: typeof t.kind === "string" ? t.kind : "agency",
    launcher: t.launcher === true,
    branding_badge: t.branding_badge === true,
    theme: t.theme && typeof t.theme === "object" ? (t.theme as Record<string, string>) : {},
    locale: t.locale === "vi" ? "vi" : "en",
  };
}

export type TenantFetch = (slug: string) => Promise<{ status: number; body: unknown }>;

// The lookup could not answer (browse 5xx, a network error, an unexpected status): unknown, not "no such tenant".
export class TenantLookupUnavailable extends Error {
  readonly status: number;
  constructor(status: number) {
    super(`tenant lookup unavailable (${status})`);
    this.name = "TenantLookupUnavailable";
    this.status = status;
  }
}

// The browse call itself (the default fetcher of resolveTenant).
export const browseTenantFetch: TenantFetch = async (slug) => {
  const base = process.env.BROWSE_API_URL ?? "";
  // No browse API on this deployment (a bare local `next dev`): no tenant can be served here, so a plain 404.
  if (!base) return { status: 404, body: null };
  const headers: Record<string, string> = { accept: "application/json" };
  const key = process.env.TRIPPLANNER_API_KEY ?? "";
  if (key) headers["x-tripplanner-key"] = key;
  try {
    const res = await fetch(`${base}/browse/tenant/${encodeURIComponent(slug)}`, { headers, cache: "no-store" });
    return { status: res.status, body: res.ok ? await res.json() : null };
  } catch {
    return { status: 502, body: null };
  }
};

export interface TenantResolver {
  // The served tenant, null when there is none (cached), or TenantLookupUnavailable thrown (not cached).
  resolve(slug: string): Promise<TenantSite | null>;
  clear(): void;
}

export function createTenantResolver(fetchTenant: TenantFetch, now: () => number = Date.now): TenantResolver {
  const cache = new Map<string, { site: TenantSite | null; until: number }>();
  return {
    async resolve(slug) {
      if (!isTenantSlug(slug)) return null;
      const t = now();
      const hit = cache.get(slug);
      if (hit && hit.until > t) return hit.site;
      const { status, body } = await fetchTenant(slug);
      // Only a 200 or a 404 is an answer; anything else (5xx, network = 502, 401 / 403 of a wrong key) is not cached
      // and is not "unknown tenant": the next request asks again.
      if (status !== 200 && status !== 404) throw new TenantLookupUnavailable(status);
      const site = status === 200 ? asTenant(body, slug) : null;
      if (cache.size >= TENANT_CACHE_MAX) cache.delete(cache.keys().next().value as string);
      cache.set(slug, { site, until: t + (site ? TENANT_TTL_MS : TENANT_MISS_TTL_MS) });
      return site;
    },
    clear() {
      cache.clear();
    },
  };
}

// One resolver per server instance.
const shared = createTenantResolver(browseTenantFetch);

export function resolveTenant(slug: string): Promise<TenantSite | null> {
  return shared.resolve(slug);
}
