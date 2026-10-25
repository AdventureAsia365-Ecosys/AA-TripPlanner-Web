// AA-908 — the signed-in traveller's session, kept by the BFF in sealed HttpOnly cookies. Server-only by convention:
// do not import it from client components. No Next imports, so `npm test` runs it.
//
// Three cookies, each sealed for its own purpose with TP_SESSION_SECRET (lib/seal.ts, AES-256-GCM):
//   __Host-tp_auth     {at: access token, aexp: its expiry, email, ev: email_verified, exp: refresh expiry}
//   __Host-tp_refresh  {rt: refresh token, exp: refresh expiry}  (a Cognito refresh token is ~1.8 kB: own cookie)
//   __Host-tp_signin   {email, mode, session, exp: now + 10 min} (the pending email-code step, lib/cognito.ts)
// HttpOnly, Secure, SameSite=Lax, Path=/ in production (local http: plain names, no Secure), like the guest cookie.
// Client JS never sees a token: the browser only learns "signed in as <email>" from /api/auth/status.
//
// The access token lives 60 minutes. When fewer than REFRESH_MARGIN_S remain, currentAccess() refreshes it
// (GetTokensFromRefreshToken; rotation is on, so a new refresh token replaces the old one) and returns the new
// cookies for the caller's response. Only a refresh Cognito refuses as not authorised (revoked or expired) ends the
// session and clears the cookies. Any other failure (a WAF block, throttling, a Cognito 5xx, the network) keeps
// them, so the next request tries again: a short outage never signs a traveller out.
// Never put these Set-Cookie headers on a CDN-cached response: only the no-store /api/auth/*, /api/me and
// /api/trip routes set them.

import { isSecureCookieEnv } from "./guestToken.ts";
import { open, seal, nowSeconds } from "./seal.ts";
import { CognitoError, type Mode, type Pending, type Tokens } from "./cognito.ts";

export const REFRESH_MARGIN_S = 60;
// The tripplanner client's refresh-token lifetime (AA-CIS-Infra identity.tf: 720 hours). A rotated refresh token
// starts a new 30-day window.
export const REFRESH_LIFETIME_S = 30 * 24 * 3600;
export const SIGNIN_LIFETIME_S = 10 * 60;

export interface AuthCookie {
  at: string;
  aexp: number;
  email: string | null;
  ev: boolean;
  exp: number;
}
interface RefreshCookie {
  rt: string;
  exp: number;
}
interface SigninCookie {
  email: string;
  mode: Mode;
  session: string | null;
  at?: number;
  exp: number;
}

export function cookieName(kind: "auth" | "refresh" | "signin", secure: boolean = isSecureCookieEnv()): string {
  return `${secure ? "__Host-" : ""}tp_${kind}`;
}

function serialize(name: string, value: string, maxAge: number, secure: boolean): string {
  return `${name}=${value}; Path=/; Max-Age=${maxAge}; HttpOnly; SameSite=Lax${secure ? "; Secure" : ""}`;
}

export function clearCookie(kind: "auth" | "refresh" | "signin", secure: boolean = isSecureCookieEnv()): string {
  return serialize(cookieName(kind, secure), "", 0, secure);
}

export function clearSessionCookies(secure: boolean = isSecureCookieEnv()): string[] {
  return [clearCookie("auth", secure), clearCookie("refresh", secure)];
}

// The claims the BFF shows or keeps from an access token it just got from Cognito over TLS. Not a verification:
// the assembly Lambda verifies every token it is sent (backend/shared/identity_verifier.py).
export function accessClaims(accessToken: string): { exp: number; email: string | null; ev: boolean } {
  try {
    const payload = JSON.parse(Buffer.from(accessToken.split(".")[1] ?? "", "base64url").toString("utf8"));
    return {
      exp: typeof payload?.exp === "number" ? payload.exp : 0,
      email: typeof payload?.email === "string" ? payload.email : null,
      ev: payload?.email_verified === true || payload?.email_verified === "true",
    };
  } catch {
    return { exp: 0, email: null, ev: false };
  }
}

// The Set-Cookie headers for fresh tokens. `previousRefresh` is kept when Cognito did not rotate it.
export function sessionCookies(
  secret: string,
  tokens: Tokens,
  previousRefresh: { rt: string; exp: number } | null = null,
  now: number = nowSeconds(),
  secure: boolean = isSecureCookieEnv(),
): string[] {
  const claims = accessClaims(tokens.accessToken);
  const rt = tokens.refreshToken ?? previousRefresh?.rt ?? null;
  const exp = tokens.refreshToken ? now + REFRESH_LIFETIME_S : (previousRefresh?.exp ?? now + tokens.expiresIn);
  const auth: AuthCookie = {
    at: tokens.accessToken,
    aexp: claims.exp || now + tokens.expiresIn,
    email: claims.email,
    ev: claims.ev,
    exp,
  };
  const out = [serialize(cookieName("auth", secure), seal(secret, "session", { ...auth }), exp - now, secure)];
  if (rt && tokens.refreshToken) {
    out.push(serialize(cookieName("refresh", secure), seal(secret, "refresh", { rt, exp }), exp - now, secure));
  }
  return out;
}

export function pendingCookie(secret: string, pending: Pending, now: number = nowSeconds(),
  secure: boolean = isSecureCookieEnv()): string {
  const value: SigninCookie = { ...pending, exp: now + SIGNIN_LIFETIME_S };
  return serialize(cookieName("signin", secure), seal(secret, "signin", { ...value }), SIGNIN_LIFETIME_S, secure);
}

export interface CookieSource {
  get(name: string): { value: string } | undefined;
}

export function readPending(secret: string, cookies: CookieSource, now: number = nowSeconds()): Pending | null {
  const v = open<SigninCookie>(secret, "signin", cookies.get(cookieName("signin"))?.value, now);
  if (!v || typeof v.email !== "string" || (v.mode !== "signup" && v.mode !== "signin")) return null;
  return {
    email: v.email,
    mode: v.mode,
    session: typeof v.session === "string" ? v.session : null,
    ...(typeof v.at === "number" ? { at: v.at } : {}),
  };
}

export function readSession(secret: string, cookies: CookieSource, now: number = nowSeconds()): AuthCookie | null {
  const v = open<AuthCookie>(secret, "session", cookies.get(cookieName("auth"))?.value, now);
  return v && typeof v.at === "string" ? v : null;
}

export function readRefresh(secret: string, cookies: CookieSource, now: number = nowSeconds()): RefreshCookie | null {
  const v = open<RefreshCookie>(secret, "refresh", cookies.get(cookieName("refresh"))?.value, now);
  return v && typeof v.rt === "string" ? v : null;
}

// The first session after a sign-up (S251 follow-up of the S250 live run). The auto sign-in right after
// ConfirmSignUp (InitiateAuth USER_AUTH with the confirm Session) can be minted from the user's attributes as they
// were before the code confirmed the email: the S250 run saw email_verified false in that first session while Cognito
// held true, and every later sign-in showed true. The BFF never asserts verification itself: when the new token
// says "not verified" it asks Cognito for a new one with the refresh token it just received (GetTokensFromRefreshToken,
// so the EXTERNAL pre-token trigger reads the stored attributes again). Best effort: any failure keeps the tokens of
// the sign-in, and the normal hourly refresh then corrects the claim, as before.
export async function settleSignUpTokens(
  tokens: Tokens,
  doRefresh: (refreshToken: string) => Promise<Tokens>,
): Promise<{ tokens: Tokens; refreshed: boolean }> {
  if (!tokens.refreshToken || accessClaims(tokens.accessToken).ev) return { tokens, refreshed: false };
  try {
    const fresh = await doRefresh(tokens.refreshToken);
    // Rotation is on: the new refresh token replaces the one just issued; without one the first stays valid.
    return { tokens: { ...fresh, refreshToken: fresh.refreshToken ?? tokens.refreshToken }, refreshed: true };
  } catch {
    return { tokens, refreshed: false };
  }
}

export interface Access {
  accessToken: string | null; // null = signed out
  email: string | null;
  emailVerified: boolean;
  setCookies: string[]; // to append to the caller's response (refreshed, or cleared)
}

const SIGNED_OUT: Access = { accessToken: null, email: null, emailVerified: false, setCookies: [] };

// The traveller's access token for this request, refreshed when it is about to expire. `doRefresh` is
// lib/cognito.ts refresh() bound to the request's call context (injectable for tests).
export async function currentAccess(
  secret: string,
  cookies: CookieSource,
  doRefresh: (refreshToken: string) => Promise<Tokens>,
  now: number = nowSeconds(),
): Promise<Access> {
  if (!secret) return SIGNED_OUT;
  const session = readSession(secret, cookies, now);
  if (session && session.aexp - now > REFRESH_MARGIN_S) {
    return { accessToken: session.at, email: session.email, emailVerified: session.ev, setCookies: [] };
  }
  const stored = readRefresh(secret, cookies, now);
  if (!stored) {
    return session || cookies.get(cookieName("refresh")) ? { ...SIGNED_OUT, setCookies: clearSessionCookies() }
      : SIGNED_OUT;
  }
  try {
    const tokens = await doRefresh(stored.rt);
    const claims = accessClaims(tokens.accessToken);
    return {
      accessToken: tokens.accessToken,
      email: claims.email,
      emailVerified: claims.ev,
      setCookies: sessionCookies(secret, tokens, stored, now),
    };
  } catch (e) {
    if (e instanceof CognitoError && e.type === "NotAuthorizedException") {
      // Revoked or expired refresh token: signed out for good.
      return { ...SIGNED_OUT, setCookies: clearSessionCookies() };
    }
    // Anything else is transient: keep the cookies. The access token is used while it is still valid.
    if (session && session.aexp > now) {
      return { accessToken: session.at, email: session.email, emailVerified: session.ev, setCookies: [] };
    }
    return SIGNED_OUT;
  }
}
