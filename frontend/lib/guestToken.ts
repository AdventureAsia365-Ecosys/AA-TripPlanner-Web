// AA-827: the guest credential. A random token (32 bytes, base64url, 43 chars) lives in an HttpOnly
// cookie that only the server side (proxy.ts + the /api/trip BFF) reads; the BFF forwards it to
// the assembly Lambda in a header, and the Lambda stores only its sha256. Client JS never sees it:
// the cookie is written as a raw Set-Cookie header (serializeGuestCookie), never through Next's
// `res.cookies.set`, whose cookie wrapper also copies the value into an `x-middleware-set-cookie` response
// header that a same-origin fetch() can read.
// Server-only by convention: do not import this from client components. Pure (no Next imports), so
// it runs in the Next proxy (proxy.ts, formerly middleware.ts), the Node route handler and `npm test` alike.

// BFF -> assembly Lambda (backend config.GUEST_TOKEN_HEADER).
export const GUEST_TOKEN_HEADER = "x-tripplanner-guest";
// proxy.ts -> BFF, on the same request, for a token minted before the browser holds the cookie.
// proxy.ts drops any client-supplied value first.
export const MINTED_TOKEN_HEADER = "x-tp-guest-minted";
// 400 days, the most a browser keeps a cookie (Chrome caps Max-Age there). Sliding: proxy.ts
// re-sends the cookie with a fresh Max-Age on every matched request, so an active guest never loses it.
// The localStorage ids it guards have no expiry, and sessions.expires_at is not enforced.
export const GUEST_COOKIE_MAX_AGE = 60 * 60 * 24 * 400;
// BFF -> browser: the token the BFF used was minted on this request (no cookie came in) and the
// Lambda refused it, so the cookie is most likely not kept by this browser. Never resets the ids.
export const COOKIES_REQUIRED = "cookies_required";

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

// The Set-Cookie value. HttpOnly + SameSite=Lax + Path=/ always; Secure (and the __Host- name) in
// production.
export function serializeGuestCookie(token: string, secure: boolean = isSecureCookieEnv()): string {
  return `${guestCookieName(secure)}=${token}; Path=/; Max-Age=${GUEST_COOKIE_MAX_AGE}; HttpOnly; SameSite=Lax${secure ? "; Secure" : ""}`;
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

export interface GuestRequest {
  method: string;
  headers: HeaderSource;
}

export type GuestCookieAction =
  // A cross-site state-changing request: answer 403 and set no cookie.
  | { kind: "reject" }
  // Go on; `token` (when set) is (re)sent as the cookie, and `minted` says it is new on this request.
  | { kind: "pass"; token: string | null; minted: boolean };

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

// What proxy.ts does with the guest cookie on one request.
// - A cross-site POST never carries the SameSite=Lax cookie, so it would look cookie-less and a new
//   token would overwrite the visitor's real cookie (a top-level cross-site POST accepts a Lax
//   Set-Cookie): it is rejected before any mint.
// - A cross-site request that is not a top-level GET navigation (an <img>, an iframe, a fetch) does
//   not carry the cookie either: leave the cookie alone.
// - Otherwise renew the valid cookie (sliding lifetime) or mint one.
export function guestCookieAction(
  req: GuestRequest,
  cookie: string | undefined,
  mint: () => string = mintGuestToken,
): GuestCookieAction {
  const safe = SAFE_METHODS.has(req.method.toUpperCase());
  if (!safe && !isSameOrigin(req)) return { kind: "reject" };
  if (req.headers.get("sec-fetch-site") === "cross-site"
    && !(safe && req.headers.get("sec-fetch-mode") === "navigate")) {
    return { kind: "pass", token: null, minted: false };
  }
  if (isGuestToken(cookie)) return { kind: "pass", token: cookie, minted: false };
  return { kind: "pass", token: mint(), minted: true };
}

// The BFF's answer to the browser when the Lambda refused the guest token. A 401
// guest_session_invalid for a token minted on this very request (the browser sent no cookie) becomes
// 401 cookies_required: either the cookie was lost once (the browser keeps the new one, and the
// client's single retry then gets the real answer) or the browser does not keep cookies at all (the
// client keeps its ids and says cookies are needed, instead of wiping the trip every second edit).
// Returns null to pass the Lambda's answer through unchanged.
export function mapMintedDenial(
  status: number,
  bodyText: string,
  minted: boolean,
): { status: number; body: string } | null {
  if (!minted || status !== 401) return null;
  try {
    if (JSON.parse(bodyText)?.error !== "guest_session_invalid") return null;
  } catch {
    return null;
  }
  return { status: 401, body: JSON.stringify({ error: COOKIES_REQUIRED }) };
}
