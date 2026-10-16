import { NextResponse, type NextRequest } from "next/server";
import {
  MINTED_TOKEN_HEADER,
  guestCookieAction,
  guestCookieName,
  serializeGuestCookie,
} from "./lib/guestToken";

// AA-827: the visitor holds the HttpOnly guest-token cookie before the planner makes its first fetch:
// it is minted on the /plan document, so parallel first POSTs usually carry the same token. Only the
// dynamic /plan page and /api/trip are matched: the home, destination and tour pages are cached by the
// CDN (s-maxage), and a per-visitor Set-Cookie must never ride on a shared cached response. Every
// matched request renews the cookie (sliding lifetime). On /api/trip
// itself (no page load first, or the cookie was cleared) the minted token is also handed to the BFF
// on this same request. A cross-site POST is refused here, before any mint, so it can never overwrite
// the visitor's real cookie (guestToken.guestCookieAction).
//
// The cookie is appended as a raw Set-Cookie header, never with `res.cookies.set`: that proxy also
// writes `x-middleware-set-cookie` (the full cookie string) onto the response, which client JS can
// read from a fetch() response and which would defeat HttpOnly.
export function middleware(req: NextRequest) {
  const action = guestCookieAction(req, req.cookies.get(guestCookieName())?.value);
  if (action.kind === "reject") {
    return NextResponse.json({ error: "cross_origin" }, { status: 403 });
  }

  let res: NextResponse;
  if (req.nextUrl.pathname.startsWith("/api/")) {
    const headers = new Headers(req.headers);
    headers.delete(MINTED_TOKEN_HEADER); // only this middleware may set it
    if (action.minted && action.token) headers.set(MINTED_TOKEN_HEADER, action.token);
    res = NextResponse.next({ request: { headers } });
  } else {
    res = NextResponse.next();
  }
  if (action.token) res.headers.append("set-cookie", serializeGuestCookie(action.token));
  return res;
}

export const config = {
  matcher: ["/plan/:path*", "/api/trip"],
};
