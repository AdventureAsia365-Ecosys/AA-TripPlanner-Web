// AA-995 (spec §6.1 phase 1, risk R1) — catalog photo URLs (`<CIS API>/content/photos/<id>?size=large|small`) answer
// with a 302 to the image CDN, which costs the LCP image a full API Gateway round trip in the browser. On the server,
// while the page renders (ISR), the hero image's URL is resolved once to its CDN location, so the browser asks the CDN
// directly. Only the LCP image of a page is resolved; every other photo keeps the catalog URL (lazy, below the fold).
//
// The CDN location is a stable, versioned file URL (`…/<file>-1600.webp?v=<hash>`, cache-control one day), so caching it
// in the ISR page for the page's lifetime is safe. Any failure (timeout, non-redirect answer, odd Location) falls back to
// the catalog URL, which still works through the 302: the page never loses its photo because of this step.
// Pure helpers first (unit-tested in lib/photoResolve.test.ts), then the one server function.

export type PhotoSize = "small" | "large";

// The same catalog photo at another size (the browse API sends `?size=large` covers; small = 600 w, large = 1600 w).
export function sizedPhotoUrl(url: string | null | undefined, size: PhotoSize): string | null {
  if (!url) return null;
  try {
    const u = new URL(url);
    u.searchParams.set("size", size);
    return u.toString();
  } catch {
    return url;
  }
}

// The redirect target to use, or null to keep the catalog URL: only a 301/302/303/307/308 with an absolute https
// Location counts.
export function redirectTarget(status: number, location: string | null): string | null {
  if (![301, 302, 303, 307, 308].includes(status) || !location) return null;
  try {
    const u = new URL(location);
    return u.protocol === "https:" ? u.toString() : null;
  } catch {
    return null;
  }
}

// The origin to preconnect to for a resolved URL (null when it did not resolve to another host).
export function preconnectOrigin(resolved: string | null | undefined, original: string | null | undefined): string | null {
  if (!resolved || !original || resolved === original) return null;
  try {
    const a = new URL(resolved);
    return a.origin === new URL(original).origin ? null : a.origin;
  } catch {
    return null;
  }
}

export const RESOLVE_TIMEOUT_MS = 1500;

// Server only (called from Server Components while the ISR page renders). Default fetch caching: the result lives in
// the rendered page, so it is fetched once per page revalidation (no `cache: "no-store"`, which would make the route
// dynamic).
export async function resolvePhotoUrl(url: string | null | undefined): Promise<string | null> {
  if (!url) return null;
  try {
    const res = await fetch(url, { redirect: "manual", signal: AbortSignal.timeout(RESOLVE_TIMEOUT_MS) });
    // The body of a redirect is empty or tiny; release the connection either way.
    res.body?.cancel().catch(() => {});
    return redirectTarget(res.status, res.headers.get("location")) ?? url;
  } catch {
    return url;
  }
}

export interface HeroPhoto {
  src: string;
  srcSet: string | null;
  preconnect: string | null;
}

// A page hero from one catalog photo URL: the large and small variants resolved in parallel, as a srcset
// (600 w / 1600 w; at 390 @2x the browser takes the large one).
export async function resolveHeroPhoto(url: string | null | undefined): Promise<HeroPhoto | null> {
  const large = sizedPhotoUrl(url, "large");
  const small = sizedPhotoUrl(url, "small");
  if (!large) return null;
  const [l, s] = await Promise.all([resolvePhotoUrl(large), resolvePhotoUrl(small)]);
  const src = l ?? large;
  return {
    src,
    srcSet: s ? `${s} 600w, ${src} 1600w` : null,
    preconnect: preconnectOrigin(src, large),
  };
}
