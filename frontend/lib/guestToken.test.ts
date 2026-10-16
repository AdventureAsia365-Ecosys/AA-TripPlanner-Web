// AA-827: the guest token the BFF keeps in an HttpOnly cookie, and the browser's guest ids.
// Run: npm test (node --test with type stripping; no extra dependencies).
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  GUEST_COOKIE_MAX_AGE,
  guestCookieName,
  guestCookieOptions,
  isGuestToken,
  isSameOrigin,
  mintGuestToken,
} from "./guestToken.ts";
import { isUuid, randomUuid, uuidFromBytes } from "./guestIds.ts";

function headers(h: Record<string, string>) {
  return { headers: { get: (name: string) => h[name.toLowerCase()] ?? null } };
}

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

test("production cookie is __Host- prefixed, HttpOnly, Secure, Lax, Path=/, 90 days", () => {
  assert.equal(guestCookieName(true), "__Host-tp_guest");
  assert.deepEqual(guestCookieOptions(true), {
    httpOnly: true, secure: true, sameSite: "lax", path: "/", maxAge: 90 * 24 * 3600,
  });
  assert.equal(GUEST_COOKIE_MAX_AGE, 7776000);
});

test("local http dev cookie has no __Host- prefix and no Secure, still HttpOnly", () => {
  assert.equal(guestCookieName(false), "tp_guest");
  const o = guestCookieOptions(false);
  assert.equal(o.secure, false);
  assert.equal(o.httpOnly, true);
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
