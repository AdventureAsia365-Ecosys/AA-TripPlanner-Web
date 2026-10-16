// AA-827: the guest token the BFF keeps in an HttpOnly cookie, and the browser's guest ids.
// Run: npm test (node --test with type stripping; no extra dependencies).
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  COOKIES_REQUIRED,
  GUEST_COOKIE_MAX_AGE,
  guestCookieAction,
  guestCookieName,
  isGuestToken,
  isSameOrigin,
  mapMintedDenial,
  mintGuestToken,
  serializeGuestCookie,
} from "./guestToken.ts";
import {
  COOKIES_REQUIRED as CLIENT_COOKIES_REQUIRED,
  RESET_LOOP_MS,
  isResetLoop,
  isUuid,
  randomUuid,
  storedIdsMatch,
  uuidFromBytes,
} from "./guestIds.ts";

function headers(h: Record<string, string>) {
  return { headers: { get: (name: string) => h[name.toLowerCase()] ?? null } };
}

function request(method: string, h: Record<string, string>) {
  return { method, ...headers(h) };
}

const HOST = { host: "plan.example.com" };
const fixedMint = () => "M".repeat(43);

test("a minted token is 43 base64url characters (32 random bytes)", () => {
  for (let i = 0; i < 50; i++) {
    const t = mintGuestToken();
    assert.equal(t.length, 43);
    assert.match(t, /^[A-Za-z0-9_-]{43}$/);
    assert.ok(isGuestToken(t));
  }
});

test("1000 mints are all different", () => {
  const seen = new Set<string>();
  for (let i = 0; i < 1000; i++) seen.add(mintGuestToken());
  assert.equal(seen.size, 1000);
});

test("isGuestToken rejects anything that is not exactly a token", () => {
  const t = mintGuestToken();
  for (const bad of [undefined, null, 42, "", t.slice(1), t + "a", t + "\n", t.slice(1) + "=", t.slice(1) + "+",
    "3f0c8a8e-1d2b-4c5d-8e9f-0a1b2c3d4e5f"]) {
    assert.equal(isGuestToken(bad), false, String(bad));
  }
});

test("production cookie is __Host- prefixed, HttpOnly, Secure, Lax, Path=/, 400 days", () => {
  const t = mintGuestToken();
  assert.equal(guestCookieName(true), "__Host-tp_guest");
  assert.equal(
    serializeGuestCookie(t, true),
    `__Host-tp_guest=${t}; Path=/; Max-Age=34560000; HttpOnly; SameSite=Lax; Secure`,
  );
  assert.equal(GUEST_COOKIE_MAX_AGE, 400 * 24 * 3600);
});

test("local http dev cookie has no __Host- prefix and no Secure, still HttpOnly", () => {
  const t = mintGuestToken();
  assert.equal(guestCookieName(false), "tp_guest");
  const c = serializeGuestCookie(t, false);
  assert.equal(c, `tp_guest=${t}; Path=/; Max-Age=34560000; HttpOnly; SameSite=Lax`);
  assert.doesNotMatch(c, /Secure/);
});

test("cookie action: a cross-site POST is rejected before any mint", () => {
  let mints = 0;
  const mint = () => { mints++; return fixedMint(); };
  for (const method of ["POST", "PUT", "PATCH", "DELETE"]) {
    const a = guestCookieAction(request(method, { ...HOST, origin: "https://evil.example",
      "sec-fetch-site": "cross-site" }), undefined, mint);
    assert.deepEqual(a, { kind: "reject" }, method);
    // with a valid cookie too (a browser would not send it, but a non-browser might)
    assert.deepEqual(guestCookieAction(request(method, { ...HOST, origin: "null" }), mintGuestToken(), mint),
      { kind: "reject" });
  }
  assert.equal(mints, 0);
});

test("cookie action: a cross-site non-navigation request leaves the cookie alone", () => {
  for (const [method, mode] of [["GET", "no-cors"], ["GET", "cors"], ["HEAD", "nested-navigate"]]) {
    const a = guestCookieAction(request(method, { ...HOST, "sec-fetch-site": "cross-site",
      "sec-fetch-mode": mode }), undefined, fixedMint);
    assert.deepEqual(a, { kind: "pass", token: null, minted: false }, `${method} ${mode}`);
  }
});

test("cookie action: a cross-site top-level GET (a link from CIS) mints or renews", () => {
  const nav = { ...HOST, "sec-fetch-site": "cross-site", "sec-fetch-mode": "navigate" };
  assert.deepEqual(guestCookieAction(request("GET", nav), undefined, fixedMint),
    { kind: "pass", token: fixedMint(), minted: true });
  const t = mintGuestToken();
  assert.deepEqual(guestCookieAction(request("GET", nav), t, fixedMint), { kind: "pass", token: t, minted: false });
});

test("cookie action: same-origin requests renew a valid cookie and mint for a missing or bad one", () => {
  const t = mintGuestToken();
  const same = { ...HOST, origin: "https://plan.example.com", "sec-fetch-site": "same-origin" };
  assert.deepEqual(guestCookieAction(request("POST", same), t, fixedMint), { kind: "pass", token: t, minted: false });
  assert.deepEqual(guestCookieAction(request("POST", same), undefined, fixedMint),
    { kind: "pass", token: fixedMint(), minted: true });
  assert.deepEqual(guestCookieAction(request("POST", same), t.slice(1), fixedMint),
    { kind: "pass", token: fixedMint(), minted: true });
  // a typed-in URL / bookmark (sec-fetch-site none) and a non-browser GET
  assert.deepEqual(guestCookieAction(request("GET", { ...HOST, "sec-fetch-site": "none" }), t, fixedMint),
    { kind: "pass", token: t, minted: false });
  assert.deepEqual(guestCookieAction(request("GET", HOST), undefined, fixedMint),
    { kind: "pass", token: fixedMint(), minted: true });
});

test("a refused minted token becomes 401 cookies_required; everything else passes through", () => {
  const invalid = JSON.stringify({ error: "guest_session_invalid" });
  assert.deepEqual(mapMintedDenial(401, invalid, true), {
    status: 401, body: JSON.stringify({ error: "cookies_required" }),
  });
  assert.equal(mapMintedDenial(401, invalid, false), null); // the cookie came in: a real mismatch
  assert.equal(mapMintedDenial(403, JSON.stringify({ error: "forbidden" }), true), null);
  assert.equal(mapMintedDenial(401, JSON.stringify({ error: "other" }), true), null);
  assert.equal(mapMintedDenial(401, "not json", true), null);
  assert.equal(mapMintedDenial(200, "{}", true), null);
  assert.equal(COOKIES_REQUIRED, CLIENT_COOKIES_REQUIRED);
});

test("reset deletes the stored ids only when they are the ones that failed", () => {
  const ids = { sessionId: randomUuid(), tripId: randomUuid() };
  assert.ok(storedIdsMatch(JSON.stringify(ids), ids));
  assert.equal(storedIdsMatch(JSON.stringify({ sessionId: randomUuid(), tripId: randomUuid() }), ids), false);
  assert.equal(storedIdsMatch(JSON.stringify({ ...ids, tripId: randomUuid() }), ids), false);
  assert.equal(storedIdsMatch(null, ids), false);
  assert.equal(storedIdsMatch("{", ids), false);
});

test("a second reset in the same tab within the loop window is a reset loop", () => {
  const now = 1_000_000_000;
  assert.equal(isResetLoop(null, now), false);
  assert.equal(isResetLoop("garbage", now), false);
  assert.ok(isResetLoop(String(now - 1000), now));
  assert.equal(isResetLoop(String(now - RESET_LOOP_MS), now), false);
  assert.equal(isResetLoop(String(now + 5000), now), false); // clock went back: not a loop
});

test("isSameOrigin: same host passes, another site or a null origin fails, no Origin passes", () => {
  assert.ok(isSameOrigin(headers({ origin: "https://plan.example.com", host: "plan.example.com" })));
  assert.ok(isSameOrigin(headers({ origin: "http://localhost:3000", host: "localhost:3000" })));
  assert.ok(isSameOrigin(headers({
    origin: "https://plan.example.com", host: "internal:3000", "x-forwarded-host": "plan.example.com",
  })));
  assert.ok(isSameOrigin(headers({ host: "plan.example.com" })));
  assert.equal(isSameOrigin(headers({ origin: "https://evil.example", host: "plan.example.com" })), false);
  assert.equal(isSameOrigin(headers({ origin: "null", host: "plan.example.com" })), false);
  assert.equal(isSameOrigin(headers({ origin: "https://plan.example.com" })), false);
});

test("guest ids are v4 UUIDs, with or without crypto.randomUUID", () => {
  assert.ok(isUuid(randomUuid()));
  const id = uuidFromBytes(new Uint8Array(16).fill(0xff));
  assert.equal(id, "ffffffff-ffff-4fff-bfff-ffffffffffff");
  assert.ok(isUuid(uuidFromBytes(new Uint8Array(16))));
  assert.equal(isUuid("k3j4h5" + Date.now().toString(36)), false); // the old Math.random fallback
  assert.equal(isUuid("3F0C8A8E-1D2B-4C5D-8E9F-0A1B2C3D4E5F"), false); // backend wants lowercase
});
