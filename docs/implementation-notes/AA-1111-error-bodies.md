# AA-1111 — error bodies and the agency redirect (S294, lane l26-tp-error-bodies)

Source: the S283 and S294 ZAP baselines (root `docs/audits/2026-11-17-S283-access-review/zap-baseline.md`,
`docs/audits/2026-11-25-S294-session/zap-baseline.md`), alerts "Information Disclosure - Debug Error Messages" (10023)
and "Big Redirect Detected" (10044) on the `tripplanner` and `tripplanner-plan` targets.

## What the scan actually hit (S294 report JSON, then plain read-only curl of the live host)

- **Debug Error Messages: false positive.** Both instances are tour pages (`/tours/<slug>`, status 200). The evidence is
  the phrase "under construction", which ZAP's list of debug strings contains; on these pages it is ordinary itinerary
  copy (a walk that follows roads being built), in the visible text and in the JSON-LD. It is CIS-owned tour text, not
  an error page, so nothing changes in the copy. The real 404 bodies (`/no-such-page`, `/tours/<missing>`,
  `/destinations/<missing>`, `/{slug}/<missing>`, `/api/<missing>`, a missing `/_next/static` file) carry the kit 404
  page only: no stack, no path, no version. The only framework marks are the ones every Next page carries (the
  `__next_error__` html id and the `NEXT_HTTP_ERROR_FALLBACK;404` digest in the RSC payload, `/_next/` asset paths):
  not error detail, kept.
- **Big Redirect: real (harmless data, but a real page body).** `GET /{slug}` (an agency's bare site) answered 307
  to `/{slug}/plan` with a 9.8 KB body: Next streams the whole page shell (metadata: the agency's public name and
  description) before `redirect()` in a server component turns it into a 307. No private data, but a redirect should
  carry no page.

## Decisions

- **The bare `/{slug}` redirect moves to `proxy.ts`.** For a served tenant the proxy answers
  `NextResponse.redirect(…, 307)`: a relative `Location` (Next relativises a same-host URL), the same target as before
  plus the request's query string, and no page in the body (Next writes the target itself as the body of a proxy
  redirect: 23 bytes for the fixture agency, against 9.8 KB before). A slug that is no served tenant, or a lookup that cannot answer, passes to `app/[tenant]/page.tsx`
  unchanged (404 page / retryable error page), so the page keeps its `redirect()` as the fallback. An agency's own
  domain (`/` is rewritten to `/{slug}`) gets the same bodiless redirect; the tenant is already resolved there.
  The slug test is `lib/tenantRoot.ts` (pure, `npm test`).
- **One error shape for the BFF (`lib/apiError.ts`).** A 5xx the BFF sends is `{"error": "<code>", "request_id":
  "<id>"}` with `x-request-id`: an upstream 5xx keeps its `error` only when it is a short code (lower case, digits,
  `_` or space, 49 characters at most), anything else (API Gateway's own `{"message": …}`, a body with `detail`, text
  that is not JSON) becomes `upstream_error`; every other field is dropped. 2xx-4xx bodies pass unchanged (they are
  the Lambdas' own codes and validation messages the planner reads).
- **A thrown error in a route handler** (fetch to a Lambda that cannot be reached, a bug) is caught by
  `withApiErrors`: 500 `{"error": "internal", "request_id"}` and one `bff_error` log line (route, error class, id),
  instead of Next's bare 500.
- **"not configured" 503s** no longer name the env variable: `{"error": "not_configured", "request_id"}`.
- **HTML: `app/global-error.tsx`** (kit `ErrorState`, brand-free because it can wrap an agency page) replaces Next's
  default global error page; the catalog and agency error pages show the error's digest as a "Reference" (the id
  Next logs with the error), never the message.
- **Lambda:** the narration 502 no longer returns `detail: str(e)`; it logs the error class and where it was raised
  (like `serve()`'s catch-all) and answers `{"error": "narration_failed"}`.

## Changed

- Nothing in the tour copy (false positive, documented above).
- `scripts/check-agency-render.mjs` (CI, after the build) also checks the bodiless redirect on our host and on the
  agency's own domain, and two BFF error bodies (an upstream crash whose body names internals, a missing trip API).

## Reproduced locally (next build + next start, Lambdas unreachable)

- `/api/trip`, `/api/browse`, `/api/trip/addons` with an unreachable Lambda: before, Next's bare 500; now 500
  `{"error":"internal","request_id":"<x-request-id>"}` and one `bff_error` line (route, `TypeError`, id).
- A failed tenant lookup (`/{slug}`) and a failed destination read show the kit error pages with "Reference:
  <digest>"; the HTML carries the digest only, the stack stays in the server log.

## Tradeoffs

- The page-level `redirect()` stays as a fallback; the cost is that a request whose proxy lookup failed still gets
  the old body. Rare (the lookup is cached per instance) and harmless.
- 4xx bodies keep the Lambdas' validation messages (some echo the bad value, for example a malformed tile id). They
  are our own messages, JSON with `nosniff`, and the planner shows none of them; not changed here.
- `/api/health` keeps `version` and `git_sha`: the deploy checks read them (AA-891).

## Should know

- Tests: `lib/apiError.test.ts`, `lib/tenantRoot.test.ts` (npm test), `backend/tests/test_s294_error_bodies.py`.
- Live step after deploy: `curl -s -o b -w '%{http_code} %{size_download} %{redirect_url}' https://<tp host>/<pilot slug>`
  → 307, at most the target's length in bytes, location `/<pilot slug>/plan`; the 404 paths above unchanged.
- The proxy now looks up every single-segment path on our host that can be a slug (the page did the same lookup
  before; both are cached per instance, a miss for 30 s).
