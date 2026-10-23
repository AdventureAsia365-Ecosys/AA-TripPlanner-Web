import { NextResponse, type NextFetchEvent, type NextRequest } from "next/server";
import {
  MINTED_TOKEN_HEADER,
  guestCookieAction,
  guestCookieName,
  serializeGuestCookie,
} from "./lib/guestToken";
import { createGoneCache, gonePage } from "./lib/goneTours";

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
export async function proxy(req: NextRequest, event?: NextFetchEvent) {
  // Tour pages are CDN-cached and public: never a guest cookie here, only the retired check.
  if (req.nextUrl.pathname.startsWith("/tours/")) {
    const retired = await goneTours.check(req.nextUrl.pathname);
    const refreshing = goneTours.pending();
    if (refreshing) event?.waitUntil(refreshing);
    if (retired) {
      return new NextResponse(gonePage(), {
        status: 410,
        headers: {
          "content-type": "text/html; charset=utf-8",
          // AA-987: no shared cache keeps the 410 (a restored tour must come back within 2 minutes); a browser
          // may keep it for a minute.
          "cache-control": "public, max-age=60",
          "vercel-cdn-cache-control": "no-store",
          "x-robots-tag": "noindex",
        },
      });
    }
    return NextResponse.next();
  }

  const action = guestCookieAction(req, req.cookies.get(guestCookieName())?.value);
  if (action.kind === "reject") {
    return NextResponse.json({ error: "cross_origin" }, { status: 403 });
  }

  let res: NextResponse;
  if (req.nextUrl.pathname.startsWith("/api/")) {
    const headers = new Headers(req.headers);
    headers.delete(MINTED_TOKEN_HEADER); // only this proxy may set it
    if (action.minted && action.token) headers.set(MINTED_TOKEN_HEADER, action.token);
    res = NextResponse.next({ request: { headers } });
  } else {
    res = NextResponse.next();
  }
  if (action.token) res.headers.append("set-cookie", serializeGuestCookie(action.token));
  return res;
}

export const config = {
  // AA-986 L6: "/tours/:slug" for the 410 of a retired tour (no cookie is ever set on it).
  matcher: ["/plan/:path*", "/api/trip", "/tours/:slug"],
};
