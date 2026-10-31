// AA-876 — security headers on every response (same list as the CIS admin and portal apps). The CSP is
// deliberately minimal (no script-src / style-src yet: Next inline scripts and inline styles); it only
// stops framing, <base> hijacking, foreign form posts and plugins. `frame-ancestors 'none'` also means
// no partner can iframe TripPlanner until an allow-list is designed (KAN-20 B2B).
const SECURITY_HEADERS = [
  { key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "X-Frame-Options", value: "DENY" },
  {
    key: "Content-Security-Policy",
    value: "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'",
  },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
];

// AA-995 S256 (mobile LCP) — the LCP photo of a page is served from this origin (lib/photoResolve.ts
// sameOriginPhotoUrl): /photo-cdn/<path> proxies to the photo CDN, so the browser reuses the HTML connection instead of
// opening one to another host. Keep PHOTO_CDN_ORIGIN / PHOTO_PROXY_PREFIX equal to lib/photoResolve.ts
// (lib/photoResolve.test.ts reads this file). x-vercel-enable-rewrite-caching=1: the Vercel CDN caches the proxied
// file on the CDN's own Cache-Control (public, max-age one year; the file URL is versioned by ?v=), so the CDN is
// asked once per file and region, not once per visit.
const PHOTO_CDN_ORIGIN = "https://img.lumiguides.it.com";

// TL-24 (S262) — until go-live every deployment is a preview: X-Robots-Tag noindex on every response unless the Vercel env
// sets SITE_INDEXING=on. Same rule as isLiveSite() / PREVIEW_ROBOTS_HEADER in lib/siteMode.ts (lib/siteMode.test.ts
// checks both). Read when Next loads the config (build and server start), like any other env-driven header.
/** @param {Record<string, string | undefined>} [env] */
export function previewHeaders(env = process.env) {
  const live = (env.SITE_INDEXING ?? "").trim().toLowerCase() === "on";
  return live ? [] : [{ key: "X-Robots-Tag", value: "noindex, nofollow" }];
}
const PHOTO_PROXY_PREFIX = "/photo-cdn";

// AA-1015 (S262) — the BotID Basic check on search and narration (lib/botCheck.ts). One build-time value for the
// browser bundle and the route handlers alike: TP_BOT_CHECK "on" / "off" wins; unset = on for a build on Vercel
// (VERCEL=1, production and preview), off elsewhere (local dev / start, CI, tests). lib/botCheck.test.ts checks it.
/** @param {Record<string, string | undefined>} [env] */
export function botCheckFlag(env = process.env) {
  const v = (env.TP_BOT_CHECK ?? "").trim().toLowerCase();
  if (v === "on" || v === "off") return v;
  return env.VERCEL === "1" ? "on" : "off";
}

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // Inlined into client and server code at build (lib/botCheck.ts botCheckOn). Set TP_BOT_CHECK, never this name.
  env: { NEXT_PUBLIC_TP_BOT_CHECK: botCheckFlag() },
  async headers() {
    return [
      { source: "/(.*)", headers: [...SECURITY_HEADERS, ...previewHeaders()] },
      { source: `${PHOTO_PROXY_PREFIX}/:path*`, headers: [{ key: "x-vercel-enable-rewrite-caching", value: "1" }] },
    ];
  },
  async rewrites() {
    return [{ source: `${PHOTO_PROXY_PREFIX}/:path*`, destination: `${PHOTO_CDN_ORIGIN}/:path*` }];
  },
};

export default nextConfig;
