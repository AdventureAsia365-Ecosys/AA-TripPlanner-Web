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

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  async headers() {
    return [{ source: "/(.*)", headers: SECURITY_HEADERS }];
  },
};

export default nextConfig;
