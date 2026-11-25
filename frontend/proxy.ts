import { NextResponse, type NextFetchEvent, type NextRequest } from "next/server";
import {
  MINTED_TOKEN_HEADER,
  guestCookieAction,
  guestCookieName,
  serializeGuestCookie,
} from "./lib/guestToken";
import { createGoneCache, gonePage } from "./lib/goneTours";
import {
  hostNotFoundPage,
  hostUnavailablePage,
  isOwnHost,
  requestHost,
  resolveTenantHost,
  siteHost,
  tenantHostPath,
} from "./lib/tenantHost";
import { TenantLookupUnavailable, resolveTenant, type TenantSite } from "./lib/tenantSite";
import { tenantPlannerPath, tenantRootSlug } from "./lib/tenantRoot";
import { jsonInit } from "./lib/jsonResponse";

// AA-986 L6 (root ADR 0015 Q5): a retired tour's page answers 410 Gone (Next has no "gone" for a page). The list
// of retired slug suffixes lives in this instance's memory (lib/goneTours.ts), refreshed every minute in the
// background; AA-987: a request waits for the browse call (≤ 1.5 s) only when the list is over 2 minutes old, so a
// retired tour answers 410 within 2 minutes on every instance.
const goneTours = createGoneCache();

// AA-906: Next 16 renamed `middleware.ts` to `proxy.ts` (official codemod `middleware-to-proxy`); the
// proxy runs on the Node.js runtime (it was Edge before). Behaviour and matcher are unchanged.
// AA-827: the visitor holds the HttpOnly guest-token cookie before the planner makes its first fetch:
// it is minted on the /plan document, so parallel first POSTs usually carry the same token. Only the
// dynamic /plan page and /api/trip get the cookie (tour pages are matched for the AA-986 410 only): the home, destination and tour pages are cached by the
// CDN (s-maxage), and a per-visitor Set-Cookie must never ride on a shared cached response. Every
// matched request renews the cookie (sliding lifetime). On /api/trip
// itself (no page load first, or the cookie was cleared) the minted token is also handed to the BFF
// on this same request. A cross-site POST is refused here, before any mint, so it can never overwrite
// the visitor's real cookie (guestToken.guestCookieAction).
//
// The cookie is appended as a raw Set-Cookie header, never with `res.cookies.set`: that cookie wrapper
// also writes `x-middleware-set-cookie` (the full cookie string) onto the response, which client JS can
// read from a fetch() response and which would defeat HttpOnly.
// AA-1090 R8: the paths this proxy acted on before custom domains (the old matcher, kept as literals): the planner
// pages (guest cookie), the four /api/trip operations (guest cookie, cross-site refusal) and the tour pages (410).
const PLANNER_PATH_RE = /^\/(?:[^/]+\/)?plan(?:\/.*)?$/;
const TRIP_API_PATHS = new Set(["/api/trip", "/api/trip/narrate", "/api/trip/send", "/api/trip/chat", "/api/trip/addons"]); // AA-1152
const TOUR_PATH_RE = /^\/tours\/[^/]+$/;
const OWN_HOST = siteHost(process.env.NEXT_PUBLIC_SITE_URL || "https://tripplanner.lumiguides.it.com");

function actsOn(pathname: string): boolean {
  return PLANNER_PATH_RE.test(pathname) || TRIP_API_PATHS.has(pathname) || TOUR_PATH_RE.test(pathname);
}

function hostPage(status: 404 | 503): NextResponse {
  return new NextResponse(status === 404 ? hostNotFoundPage() : hostUnavailablePage(), {
    status,
    headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store", "x-robots-tag": "noindex" },
  });
}

// AA-1090 R8: a request on an agency's own domain. The host must be a served tenant's active custom domain (else a
// plain 404, never AA's site); the path is mapped onto the tenant's pages (lib/tenantHost.ts) and then handled like
// the same path on our host.
async function onTenantHost(req: NextRequest, event: NextFetchEvent | undefined, host: string) {
  let slug: string;
  try {
    const site = await resolveTenantHost(host);
    if (!site) return hostPage(404);
    slug = site.slug;
  } catch (e) {
    if (e instanceof TenantLookupUnavailable) return hostPage(503);
    throw e;
  }
  const decision = tenantHostPath(req.nextUrl.pathname, slug);
  if (decision.kind === "not_found") return hostPage(404);
  const path = decision.kind === "rewrite" ? decision.path : req.nextUrl.pathname;
  // AA-1111: "/" (rewritten to "/{slug}") and "/{slug}" go to the planner with a bodiless redirect (lib/tenantRoot.ts).
  if (tenantRootSlug(path) === slug) return plannerRedirect(req, slug);
  const target = decision.kind === "rewrite" ? new URL(`${path}${req.nextUrl.search}`, req.url) : null;
  if (!actsOn(path)) return pass(target);
  return handle(req, event, path, target);
}

export async function proxy(req: NextRequest, event?: NextFetchEvent) {
  const host = requestHost(req.headers.get("host"));
  if (!isOwnHost(host, OWN_HOST)) return onTenantHost(req, event, host);
  const root = tenantRootSlug(req.nextUrl.pathname);
  if (root) return tenantRoot(req, root);
  if (!actsOn(req.nextUrl.pathname)) return NextResponse.next();
  return handle(req, event, req.nextUrl.pathname, null);
}

// AA-1111 (S294 ZAP "Big Redirect Detected"): the bare /{slug} of a served tenant is a 307 to its planner answered here,
// with no body. A redirect() in app/[tenant]/page.tsx is sent only after Next has streamed the page shell (about 10 KB).
// A slug that is no served tenant, or a lookup that cannot answer, goes on to that page unchanged (the 404 page, or
// the retryable app/[tenant]/error.tsx), which keeps its redirect() as the fallback.
async function tenantRoot(req: NextRequest, slug: string): Promise<NextResponse> {
  let site: TenantSite | null;
  try {
    site = await resolveTenant(slug);
  } catch {
    return NextResponse.next();
  }
  return site ? plannerRedirect(req, site.slug) : NextResponse.next();
}

// An absolute URL on the request's own host: Next sends it as the relative Location "/{slug}/plan" (same host).
function plannerRedirect(req: NextRequest, slug: string): NextResponse {
  return NextResponse.redirect(new URL(`${tenantPlannerPath(slug)}${req.nextUrl.search}`, req.url), 307);
}

// `path` is the path the app serves (the request's own, or the tenant page an agency's domain maps it to, `target`).
async function handle(req: NextRequest, event: NextFetchEvent | undefined, path: string, target: URL | null) {
  // Tour pages are CDN-cached and public: never a guest cookie here, only the retired check.
  if (path.startsWith("/tours/")) return tourPage(event, path, target);
  return withGuestCookie(req, path, target);
}

function pass(target: URL | null): NextResponse {
  return target ? NextResponse.rewrite(target) : NextResponse.next();
}

async function tourPage(event: NextFetchEvent | undefined, path: string, target: URL | null) {
  const retired = await goneTours.check(path);
  const refreshing = goneTours.pending();
  if (refreshing) event?.waitUntil(refreshing);
  if (!retired) return pass(target);
  return new NextResponse(gonePage(), {
    status: 410,
    headers: {
      "content-type": "text/html; charset=utf-8",
      // AA-987: no shared cache keeps the 410 (a restored tour must come back within 2 minutes: CloudFront
      // caches no page, AA-CIS-Infra tripplanner_web_edge.tf); a browser may keep it for a minute.
      "cache-control": "public, max-age=60",
      "x-robots-tag": "noindex",
    },
  });
}

function withGuestCookie(req: NextRequest, path: string, target: URL | null) {
  const action = guestCookieAction(req, req.cookies.get(guestCookieName())?.value);
  if (action.kind === "reject") {
    return NextResponse.json({ error: "cross_origin" }, jsonInit({ status: 403 }));
  }
  let res: NextResponse;
  if (path.startsWith("/api/")) {
    const headers = new Headers(req.headers);
    headers.delete(MINTED_TOKEN_HEADER); // only this proxy may set it
    if (action.minted && action.token) headers.set(MINTED_TOKEN_HEADER, action.token);
    res = NextResponse.next({ request: { headers } });
  } else {
    res = pass(target);
  }
  if (action.token) res.headers.append("set-cookie", serializeGuestCookie(action.token));
  return res;
}

export const config = {
  // AA-1090 R8: every path but the build chunks and the image optimiser, because a request on an agency's own domain
  // is resolved by its Host whatever the path. On our own host the proxy still acts only on the paths of actsOn():
  // AA-986 L6 "/tours/:slug" (410 of a retired tour, never a cookie); AA-1015 "/api/trip/narrate", AA-1038 slice 3
  // "/api/trip/send", AA-1041 "/api/trip/chat" (same guest cookie rules as "/api/trip"); "/plan" and, AA-1038 R5
  // slice 2, "/:tenant/plan" (a tenant's planner, the same guest cookie; the ids are per tenant).
  matcher: ["/((?!_next/static|_next/image).*)"],
};
