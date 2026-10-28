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
const PHOTO_PROXY_PREFIX = "/photo-cdn";

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  async headers() {
    return [
      { source: "/(.*)", headers: SECURITY_HEADERS },
      { source: `${PHOTO_PROXY_PREFIX}/:path*`, headers: [{ key: "x-vercel-enable-rewrite-caching", value: "1" }] },
    ];
  },
  async rewrites() {
    return [{ source: `${PHOTO_PROXY_PREFIX}/:path*`, destination: `${PHOTO_CDN_ORIGIN}/:path*` }];
  },
};

export default nextConfig;
