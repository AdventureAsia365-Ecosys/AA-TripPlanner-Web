// AA-812 — server-side reads of the browse Lambda's catalog resources, for Server Components
// (home, tour pages, destination pages, sitemap). Runs only on the server: it uses
// BROWSE_API_URL and the shared secret directly, like the BFF route does. Responses are cached
// by Next (ISR) for CATALOG_REVALIDATE_S, matching the Lambda's CDN TTL.
// Never import this from a Client Component (it reads server-only env vars).

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

async function browseGet<T>(path: string): Promise<Result<T>> {
  const base = process.env.BROWSE_API_URL ?? "";
  if (!base) return { ok: false, status: 503, error: "BROWSE_API_URL not configured" };
  const headers: Record<string, string> = { accept: "application/json" };
  const key = process.env.TRIPPLANNER_API_KEY ?? "";
  if (key) headers["x-tripplanner-key"] = key;
  try {
    const res = await fetch(`${base}${path}`, {
      headers,
      next: { revalidate: CATALOG_REVALIDATE_S },
    });
    if (!res.ok) return { ok: false, status: res.status, error: `browse ${res.status}` };
    return { ok: true, data: (await res.json()) as T };
  } catch (e) {
    return { ok: false, status: 502, error: e instanceof Error ? e.message : "network error" };
  }
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
