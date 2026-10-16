import { NextResponse, type NextRequest } from "next/server";
import {
  MINTED_TOKEN_HEADER,
  guestCookieName,
  guestCookieOptions,
  isGuestToken,
  mintGuestToken,
} from "./lib/guestToken";

// AA-827: a planner visitor holds the HttpOnly guest-token cookie before the page makes its first
// fetch, so parallel first POSTs all carry the same token. On /api/trip itself (no page load first,
// or the cookie was cleared) the minted token is also handed to the BFF on this same request.
export function middleware(req: NextRequest) {
  const name = guestCookieName();
  const token = isGuestToken(req.cookies.get(name)?.value) ? null : mintGuestToken();

  let res: NextResponse;
  if (req.nextUrl.pathname.startsWith("/api/")) {
    const headers = new Headers(req.headers);
    headers.delete(MINTED_TOKEN_HEADER); // only this middleware may set it
    if (token) headers.set(MINTED_TOKEN_HEADER, token);
    res = NextResponse.next({ request: { headers } });
  } else {
    res = NextResponse.next();
  }
  if (token) res.cookies.set(name, token, guestCookieOptions());
  return res;
}

export const config = {
  matcher: ["/plan/:path*", "/api/trip"],
};
