// TL-24 (S262, tech lead decision on the S261 architecture review): until go-live the Trip Planner is a preview.
// Every deployment, the Dev host https://aa-tripplanner.vercel.app included, asks search engines not to index it
// (robots meta in the root layout, an X-Robots-Tag header from next.config.mjs, no sitemap line in robots.txt) and
// shows a small "Preview site" line in the footer. Only a deployment whose Vercel env sets SITE_INDEXING=on is the
// live site. The Dev host is a Vercel *production* deployment, so VERCEL_ENV cannot tell the two apart.
// next.config.mjs repeats isLiveSite() in plain JS (it cannot import TypeScript); lib/siteMode.test.ts keeps them equal.
import type { MetadataRoute } from "next";

/** The Vercel env var that marks the live site. Any value other than "on" (or no value) means preview. */
export const SITE_INDEXING_ENV = "SITE_INDEXING";

/** The footer line on a preview deployment (English UI; the site has no other language yet). */
export const PREVIEW_SITE_NOTE =
  "Preview site: a test version of the Adventure Asia trip planner. Trips and requests sent here are not handled as real enquiries.";

/** The X-Robots-Tag value of a preview deployment. */
export const PREVIEW_ROBOTS_HEADER = "noindex, nofollow";

type Env = Record<string, string | undefined>;

/** True only when the deployment is marked as the live site (`SITE_INDEXING=on`, case and spaces ignored). */
export function isLiveSite(env: Env = process.env): boolean {
  return (env[SITE_INDEXING_ENV] ?? "").trim().toLowerCase() === "on";
}

/** The root layout's `robots` metadata: nothing on the live site (pages decide), noindex + nofollow on a preview. */
export function layoutRobots(live: boolean): { index: false; follow: false } | undefined {
  return live ? undefined : { index: false, follow: false };
}

/**
 * robots.txt. Live: the crawl rules and the sitemap. Preview: the same crawl rules but no sitemap, so crawlers can
 * still fetch the pages and read their noindex (a Disallow would hide the noindex and leave known URLs listed).
 */
export function robotsFor(live: boolean, baseUrl: string): MetadataRoute.Robots {
  const rules = [{ userAgent: "*", allow: "/", disallow: ["/api/", "/plan"] }];
  return live ? { rules, sitemap: `${baseUrl}/sitemap.xml` } : { rules };
}
