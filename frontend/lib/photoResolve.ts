// AA-995 (spec §6.1 phase 1, risk R1) — catalog photo URLs (`<CIS API>/content/photos/<id>?size=large|small`) answer
// with a 302 to the image CDN, which costs the LCP image a full API Gateway round trip in the browser. On the server,
// while the page renders (ISR), the hero image's URL is resolved once to its CDN location, so the browser asks the CDN
// directly. The LCP image of a page is resolved, and since S257 the other photos fetched during page load on the
// destination pages too (resolvePhotoMap); every other photo keeps the catalog URL (lazy, below the fold).
//
// Only a target that is safe to keep in the ISR page is used. The CDN branch of CIS `/content/photos/{id}` answers with a
// versioned file URL (`…/<file>-1600.webp?v=<hash>`, `cache-control: public, max-age=86400`). Its other branch (no CDN:
// `PHOTO_CDN_BASE=off`, or an S3 key outside the CDN prefix) answers with a 1-hour presigned S3 URL (`max-age=3000`);
// stale-while-revalidate can serve the page hours later, so that URL would be an expired, broken LCP image. A Location
// carrying presign parameters, or a redirect cached for less than a day, is therefore refused (AA-995 review). Any
// refusal or failure (timeout, non-redirect answer, odd Location) falls back to the catalog URL, which still works
// through the 302: the page never loses its photo because of this step.
// Pure helpers first (unit-tested in lib/photoResolve.test.ts), then the one server function.

export type PhotoSize = "small" | "medium" | "large";

// The same catalog photo at another size (the browse API sends `?size=large` covers; small = 600 w, large = 1600 w).
// "medium" (960 w) is not served by CIS yet (it answers 422): resolveHeroPhoto asks for it and uses it only when the
// catalog redirects it to a file, so the rendition is picked up without a TripPlanner change once CIS stores it.
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

// A redirect must be cacheable for at least this long to be kept in the page (the CDN branch sends one day).
export const MIN_REDIRECT_MAX_AGE_S = 86400;

// Query parameters of a presigned (expiring) S3 / SigV4 URL, compared case-insensitively.
const PRESIGN_PARAMS = ["x-amz-signature", "x-amz-expires", "x-amz-credential", "x-amz-security-token", "signature", "expires"];

// The max-age (or s-maxage) of a Cache-Control header in seconds; null when absent, 0 for no-store / no-cache / private.
export function cacheMaxAge(cacheControl: string | null | undefined): number | null {
  if (!cacheControl) return null;
  const parts = cacheControl.toLowerCase().split(",").map((p) => p.trim());
  if (parts.some((p) => p === "no-store" || p === "no-cache" || p === "private")) return 0;
  const ages = parts
    .map((p) => /^(?:s-maxage|max-age)\s*=\s*"?(\d+)"?$/.exec(p))
    .filter((m): m is RegExpExecArray => m !== null)
    .map((m) => Number(m[1]));
  return ages.length ? Math.max(...ages) : null;
}

// The redirect target to use, or null to keep the catalog URL: only a 301/302/303/307/308 with an absolute https
// Location that is not a presigned URL, sent with a Cache-Control max-age of at least a day.
export function redirectTarget(status: number, location: string | null, cacheControl: string | null = null): string | null {
  if (![301, 302, 303, 307, 308].includes(status) || !location) return null;
  const maxAge = cacheMaxAge(cacheControl);
  if (maxAge === null || maxAge < MIN_REDIRECT_MAX_AGE_S) return null;
  try {
    const u = new URL(location);
    if (u.protocol !== "https:") return null;
    const keys = [...u.searchParams.keys()].map((k) => k.toLowerCase());
    if (keys.some((k) => PRESIGN_PARAMS.includes(k))) return null;
    return u.toString();
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

// AA-995 S256 (mobile LCP) — the LCP photo is served from the page's own origin. A CDN file URL is rewritten to
// `/photo-cdn/<path>?<query>`, which next.config.mjs proxies to the CDN (Vercel external rewrite, cached at the edge on
// the CDN's own `max-age`, the file URL being versioned by `?v=`). The browser then fetches the LCP image over the
// HTTP/2 connection it already has for the HTML instead of opening a new one (DNS + TCP + TLS) to the CDN host:
// measured with Lighthouse mobile on a local production build, see the root audit 2026-10-28-S256-aa995-lcp.
// Only the CDN host is proxied (next.config.mjs keeps the same constant; lib/photoResolve.test.ts checks it), and
// only for the photos this module resolves (the LCP photo, and resolvePhotoMap's set); every other photo keeps its
// catalog URL.
export const PHOTO_CDN_ORIGIN = "https://img.lumiguides.it.com";
export const PHOTO_PROXY_PREFIX = "/photo-cdn";

// The same-origin path of a CDN file URL; any other URL (catalog URL, another host, not a URL) is returned as is.
export function sameOriginPhotoUrl(url: string, cdnOrigin: string = PHOTO_CDN_ORIGIN): string {
  try {
    const u = new URL(url);
    if (u.origin !== cdnOrigin) return url;
    return `${PHOTO_PROXY_PREFIX}${u.pathname}${u.search}`;
  } catch {
    return url;
  }
}

export const RESOLVE_TIMEOUT_MS = 1500;

// Server only (called from Server Components while the ISR page renders). Default fetch caching: the result lives in
// the rendered page, so it is fetched once per page revalidation (no `cache: "no-store"`, which would make the route
// dynamic).
export async function resolvePhotoUrl(url: string | null | undefined): Promise<string | null> {
  if (!url) return null;
  return (await resolveRedirect(url)) ?? url;
}

// The CDN file a catalog photo URL redirects to, or null (no redirect, a refused target, an error or a timeout).
// `signal` replaces the per-call timeout (resolvePhotoMap shares one deadline between its lookups).
export async function resolveRedirect(url: string, signal?: AbortSignal): Promise<string | null> {
  try {
    const res = await fetch(url, { redirect: "manual", signal: signal ?? AbortSignal.timeout(RESOLVE_TIMEOUT_MS) });
    // The body of a redirect is empty or tiny; release the connection either way.
    res.body?.cancel().catch(() => {});
    return redirectTarget(res.status, res.headers.get("location"), res.headers.get("cache-control"));
  } catch {
    return null;
  }
}

// S257 (mobile LCP, follow-up of AA-995) — every photo the browser may fetch while the page is still loading, not only
// the LCP one. Production Lighthouse (S256, prod-after) showed the cost of the photos left on the catalog URL: the
// second-row tiles of /destinations and the first tour card of a destination page are fetched at once (in the viewport
// or inside the browser's lazy-load distance), each as an API Gateway round trip (~0.6 s observed) for the 302, then a
// new connection to the CDN host. When the first paint came late, Lighthouse counted that chain before the LCP: the
// same page measured 1.4 s or 3.4 s (LCP "render delay" 0.05 s or 2.5 s). Served same-origin from the resolved file,
// those photos ride the HTML's HTTP/2 connection and the Vercel CDN cache like the LCP photo.
//
// Resolves up to RESOLVE_MAX distinct catalog URLs in parallel under ONE shared deadline (RESOLVE_TIMEOUT_MS), so a
// slow or failing catalog costs the ISR render at most that long, never one timeout per photo. Answer: catalog URL ->
// same-origin URL (or another resolved https URL), only for the lookups that succeeded; the caller keeps the catalog
// URL for every other photo (the 302 still works). Same refusal rules as resolvePhotoUrl (redirectTarget).
export const RESOLVE_MAX = 24;

// The distinct non-empty URLs, in first-seen order, at most `max`.
export function uniquePhotoUrls(urls: Iterable<string | null | undefined>, max: number = RESOLVE_MAX): string[] {
  const out: string[] = [];
  for (const u of urls) {
    if (out.length >= max) break;
    if (u && !out.includes(u)) out.push(u);
  }
  return out;
}

export async function resolvePhotoMap(
  urls: Iterable<string | null | undefined>,
  max: number = RESOLVE_MAX,
): Promise<Map<string, string>> {
  const list = uniquePhotoUrls(urls, max);
  const out = new Map<string, string>();
  if (list.length === 0) return out;
  const deadline = AbortSignal.timeout(RESOLVE_TIMEOUT_MS);
  const targets = await Promise.all(list.map((u) => resolveRedirect(u, deadline)));
  list.forEach((u, i) => {
    const t = targets[i];
    if (t) out.set(u, sameOriginPhotoUrl(t));
  });
  return out;
}

export interface HeroPhoto {
  src: string;
  srcSet: string | null;
  preconnect: string | null;
}

// A page hero from one catalog photo URL: the variants resolved in parallel, as a srcset (600 w / [960 w] / 1600 w;
// at 390 @2x the browser takes the large one). 960 w only when the catalog redirects `size=medium` to a file (see
// sizedPhotoUrl). Resolved CDN files are served same-origin (sameOriginPhotoUrl); a URL that stays on another host
// (the catalog URL after a failed lookup) keeps a preconnect.
export async function resolveHeroPhoto(url: string | null | undefined): Promise<HeroPhoto | null> {
  const large = sizedPhotoUrl(url, "large");
  const small = sizedPhotoUrl(url, "small");
  const medium = sizedPhotoUrl(url, "medium");
  if (!large) return null;
  const [l, s, m] = await Promise.all([resolvePhotoUrl(large), resolvePhotoUrl(small), medium ? resolveRedirect(medium) : null]);
  const src = sameOriginPhotoUrl(l ?? large);
  const smallSrc = s ? sameOriginPhotoUrl(s) : null;
  const mediumSrc = m ? sameOriginPhotoUrl(m) : null;
  return {
    src,
    srcSet: smallSrc ? [`${smallSrc} 600w`, ...(mediumSrc ? [`${mediumSrc} 960w`] : []), `${src} 1600w`].join(", ") : null,
    preconnect: src.startsWith("/") ? null : preconnectOrigin(src, large),
  };
}

// The URLs of a hero's files (its src and every srcset candidate).
export function heroFiles(hero: HeroPhoto | null | undefined): string[] {
  if (!hero) return [];
  const set = (hero.srcSet ?? "").split(",").map((c) => c.trim().split(/\s+/)[0]).filter(Boolean);
  return [hero.src, ...set];
}

// AA-995 S258 (mobile LCP) — the hero itself when a card under it shows the very same file (the destination cover is
// usually the first tour's cover: Bhutan's hero 960 w and its first card's 600 w were the same photo, 46 KB more in the
// first screen). Such a card then renders the hero's src + srcset with the hero's `sizes`, so the browser picks the
// file it already has for the hero (memory cache) instead of a second size of the same photo. Compared on the
// resolved file URLs, not the catalog ids (one file can sit behind several photo ids). null = an ordinary card.
export function sameFileAsHero(cardPhoto: string | null | undefined, hero: HeroPhoto | null | undefined): HeroPhoto | null {
  if (!cardPhoto || !hero) return null;
  return heroFiles(hero).includes(cardPhoto) ? hero : null;
}
