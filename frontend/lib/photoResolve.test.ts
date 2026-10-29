// AA-995 — server-side resolve of the LCP catalog photo (spec §6.1 phase 1): size variants, redirect handling and the
// fallback to the catalog URL whenever the hop cannot be resolved.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  PHOTO_CDN_ORIGIN,
  PHOTO_PROXY_PREFIX,
  cacheMaxAge,
  heroFiles,
  preconnectOrigin,
  redirectTarget,
  resolveHeroPhoto,
  resolvePhotoUrl,
  resolveRedirect,
  resolvePhotoMap,
  sameFileAsHero,
  sameOriginPhotoUrl,
  sizedPhotoUrl,
  uniquePhotoUrls,
} from "./photoResolve.ts";

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

// CIS today: small and large redirect to the CDN file, medium is not a size yet (422).
function catalogAnswer(cdn: string, medium: "422" | "redirect" = "422") {
  return (async (input: string | URL | Request) => {
    const u = String(input);
    if (u.includes("size=medium")) {
      return medium === "422"
        ? new Response("{}", { status: 422 })
        : new Response(null, { status: 302, headers: { location: `${cdn}/a-960.webp`, "cache-control": CDN_CC } });
    }
    const location = u.includes("size=small") ? `${cdn}/a-600.webp` : `${cdn}/a-1600.webp`;
    return new Response(null, { status: 302, headers: { location, "cache-control": CDN_CC } });
  }) as typeof fetch;
}

test("resolveHeroPhoto builds a 600 w / 1600 w srcset from the two resolved sizes", async () => {
  await withFetch(
    catalogAnswer("https://img.example"),
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

test("resolveHeroPhoto serves the photo CDN's files same-origin, without a preconnect", async () => {
  await withFetch(catalogAnswer(PHOTO_CDN_ORIGIN + "/bhutan"), async () => {
    assert.deepEqual(await resolveHeroPhoto(`${P}?size=large`), {
      src: "/photo-cdn/bhutan/a-1600.webp",
      srcSet: "/photo-cdn/bhutan/a-600.webp 600w, /photo-cdn/bhutan/a-1600.webp 1600w",
      preconnect: null,
    });
  });
});

test("resolveHeroPhoto adds the 960 w file once the catalog redirects size=medium", async () => {
  await withFetch(catalogAnswer(PHOTO_CDN_ORIGIN + "/bhutan", "redirect"), async () => {
    const h = await resolveHeroPhoto(`${P}?size=large`);
    assert.equal(
      h?.srcSet,
      "/photo-cdn/bhutan/a-600.webp 600w, /photo-cdn/bhutan/a-960.webp 960w, /photo-cdn/bhutan/a-1600.webp 1600w",
    );
  });
});

test("resolveRedirect is null when there is no usable redirect; resolvePhotoUrl then keeps the catalog URL", async () => {
  await withFetch((async () => new Response("{}", { status: 422 })) as typeof fetch, async () => {
    assert.equal(await resolveRedirect(`${P}?size=medium`), null);
    assert.equal(await resolvePhotoUrl(`${P}?size=small`), `${P}?size=small`);
  });
  await withFetch((async () => { throw new Error("down"); }) as typeof fetch, async () => {
    assert.equal(await resolveRedirect(`${P}?size=small`), null);
  });
});

test("uniquePhotoUrls keeps the distinct non-empty URLs in first-seen order, capped", () => {
  assert.deepEqual(uniquePhotoUrls([`${P}?size=small`, null, "", `${P}?size=small`, undefined, `${P}2?size=small`]), [
    `${P}?size=small`,
    `${P}2?size=small`,
  ]);
  assert.deepEqual(uniquePhotoUrls(["a", "b", "c"], 2), ["a", "b"]);
  assert.deepEqual(uniquePhotoUrls([]), []);
});

test("resolvePhotoMap: each distinct URL looked up once, same-origin, failures left out (the caller keeps the catalog URL)", async () => {
  const seen: string[] = [];
  await withFetch(
    (async (input: string | URL | Request) => {
      const u = String(input);
      seen.push(u);
      if (u.includes("/bad")) return new Response("{}", { status: 500 });
      if (u.includes("/presigned")) return new Response(null, { status: 302, headers: { location: PRESIGNED, "cache-control": PRESIGN_CC } });
      if (u.includes("/other")) return new Response(null, { status: 302, headers: { location: CDN, "cache-control": CDN_CC } });
      return new Response(null, { status: 302, headers: { location: `${PHOTO_CDN_ORIGIN}/vn/a-600.webp?v=1`, "cache-control": CDN_CC } });
    }) as typeof fetch,
    async () => {
      const m = await resolvePhotoMap([`${P}?size=small`, `${P}?size=small`, null, `${P}/bad`, `${P}/presigned`, `${P}/other`]);
      assert.deepEqual([...m.entries()], [
        [`${P}?size=small`, "/photo-cdn/vn/a-600.webp?v=1"],
        // A resolved file on another host still skips the 302 hop; it is just not same-origin.
        [`${P}/other`, CDN],
      ]);
      assert.equal(seen.filter((u) => u === `${P}?size=small`).length, 1);
    },
  );
  assert.equal((await resolvePhotoMap([null, undefined])).size, 0);
});

test("resolvePhotoMap shares one deadline: every lookup gets the same signal, a thrown lookup only drops its URL", async () => {
  const signals = new Set<AbortSignal | null | undefined>();
  await withFetch(
    (async (input: string | URL | Request, init?: RequestInit) => {
      signals.add(init?.signal);
      if (String(input).includes("/slow")) throw new Error("aborted");
      return new Response(null, { status: 302, headers: { location: `${PHOTO_CDN_ORIGIN}/la/b-600.webp`, "cache-control": CDN_CC } });
    }) as typeof fetch,
    async () => {
      const m = await resolvePhotoMap([`${P}/a`, `${P}/slow`, `${P}/b`]);
      assert.deepEqual([...m.keys()], [`${P}/a`, `${P}/b`]);
      assert.equal(signals.size, 1);
      assert.ok([...signals][0] instanceof AbortSignal);
    },
  );
});

test("resolvePhotoMap looks up at most RESOLVE_MAX URLs (the rest keep the catalog URL)", async () => {
  let calls = 0;
  await withFetch(
    (async () => {
      calls += 1;
      return new Response(null, { status: 302, headers: { location: `${PHOTO_CDN_ORIGIN}/x/c.webp`, "cache-control": CDN_CC } });
    }) as typeof fetch,
    async () => {
      const m = await resolvePhotoMap(Array.from({ length: 30 }, (_, i) => `${P}/${i}`), 5);
      assert.equal(m.size, 5);
      assert.equal(calls, 5);
    },
  );
});

test("sameOriginPhotoUrl rewrites only the photo CDN host and keeps path and query", () => {
  assert.equal(sameOriginPhotoUrl(`${PHOTO_CDN_ORIGIN}/vn/f-600.webp?v=ab12`), "/photo-cdn/vn/f-600.webp?v=ab12");
  assert.equal(sameOriginPhotoUrl("https://img.example/vn/f.webp"), "https://img.example/vn/f.webp");
  assert.equal(sameOriginPhotoUrl(`${P}?size=large`), `${P}?size=large`);
  assert.equal(sameOriginPhotoUrl("not a url"), "not a url");
});

test("next.config.mjs proxies the same prefix to the same CDN host, with rewrite caching on", () => {
  const cfg = readFileSync(new URL("../next.config.mjs", import.meta.url), "utf8");
  assert.ok(cfg.includes(`const PHOTO_CDN_ORIGIN = "${PHOTO_CDN_ORIGIN}";`), "PHOTO_CDN_ORIGIN differs from next.config.mjs");
  assert.ok(cfg.includes(`const PHOTO_PROXY_PREFIX = "${PHOTO_PROXY_PREFIX}";`), "PHOTO_PROXY_PREFIX differs from next.config.mjs");
  assert.match(cfg, /x-vercel-enable-rewrite-caching", value: "1"/);
});

test("sameFileAsHero matches a card photo that is one of the hero's files, compared on the resolved URL", () => {
  const hero = {
    src: "/photo-cdn/bt/f-1600.webp?v=1",
    srcSet: "/photo-cdn/bt/f-600.webp?v=1 600w, /photo-cdn/bt/f-960.webp?v=1 960w, /photo-cdn/bt/f-1600.webp?v=1 1600w",
    preconnect: null,
  };
  assert.deepEqual(heroFiles(hero), [
    "/photo-cdn/bt/f-1600.webp?v=1", "/photo-cdn/bt/f-600.webp?v=1", "/photo-cdn/bt/f-960.webp?v=1", "/photo-cdn/bt/f-1600.webp?v=1",
  ]);
  assert.equal(sameFileAsHero("/photo-cdn/bt/f-600.webp?v=1", hero), hero);
  // Another photo, another version of the file, or no resolved card photo: an ordinary card.
  assert.equal(sameFileAsHero("/photo-cdn/bt/g-600.webp?v=1", hero), null);
  assert.equal(sameFileAsHero("/photo-cdn/bt/f-600.webp?v=2", hero), null);
  assert.equal(sameFileAsHero(null, hero), null);
  assert.equal(sameFileAsHero("/photo-cdn/bt/f-600.webp?v=1", null), null);
  // A hero without a srcset (the small size did not resolve) still matches its src.
  const bare = { src: "/photo-cdn/bt/f-1600.webp?v=1", srcSet: null, preconnect: null };
  assert.equal(sameFileAsHero("/photo-cdn/bt/f-1600.webp?v=1", bare), bare);
  assert.deepEqual(heroFiles(null), []);
});
