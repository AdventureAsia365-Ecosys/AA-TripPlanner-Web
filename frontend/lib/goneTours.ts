// AA-986 L6 (root ADR 0015 decision 5, Q5): a retired tour's page answers 410 Gone.
//
// Next.js has no "gone" for a page (only notFound = 404, and redirects), so the 410 comes from the request proxy
// (proxy.ts). The proxy keeps the slug suffixes of the retired tours (GET /browse/catalog/retired: the first 8 hex
// of the catalog source_id, the same suffix every tour slug ends with) in memory and refreshes them every
// CATALOG_REVALIDATE_S, so a tour page request costs a Set lookup, not a browse call. A suffix a live tour also has
// is never in the list (the browse Lambda leaves it out). Anything that fails (no BROWSE_API_URL, a timeout, a
// browse error, CIS 274 not applied) keeps the last list, or none: the page then answers as before (404 for a tour
// the catalog does not serve). Paused tours are not in the list: they stay 404, temporary (ADR 0015).
//
// No next/server import here, so `npm test` (node --test) can load it.

export const GONE_TTL_MS = 30 * 60 * 1000; // = CATALOG_REVALIDATE_S (lib/catalog.ts)
export const GONE_RETRY_MS = 60 * 1000; // after a failed refresh
export const GONE_FETCH_TIMEOUT_MS = 1500;

const TOUR_PATH = /^\/tours\/([a-z0-9-]{1,120})$/;

/** The source-id suffix of a tour page path ("/tours/hanoi-ha-long-vietnam-3f0c2a8e" → "3f0c2a8e"), else null. */
export function tourPathSuffix(pathname: string): string | null {
  const m = TOUR_PATH.exec(pathname.toLowerCase());
  if (!m) return null;
  const s = /-([0-9a-f]{8})$/.exec(m[1]);
  return s ? s[1] : null;
}

export type RetiredFetcher = () => Promise<string[] | null>;

/** The browse Lambda's list, or null when it cannot be read. */
export async function fetchRetiredSuffixes(): Promise<string[] | null> {
  const base = process.env.BROWSE_API_URL ?? "";
  if (!base) return null;
  const headers: Record<string, string> = { accept: "application/json" };
  const key = process.env.TRIPPLANNER_API_KEY ?? "";
  if (key) headers["x-tripplanner-key"] = key;
  try {
    const res = await fetch(`${base}/browse/catalog/retired`, {
      headers,
      cache: "no-store",
      signal: AbortSignal.timeout(GONE_FETCH_TIMEOUT_MS),
    });
    if (!res.ok) return null;
    const body = (await res.json()) as { suffixes?: unknown };
    return Array.isArray(body.suffixes)
      ? body.suffixes.filter((s): s is string => typeof s === "string" && /^[0-9a-f]{8}$/.test(s))
      : null;
  } catch {
    return null;
  }
}

/**
 * A per-instance cache of the retired suffixes, stale-while-revalidate: a request never waits for the browse call.
 * When the list is due, the request starts one background refresh (one at a time) and answers from the current list,
 * which is empty on an instance's first request. A failed refresh keeps the last list and retries after
 * GONE_RETRY_MS. Missing a 410 costs nothing: the tour page then answers 404 as before (review round, AA-986 L6).
 */
export function createGoneCache(fetcher: RetiredFetcher = fetchRetiredSuffixes) {
  let suffixes = new Set<string>();
  let nextRefresh = 0;
  let inflight: Promise<void> | null = null;

  async function refresh(now: number): Promise<void> {
    let list: string[] | null = null;
    try {
      list = await fetcher();
    } catch {
      list = null; // never an unhandled rejection: nobody awaits this promise on the request path
    }
    if (list) {
      suffixes = new Set(list);
      nextRefresh = now + GONE_TTL_MS;
    } else {
      nextRefresh = now + GONE_RETRY_MS;
    }
  }

  return {
    /** Answers from the list in memory; starts a background refresh when it is due, never awaits it. */
    isRetired(pathname: string, now: number = Date.now()): boolean {
      const suffix = tourPathSuffix(pathname);
      if (!suffix) return false;
      if (now >= nextRefresh) {
        inflight ??= refresh(now).finally(() => {
          inflight = null;
        });
      }
      return suffixes.has(suffix);
    },
    /** The running background refresh, if any (the proxy hands it to waitUntil so the platform lets it finish). */
    pending(): Promise<void> | null {
      return inflight;
    },
    /** Resolves when no refresh is running (tests). */
    async settled(): Promise<void> {
      while (inflight) await inflight;
    },
  };
}

/** The body of the 410 answer: a plain page that says the tour is no longer offered. Not indexed. */
export function gonePage(): string {
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Tour no longer offered — Adventure Asia</title>
<style>
:root{color-scheme:light dark;--bg:#fbfaf7;--ink:#1f2933;--muted:#52606d;--accent:#8a5a00}
@media (prefers-color-scheme:dark){:root{--bg:#14181d;--ink:#eef1f4;--muted:#b6c0ca;--accent:#e8b65a}}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:36rem;margin:0 auto;padding:4rem 16px}
h1{font-size:1.5rem;margin:0 0 .5rem}
p{color:var(--muted);margin:0 0 1.5rem}
a{color:var(--accent);font-weight:600;margin-right:1.25rem}
</style>
</head>
<body>
<main>
<h1>This tour is no longer offered</h1>
<p>Adventure Asia no longer runs this tour. Browse our other tours, or plan your own trip on the map.</p>
<a href="/">See all tours</a><a href="/plan">Plan a trip</a>
</main>
</body>
</html>
`;
}
