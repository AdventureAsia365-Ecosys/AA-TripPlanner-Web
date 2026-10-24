// AA-908: sealed session cookies (lib/seal.ts). Run: npm test.
import { test } from "node:test";
import assert from "node:assert/strict";
import { open, seal } from "./seal.ts";

const SECRET = "s".repeat(48);

test("a sealed value opens with the same secret and purpose", () => {
  const v = seal(SECRET, "session", { at: "a.b.c", exp: 2000 });
  assert.deepEqual(open(SECRET, "session", v, 1000), { at: "a.b.c", exp: 2000 });
});

test("a wrong secret, a wrong purpose, an expired value or a changed byte opens as null", () => {
  const v = seal(SECRET, "session", { at: "x", exp: 2000 });
  assert.equal(open("t".repeat(48), "session", v, 1000), null);
  assert.equal(open(SECRET, "refresh", v, 1000), null);
  assert.equal(open(SECRET, "session", v, 2000), null);
  const raw = Buffer.from(v, "base64url");
  raw[raw.length - 20] ^= 1;
  assert.equal(open(SECRET, "session", raw.toString("base64url"), 1000), null);
  for (const bad of [undefined, null, "", "garbage", "x".repeat(9000)]) assert.equal(open(SECRET, "session", bad), null);
});

test("each seal is different (random IV) and holds no plaintext", () => {
  const a = seal(SECRET, "signin", { email: "a@b.co", exp: 2000 });
  const b = seal(SECRET, "signin", { email: "a@b.co", exp: 2000 });
  assert.notEqual(a, b);
  assert.equal(Buffer.from(a, "base64url").toString("latin1").includes("a@b.co"), false);
});
