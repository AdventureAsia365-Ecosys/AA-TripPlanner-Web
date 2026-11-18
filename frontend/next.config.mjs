// AA-876 — security headers on every response (same list as the CIS admin, the Booking admin and the CRM web).
// AA-1111 (S284, ZAP baseline triage): + Cross-Origin-Opener-Policy and a full Content-Security-Policy: default-src
// 'self' and an explicit list per fetch directive (the Mapbox GL map: its API, tile and telemetry hosts, blob workers;
// photos from any https host), so no directive falls back to a wildcard. script-src keeps 'unsafe-inline': the home,
// destination and tour pages are prerendered and cached (revalidate + CloudFront s-maxage), and a per-request nonce
// would need every page rendered per request. Moving to nonces (or to hashes) is a follow-up in
// docs/implementation-notes/AA-1111-headers.md. `frame-ancestors 'none'` also means no partner can iframe TripPlanner
// until an allow-list is designed (KAN-20 B2B).
const MAPBOX_HOSTS = ["https://api.mapbox.com", "https://*.tiles.mapbox.com", "https://events.mapbox.com"];

/** @param {{ dev?: boolean }} [opts] */
export function contentSecurityPolicy(opts = {}) {
  const script = ["'self'", "'unsafe-inline'", ...(opts.dev ? ["'unsafe-eval'"] : [])];
  /** @type {[string, string[]][]} */
  const directives = [
    ["frame-ancestors", ["'none'"]],
    ["default-src", ["'self'"]],
    ["script-src", script],
    ["style-src", ["'self'", "'unsafe-inline'"]],
    ["font-src", ["'self'", "data:"]],
    ["img-src", ["'self'", "data:", "blob:", "https:"]],
    ["connect-src", ["'self'", ...MAPBOX_HOSTS]],
    ["worker-src", ["'self'", "blob:"]],
    ["child-src", ["'self'", "blob:"]],
    ["frame-src", ["'self'"]],
    ["manifest-src", ["'self'"]],
    ["base-uri", ["'self'"]],
    ["form-action", ["'self'"]],
    ["object-src", ["'none'"]],
  ];
  return directives.map(([name, sources]) => `${name} ${sources.join(" ")}`).join("; ");
}

export const SECURITY_HEADERS = [
  { key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Content-Security-Policy", value: contentSecurityPolicy({ dev: process.env.NODE_ENV === "development" }) },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=(), usb=()" },
  { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
];

// AA-995 S256 (mobile LCP) — the LCP photo of a page is served from this origin (lib/photoResolve.ts
// sameOriginPhotoUrl): /photo-cdn/<path> proxies to the photo CDN, so the browser reuses the HTML connection instead of
// opening one to another host. Keep PHOTO_CDN_ORIGIN / PHOTO_PROXY_PREFIX equal to lib/photoResolve.ts
// (lib/photoResolve.test.ts reads this file). CloudFront caches the proxied file at the edge (AA-CIS-Infra
// tripplanner_web_edge.tf, behaviour /photo-cdn/* with ?v= in the cache key), so the CDN is asked once per file and
// edge location, not once per visit.
const PHOTO_CDN_ORIGIN = "https://img.lumiguides.it.com";

// TL-24 (S262) — until go-live every deployment is a preview: X-Robots-Tag noindex on every response unless the
// runtime env sets SITE_INDEXING=on. Same rule as isLiveSite() / PREVIEW_ROBOTS_HEADER in lib/siteMode.ts (lib/siteMode.test.ts
// checks both). Read when Next loads the config (build and server start), like any other env-driven header.
/** @param {Record<string, string | undefined>} [env] */
export function previewHeaders(env = process.env) {
  const live = (env.SITE_INDEXING ?? "").trim().toLowerCase() === "on";
  return live ? [] : [{ key: "X-Robots-Tag", value: "noindex, nofollow" }];
}
const PHOTO_PROXY_PREFIX = "/photo-cdn";

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // AA-1038 R5 hosting: the ECS image (frontend/Dockerfile) builds the Next standalone server; the CI build keeps the
  // default output. Same switch as the AA-Booking and AA-CIS-App admin images.
  ...(process.env.NEXT_OUTPUT === "standalone" ? { output: "standalone" } : {}),
  async headers() {
    return [{ source: "/(.*)", headers: [...SECURITY_HEADERS, ...previewHeaders()] }];
  },
  async rewrites() {
    return [{ source: `${PHOTO_PROXY_PREFIX}/:path*`, destination: `${PHOTO_CDN_ORIGIN}/:path*` }];
  },
};

export default nextConfig;
