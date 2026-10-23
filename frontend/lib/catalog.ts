// AA-812 — server-side reads of the browse Lambda's catalog resources, for Server Components
// (home, tour pages, destination pages, sitemap). Runs only on the server: it uses
// BROWSE_API_URL and the shared secret directly, like the BFF route does. Responses are cached
// by Next (ISR) for CATALOG_REVALIDATE_S, matching the Lambda's CDN TTL, and dropped at once on a CIS tour lifecycle
// event (AA-987, tag CATALOG_CACHE_TAG, lib/catalogPurge.ts).
// Never import this from a Client Component (it reads server-only env vars).

import { CATALOG_CACHE_TAG } from "./catalogPurge.ts";
import type {
  CountrySummary,
  DestinationPage,
  SitemapData,
  TourDetail,
  TourList,
  TourSort,
} from "./catalogTypes";

export const CATALOG_REVALIDATE_S = 1800;

export type Result<T> =
  | { ok: true; data: T }
  | { ok: false; status: number; error: string };

// AA-833: a throttled browse Lambda (429), a busy API Gateway (503) or a dropped connection (502)
// is usually gone a few hundred ms later. One short retry keeps a single blip from becoming an
// error page; anything else (404, other 4xx, 500) returns at once.
const RETRY_STATUSES = new Set([429, 502, 503]);
export const RETRY_DELAY_MS = 400;

async function browseOnce<T>(url: string, headers: Record<string, string>): Promise<Result<T>> {
  try {
    const res = await fetch(url, {
      headers,
      // AA-987: tagged, so a CIS lifecycle event (POST /api/revalidate/catalog) drops it and every page that read it.
      next: { revalidate: CATALOG_REVALIDATE_S, tags: [CATALOG_CACHE_TAG] },
    });
    if (!res.ok) return { ok: false, status: res.status, error: `browse ${res.status}` };
    return { ok: true, data: (await res.json()) as T };
  } catch (e) {
    return { ok: false, status: 502, error: e instanceof Error ? e.message : "network error" };
  }
}

// Exported for lib/catalog.test.ts; pages use the typed getters below.
export async function browseGet<T>(path: string, retryDelayMs = RETRY_DELAY_MS): Promise<Result<T>> {
  const base = process.env.BROWSE_API_URL ?? "";
  if (!base) return { ok: false, status: 503, error: "BROWSE_API_URL not configured" };
  const headers: Record<string, string> = { accept: "application/json" };
  const key = process.env.TRIPPLANNER_API_KEY ?? "";
  if (key) headers["x-tripplanner-key"] = key;
  const first = await browseOnce<T>(`${base}${path}`, headers);
  if (first.ok || !RETRY_STATUSES.has(first.status)) return first;
  await new Promise((r) => setTimeout(r, retryDelayMs));
  // The extra header changes React's per-render fetch memoisation key, which would otherwise hand
  // back the same failed response. Next caches only 200 responses, so the failure is not stored.
  return browseOnce<T>(`${base}${path}`, { ...headers, "x-browse-retry": "1" });
}

export function getTours(q: {
  country?: string;
  min_days?: number;
  max_days?: number;
  month?: number;
  sort?: TourSort;
  limit?: number;
  offset?: number;
} = {}): Promise<Result<TourList>> {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(q)) if (v !== undefined && v !== "") p.set(k, String(v));
  const qs = p.toString();
  return browseGet<TourList>(`/browse/catalog/tours${qs ? `?${qs}` : ""}`);
}

export function getTour(slug: string): Promise<Result<TourDetail>> {
  return browseGet<TourDetail>(`/browse/catalog/tours/${encodeURIComponent(slug)}`);
}

export function getCountries(): Promise<Result<{ countries: CountrySummary[] }>> {
  return browseGet(`/browse/catalog/destinations`);
}

export function getDestination(slug: string): Promise<Result<DestinationPage>> {
  return browseGet<DestinationPage>(`/browse/catalog/destinations/${encodeURIComponent(slug)}`);
}

export function getSitemap(): Promise<Result<SitemapData>> {
  return browseGet<SitemapData>(`/browse/catalog/sitemap`);
}

// Canonical site origin for metadata, sitemap and JSON-LD.
export function siteUrl(): string {
  return (process.env.NEXT_PUBLIC_SITE_URL || "https://aa-tripplanner.vercel.app").replace(/\/$/, "");
}
