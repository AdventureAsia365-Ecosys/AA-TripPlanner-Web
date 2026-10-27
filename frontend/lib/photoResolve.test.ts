// AA-995 — server-side resolve of the LCP catalog photo (spec §6.1 phase 1): size variants, redirect handling and the
// fallback to the catalog URL whenever the hop cannot be resolved.
import { test } from "node:test";
import assert from "node:assert/strict";
import { cacheMaxAge, preconnectOrigin, redirectTarget, resolveHeroPhoto, resolvePhotoUrl, sizedPhotoUrl } from "./photoResolve.ts";

const P = "https://api.example/content/photos/abc";
const CDN = "https://img.example/vn/file-1600.webp?v=1";
// What CIS sends: the CDN branch caches the redirect a day, the presign branch 3000 s.
const CDN_CC = "public, max-age=86400";
const PRESIGN_CC = "public, max-age=3000";
const PRESIGNED =
  "https://bucket.s3.us-west-1.amazonaws.com/places/k.webp?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AK%2F20261028&X-Amz-Expires=3600&X-Amz-Signature=abc";

test("sizedPhotoUrl swaps the size query and keeps the rest", () => {
  assert.equal(sizedPhotoUrl(`${P}?size=large`, "small"), `${P}?size=small`);
  assert.equal(sizedPhotoUrl(P, "large"), `${P}?size=large`);
  assert.equal(sizedPhotoUrl(null, "small"), null);
  assert.equal(sizedPhotoUrl("not a url", "small"), "not a url");
});

test("redirectTarget accepts only a redirect status with an absolute https Location", () => {
  assert.equal(redirectTarget(302, CDN, CDN_CC), CDN);
  assert.equal(redirectTarget(301, CDN, CDN_CC), CDN);
  assert.equal(redirectTarget(200, CDN, CDN_CC), null);
  assert.equal(redirectTarget(302, null, CDN_CC), null);
  assert.equal(redirectTarget(302, "/relative/path", CDN_CC), null);
  assert.equal(redirectTarget(302, "http://img.example/x.webp", CDN_CC), null);
});

test("redirectTarget refuses a target that cannot live in the ISR page (presigned or cached < 1 day)", () => {
  // The CIS presign branch: expiring URL, max-age 3000.
  assert.equal(redirectTarget(302, PRESIGNED, PRESIGN_CC), null);
  // Presign parameters alone are enough to refuse, whatever the header says.
  assert.equal(redirectTarget(302, PRESIGNED, CDN_CC), null);
  assert.equal(redirectTarget(302, "https://img.example/a.webp?x-amz-expires=60", CDN_CC), null);
  // A short-lived or uncacheable redirect, or one with no Cache-Control at all.
  assert.equal(redirectTarget(302, CDN, PRESIGN_CC), null);
  assert.equal(redirectTarget(302, CDN, "no-store"), null);
  assert.equal(redirectTarget(302, CDN, null), null);
  assert.equal(redirectTarget(302, CDN, "public, s-maxage=604800"), CDN);
});

test("cacheMaxAge reads max-age / s-maxage and treats no-store, no-cache and private as 0", () => {
  assert.equal(cacheMaxAge(CDN_CC), 86400);
  assert.equal(cacheMaxAge("Public, Max-Age=3000"), 3000);
  assert.equal(cacheMaxAge("max-age=60, s-maxage=86400"), 86400);
  assert.equal(cacheMaxAge("private, max-age=86400"), 0);
  assert.equal(cacheMaxAge("public"), null);
  assert.equal(cacheMaxAge(null), null);
});

test("preconnectOrigin names the CDN only when the photo moved to another host", () => {
  assert.equal(preconnectOrigin(CDN, `${P}?size=large`), "https://img.example");
  assert.equal(preconnectOrigin(`${P}?size=large`, `${P}?size=large`), null);
  assert.equal(preconnectOrigin(null, P), null);
});

function withFetch(impl: typeof fetch, run: () => Promise<void>) {
  const real = globalThis.fetch;
  globalThis.fetch = impl;
  return run().finally(() => {
    globalThis.fetch = real;
  });
}

test("resolvePhotoUrl follows one 302 and falls back to the catalog URL on any failure", async () => {
  await withFetch(
    (async () => new Response(null, { status: 302, headers: { location: CDN, "cache-control": CDN_CC } })) as typeof fetch,
    async () => assert.equal(await resolvePhotoUrl(`${P}?size=large`), CDN),
  );
  // The CIS presign branch keeps the catalog URL (an expired signature would break the cached page's LCP image).
  await withFetch(
    (async () => new Response(null, { status: 302, headers: { location: PRESIGNED, "cache-control": PRESIGN_CC } })) as typeof fetch,
    async () => assert.equal(await resolvePhotoUrl(`${P}?size=large`), `${P}?size=large`),
  );
  await withFetch(
    (async () => new Response("img", { status: 200 })) as typeof fetch,
    async () => assert.equal(await resolvePhotoUrl(`${P}?size=large`), `${P}?size=large`),
  );
  await withFetch(
    (async () => {
      throw new Error("timeout");
    }) as typeof fetch,
    async () => assert.equal(await resolvePhotoUrl(`${P}?size=large`), `${P}?size=large`),
  );
  assert.equal(await resolvePhotoUrl(null), null);
});

test("resolveHeroPhoto builds a 600 w / 1600 w srcset from the two resolved sizes", async () => {
  await withFetch(
    (async (input: string | URL | Request) => {
      const small = String(input).includes("size=small");
      const location = small ? "https://img.example/a-600.webp" : "https://img.example/a-1600.webp";
      return new Response(null, { status: 302, headers: { location, "cache-control": CDN_CC } });
    }) as typeof fetch,
    async () => {
      const h = await resolveHeroPhoto(`${P}?size=large`);
      assert.deepEqual(h, {
        src: "https://img.example/a-1600.webp",
        srcSet: "https://img.example/a-600.webp 600w, https://img.example/a-1600.webp 1600w",
        preconnect: "https://img.example",
      });
    },
  );
  assert.equal(await resolveHeroPhoto(null), null);
});
