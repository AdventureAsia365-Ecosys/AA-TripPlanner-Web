// AA-827: the guest credential. A random token (32 bytes, base64url, 43 chars) lives in an HttpOnly
// cookie that only the server side (middleware.ts + the /api/trip BFF) reads; the BFF forwards it to
// the assembly Lambda in a header, and the Lambda stores only its sha256. Client JS never sees it.
// Server-only by convention: do not import this from client components. Pure (no Next imports), so
// it runs in the edge middleware, the Node route handler and `npm test` alike.

// BFF -> assembly Lambda (backend config.GUEST_TOKEN_HEADER).
export const GUEST_TOKEN_HEADER = "x-tripplanner-guest";
// middleware -> BFF, on the same request, for a token minted before the browser holds the cookie.
// The middleware drops any client-supplied value first.
export const MINTED_TOKEN_HEADER = "x-tp-guest-minted";
export const GUEST_COOKIE_MAX_AGE = 60 * 60 * 24 * 90; // 90 days, like the guest session

const TOKEN_RE = /^[A-Za-z0-9_-]{43}$/;

export function mintGuestToken(): string {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function isGuestToken(value: unknown): value is string {
  return typeof value === "string" && TOKEN_RE.test(value);
}

// Production (Vercel, https) uses the __Host- prefix, which requires Secure + Path=/ and no Domain;
// local `next dev` over http uses a plain name without Secure.
export function isSecureCookieEnv(): boolean {
  return process.env.NODE_ENV === "production";
}

export function guestCookieName(secure: boolean = isSecureCookieEnv()): string {
  return secure ? "__Host-tp_guest" : "tp_guest";
}

export function guestCookieOptions(secure: boolean = isSecureCookieEnv()) {
  return {
    httpOnly: true,
    secure,
    sameSite: "lax" as const,
    path: "/",
    maxAge: GUEST_COOKIE_MAX_AGE,
  };
}

export interface HeaderSource {
  get(name: string): string | null;
}

// A browser always sends Origin on a cross-site POST. No Origin = not a browser form/fetch from
// another site (curl, server-side caller): allowed here, the Lambda still checks the token.
export function isSameOrigin(req: { headers: HeaderSource }): boolean {
  const origin = req.headers.get("origin");
  if (!origin) return true;
  const host = (req.headers.get("x-forwarded-host") ?? req.headers.get("host") ?? "").split(",")[0].trim();
  if (!host) return false;
  try {
    return new URL(origin).host === host;
  } catch {
    return false; // "null" (sandboxed frame) or garbage
  }
}
