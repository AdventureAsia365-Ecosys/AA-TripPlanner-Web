// AA-995 — server-side resolve of the LCP catalog photo (spec §6.1 phase 1): size variants, redirect handling and the
// fallback to the catalog URL whenever the hop cannot be resolved.
import { test } from "node:test";
import assert from "node:assert/strict";
import { preconnectOrigin, redirectTarget, resolveHeroPhoto, resolvePhotoUrl, sizedPhotoUrl } from "./photoResolve.ts";

const P = "https://api.example/content/photos/abc";
const CDN = "https://img.example/vn/file-1600.webp?v=1";

test("sizedPhotoUrl swaps the size query and keeps the rest", () => {
  assert.equal(sizedPhotoUrl(`${P}?size=large`, "small"), `${P}?size=small`);
  assert.equal(sizedPhotoUrl(P, "large"), `${P}?size=large`);
  assert.equal(sizedPhotoUrl(null, "small"), null);
  assert.equal(sizedPhotoUrl("not a url", "small"), "not a url");
});

test("redirectTarget accepts only a redirect status with an absolute https Location", () => {
  assert.equal(redirectTarget(302, CDN), CDN);
  assert.equal(redirectTarget(301, CDN), CDN);
  assert.equal(redirectTarget(200, CDN), null);
  assert.equal(redirectTarget(302, null), null);
  assert.equal(redirectTarget(302, "/relative/path"), null);
  assert.equal(redirectTarget(302, "http://img.example/x.webp"), null);
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
    (async () => new Response(null, { status: 302, headers: { location: CDN } })) as typeof fetch,
    async () => assert.equal(await resolvePhotoUrl(`${P}?size=large`), CDN),
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
      return new Response(null, { status: 302, headers: { location: small ? "https://img.example/a-600.webp" : "https://img.example/a-1600.webp" } });
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
