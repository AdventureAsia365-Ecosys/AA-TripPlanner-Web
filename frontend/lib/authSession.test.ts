// AA-908: the traveller session cookies (lib/authSession.ts). Run: npm test.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  REFRESH_LIFETIME_S,
  REFRESH_MARGIN_S,
  accessClaims,
  cookieName,
  currentAccess,
  pendingCookie,
  readPending,
  readRefresh,
  readSession,
  sessionCookies,
} from "./authSession.ts";
import type { Tokens } from "./cognito.ts";

const SECRET = "k".repeat(40);
const NOW = 1_800_000_000;

function jwt(payload: Record<string, unknown>): string {
  const b = (o: unknown) => Buffer.from(JSON.stringify(o)).toString("base64url");
  return `${b({ alg: "RS256" })}.${b(payload)}.sig`;
}

// The cookies a browser would send back after these Set-Cookie headers (name=value only).
function jar(setCookies: string[]) {
  const values = new Map<string, string>();
  for (const c of setCookies) {
    const [pair, ...attrs] = c.split("; ");
    const [name, ...rest] = pair.split("=");
    if (attrs.includes("Max-Age=0")) values.delete(name);
    else values.set(name, rest.join("="));
  }
  return { get: (name: string) => (values.has(name) ? { value: values.get(name)! } : undefined), values };
}

const TOKENS: Tokens = {
  accessToken: jwt({ exp: NOW + 3600, email: "t@example.com", email_verified: "true" }),
  refreshToken: "refresh-1",
  expiresIn: 3600,
};

test("fresh tokens become sealed HttpOnly cookies that hold no token in clear", () => {
  const set = sessionCookies(SECRET, TOKENS, null, NOW, true);
  assert.equal(set.length, 2);
  for (const c of set) {
    assert.match(c, /^__Host-tp_(auth|refresh)=/);
    assert.match(c, /; Path=\/; Max-Age=\d+; HttpOnly; SameSite=Lax; Secure$/);
    assert.equal(c.includes("refresh-1") || c.includes(TOKENS.accessToken), false);
  }
  assert.ok(set[0].includes(`Max-Age=${REFRESH_LIFETIME_S}`));
  const cookies = jar(set);
  const s = readSession(SECRET, { get: (n) => cookies.get(n.replace(/^tp_/, "__Host-tp_")) }, NOW);
  assert.equal(s?.email, "t@example.com");
  assert.equal(s?.ev, true);
});

test("a valid access token is used as it is, without a refresh", async () => {
  const cookies = jar(sessionCookies(SECRET, TOKENS, null, NOW, false));
  let refreshed = 0;
  const a = await currentAccess(SECRET, cookies, async () => {
    refreshed++;
    return TOKENS;
  }, NOW + 100);
  assert.equal(a.accessToken, TOKENS.accessToken);
  assert.equal(a.email, "t@example.com");
  assert.deepEqual(a.setCookies, []);
  assert.equal(refreshed, 0);
});

test("near expiry the token is refreshed and a rotated refresh token replaces the old one", async () => {
  const cookies = jar(sessionCookies(SECRET, TOKENS, null, NOW, false));
  const later = NOW + 3600 - REFRESH_MARGIN_S + 1;
  const rotated: Tokens = { accessToken: jwt({ exp: later + 3600, email: "t@example.com" }), refreshToken: "refresh-2",
    expiresIn: 3600 };
  let used = "";
  const a = await currentAccess(SECRET, cookies, async (rt) => {
    used = rt;
    return rotated;
  }, later);
  assert.equal(used, "refresh-1");
  assert.equal(a.accessToken, rotated.accessToken);
  const after = jar(a.setCookies);
  assert.equal(readRefresh(SECRET, after, later)?.rt, "refresh-2");
});

test("a refresh without rotation keeps the stored refresh token and its expiry", async () => {
  const cookies = jar(sessionCookies(SECRET, TOKENS, null, NOW, false));
  const later = NOW + 3600;
  const a = await currentAccess(SECRET, cookies, async () => ({ accessToken: jwt({ exp: later + 3600 }),
    refreshToken: null, expiresIn: 3600 }), later);
  assert.equal(a.setCookies.length, 1);                 // only the auth cookie; the refresh cookie stays
  assert.match(a.setCookies[0], new RegExp(`^${cookieName("auth", false)}=`));
});

test("a refused refresh signs out and clears both cookies", async () => {
  const cookies = jar(sessionCookies(SECRET, TOKENS, null, NOW, false));
  const a = await currentAccess(SECRET, cookies, async () => {
    throw new Error("NotAuthorizedException");
  }, NOW + 4000);
  assert.equal(a.accessToken, null);
  assert.deepEqual(a.setCookies.map((c) => c.split("=")[0]), ["tp_auth", "tp_refresh"]);
  assert.ok(a.setCookies.every((c) => c.includes("Max-Age=0")));
});

test("no cookies, or no secret, is signed out with nothing to clear", async () => {
  const none = { get: () => undefined };
  assert.deepEqual(await currentAccess(SECRET, none, async () => TOKENS, NOW),
    { accessToken: null, email: null, emailVerified: false, setCookies: [] });
  const cookies = jar(sessionCookies(SECRET, TOKENS, null, NOW, false));
  assert.equal((await currentAccess("", cookies, async () => TOKENS, NOW)).accessToken, null);
});

test("the pending sign-in step round-trips for 10 minutes only", () => {
  const set = pendingCookie(SECRET, { email: "t@example.com", mode: "signin", session: "s" }, NOW, false);
  assert.match(set, /^tp_signin=.*; Max-Age=600; HttpOnly; SameSite=Lax$/);
  const cookies = jar([set]);
  assert.deepEqual(readPending(SECRET, cookies, NOW + 599), { email: "t@example.com", mode: "signin", session: "s" });
  assert.equal(readPending(SECRET, cookies, NOW + 600), null);
});

test("access claims are read without trusting them for anything but display", () => {
  assert.deepEqual(accessClaims(jwt({ exp: 5, email: "a@b.co", email_verified: true })),
    { exp: 5, email: "a@b.co", ev: true });
  assert.deepEqual(accessClaims("garbage"), { exp: 0, email: null, ev: false });
});
