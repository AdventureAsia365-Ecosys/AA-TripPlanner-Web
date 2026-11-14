// AA-1090 TripPlanner R8 — an agency's own domain (trip.<agency domain>) serves that agency's planner.
//
// The CIS job attaches the host to our agency CloudFront distribution (an alternate domain name with its certificate)
// and AA-CIS-Infra routes every alias of it to this service, so the request reaches proxy.ts with the
// agency's Host. Here:
//   * isOwnHost: our public host (NEXT_PUBLIC_SITE_URL), localhost, an IP literal or a one-label name (the container
//     and target-group health checks) are ours: the request is handled exactly as before;
//   * any other host is looked up (browse GET /browse/tenant-host/{host}, CIS 428 custom_domains): a served tenant's
//     ACTIVE host resolves to its TenantSite, anything else is a 404 (an unknown host never shows AA's site);
//   * tenantHostPath maps the path on that host to the tenant's pages: "/" -> "/{slug}", "/plan..." ->
//     "/{slug}/plan...", "/tours/{tour}" -> "/{slug}/tours/{tour}"; "/{slug}/..." (our path) keeps working; the shared
//     pages (sign-in, trips, privacy, terms), the BFF and static files pass; AA's own catalogue pages and another
//     tenant's path are a 404 on an agency's domain.
// Cache: per server instance, like the slug resolver (lib/tenantSite.ts): a served answer for TENANT_TTL_MS, a miss for
// TENANT_MISS_TTL_MS; a lookup that could not answer throws TenantLookupUnavailable and is not cached. No next/*
// import, so `npm test` runs it.

import {
  TENANT_CACHE_MAX,
  TENANT_MISS_TTL_MS,
  TENANT_TTL_MS,
  TenantLookupUnavailable,
  asTenant,
  type TenantSite,
} from "./tenantSite.ts";
import { isTenantSlug } from "./tenantSlug.ts";

// The CIS 427 host shape (lower-case labels, a letter-led last label), at most 253 characters.
const HOST_RE = /^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]([a-z0-9-]{0,61}[a-z0-9])?$/;
const IPV4_RE = /^\d{1,3}(\.\d{1,3}){3}$/;

// Paths an agency's domain serves unchanged: the BFF, build chunks, the photo proxy, public files and the pages every
// planner shares (sign-in and My Trips carry the tenant in ?next= / ?tenant=; the legal pages are the platform's).
const PASS_PREFIXES = ["/api/", "/_next/", "/photo-cdn/", "/brand/", "/hero/"];
const PASS_PATHS = new Set(["/sign-in", "/trips", "/privacy", "/terms", "/icon.svg", "/favicon.ico", "/robots.txt",
  "/sitemap.xml"]);
// Paths that become the tenant's own pages.
const TENANT_PREFIXES = ["/plan", "/tours"];

/** The request's host: lower-case, without a port or a trailing root dot ("" when absent). */
export function requestHost(raw: string | null | undefined): string {
  const first = (raw ?? "").split(",")[0].trim().toLowerCase();
  if (first.startsWith("[")) return first.slice(0, first.indexOf("]") + 1);   // an IPv6 literal, kept as is
  return first.split(":")[0].replace(/\.+$/, "");
}

export function siteHost(siteUrl: string | undefined): string {
  try {
    return siteUrl ? new URL(siteUrl).host.toLowerCase() : "";
  } catch {
    return "";
  }
}

/** Ours: the public host, localhost, an IP literal, a one-label name, or no host at all. */
export function isOwnHost(host: string, ownHost: string): boolean {
  if (!host || host === ownHost || host === "localhost" || host.endsWith(".localhost")) return true;
  if (host.startsWith("[") || IPV4_RE.test(host)) return true;
  return !host.includes(".");
}

export type HostPathDecision = { kind: "pass" } | { kind: "rewrite"; path: string } | { kind: "not_found" };

function passes(pathname: string, slug: string): boolean {
  return PASS_PATHS.has(pathname) || PASS_PREFIXES.some((p) => pathname.startsWith(p))
    || pathname === `/${slug}` || pathname.startsWith(`/${slug}/`);
}

// The tenant page a path becomes on the agency's domain, or null (AA's own pages, /tours alone, another tenant).
function tenantTarget(pathname: string, slug: string): string | null {
  if (pathname === "/" || pathname === "") return `/${slug}`;
  const tenantPage = pathname === "/plan" || TENANT_PREFIXES.some((p) => pathname.startsWith(`${p}/`));
  return tenantPage ? `/${slug}${pathname}` : null;
}

/** What the path means on the tenant's own domain (see the header). */
export function tenantHostPath(pathname: string, slug: string): HostPathDecision {
  if (passes(pathname, slug)) return { kind: "pass" };
  const target = tenantTarget(pathname, slug);
  return target ? { kind: "rewrite", path: target } : { kind: "not_found" };
}

// The answer names its tenant; it is read like a slug answer for that slug (lib/tenantSite.ts asTenant).
function asHostTenant(body: unknown): TenantSite | null {
  const slug = (body as { tenant?: { slug?: unknown } } | null)?.tenant?.slug;
  return isTenantSlug(slug) ? asTenant(body, slug) : null;
}

export type TenantHostFetch = (host: string) => Promise<{ status: number; body: unknown }>;

export const browseTenantHostFetch: TenantHostFetch = async (host) => {
  const base = process.env.BROWSE_API_URL ?? "";
  if (!base) return { status: 404, body: null };
  const headers: Record<string, string> = { accept: "application/json" };
  const key = process.env.TRIPPLANNER_API_KEY ?? "";
  if (key) headers["x-tripplanner-key"] = key;
  try {
    const res = await fetch(`${base}/browse/tenant-host/${encodeURIComponent(host)}`, { headers, cache: "no-store" });
    return { status: res.status, body: res.ok ? await res.json() : null };
  } catch {
    return { status: 502, body: null };
  }
};

export interface TenantHostResolver {
  resolve(host: string): Promise<TenantSite | null>;
  clear(): void;
}

export function createTenantHostResolver(fetchHost: TenantHostFetch, now: () => number = Date.now): TenantHostResolver {
  const cache = new Map<string, { site: TenantSite | null; until: number }>();
  return {
    async resolve(host) {
      if (!HOST_RE.test(host)) return null;
      const t = now();
      const hit = cache.get(host);
      if (hit && hit.until > t) return hit.site;
      const { status, body } = await fetchHost(host);
      if (status !== 200 && status !== 404) throw new TenantLookupUnavailable(status);
      const site = status === 200 ? asHostTenant(body) : null;
      if (cache.size >= TENANT_CACHE_MAX) cache.delete(cache.keys().next().value as string);
      cache.set(host, { site, until: t + (site ? TENANT_TTL_MS : TENANT_MISS_TTL_MS) });
      return site;
    },
    clear() {
      cache.clear();
    },
  };
}

const shared = createTenantHostResolver(browseTenantHostFetch);

export function resolveTenantHost(host: string): Promise<TenantSite | null> {
  return shared.resolve(host);
}

// The plain pages proxy.ts answers itself on an agency's domain (no AA branding, nothing cached by a shared cache).
export function hostNotFoundPage(): string {
  return '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="robots" content="noindex">'
    + "<title>Page not found</title></head><body><h1>Page not found</h1>"
    + "<p>This page does not exist on this site.</p></body></html>";
}

export function hostUnavailablePage(): string {
  return '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="robots" content="noindex">'
    + "<title>Temporarily unavailable</title></head><body><h1>Temporarily unavailable</h1>"
    + "<p>Please reload the page in a moment.</p></body></html>";
}
