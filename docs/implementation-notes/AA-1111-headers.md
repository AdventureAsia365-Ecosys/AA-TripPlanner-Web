# AA-1111 — security headers slice (S284, lane l5-aa1111-headers)

Source: the S283 ZAP baseline triage (root `docs/audits/2026-11-17-S283-access-review/zap-baseline.md`).

## Decisions

- **Full CSP, no nonce.** `frontend/next.config.mjs` `contentSecurityPolicy()`: `default-src 'self'` and every fetch
  directive explicit (Mapbox GL: `connect-src` api / `*.tiles` / events hosts, `worker-src` + `child-src blob:`;
  `img-src` any https host for tour photos), so ZAP's "wildcard directive" (a directive falling back to `*`) is gone.
- **script-src keeps `'unsafe-inline'`.** The home, destination and tour pages are prerendered with `revalidate` and
  cached by CloudFront (`s-maxage`); a nonce must be per response, so it needs every page rendered per request, which
  undoes the AA-995 LCP and cache work. The CIS, Booking and CRM admins (per-request pages) use a nonce. Options for
  later: Next's experimental SRI / hash-based CSP for the static pages, or a nonce only on the dynamic planner pages.
- **frame-ancestors first** in the header (the deploy check greps it); **HSTS stays two years, sub-domains, no
  preload**; **COOP same-origin** added. No COEP: it would block cross-origin photos without CORP.

## Changed

- `Permissions-Policy` adds `payment=()` and `usb=()`.
- The proxy's own responses (410 of a retired tour, 404 / 503 of an agency domain) already carried the
  `next.config.mjs` headers; checked again with `next start`.

## Tradeoffs

- No settings row for the CSP (the decision said not to invent one).
- The map with a real token was not loaded locally (no token): the live step opens `/plan` on Dev and checks the
  console for CSP violations.

## Should know

- `lib/securityHeaders.test.ts` (npm test) checks the policy and the header set; `.github/scripts/check_security_headers.sh`
  is unchanged (it greps `frame-ancestors 'none'`, still true).
- ZAP's "debug error messages" ("under construction" on two tour pages) and the 500s on some destination pages are
  not headers: they stay open in the triage.
