// AA-1111 (S294 ZAP "Big Redirect Detected"): the bare /{slug} of an agency is redirected by proxy.ts with no body
// (lib/tenantRoot.ts). Run: npm test.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { tenantPlannerPath, tenantRootSlug } from "./tenantRoot.ts";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");

test("the bare site of a slug, with or without the trailing slash", () => {
  assert.equal(tenantRootSlug("/quillmere-voyages"), "quillmere-voyages");
  assert.equal(tenantRootSlug("/quillmere-voyages/"), "quillmere-voyages");
  assert.equal(tenantPlannerPath("quillmere-voyages"), "/quillmere-voyages/plan");
});

test("not a bare site: the home, sub-paths, static routes, public files, a malformed slug", () => {
  for (const p of ["/", "", "/quillmere-voyages/plan", "/quillmere-voyages/tours/x", "/plan", "/tours", "/destinations",
    "/privacy", "/terms", "/api", "/brand", "/hero", "/sign-in", "/trips", "/robots.txt", "/sitemap.xml", "/icon.svg",
    "/favicon.ico", "/Quillmere", "/-quill", "//quill"]) {
    assert.equal(tenantRootSlug(p), null, p);
  }
});

test("proxy.ts answers the bare site itself, with NextResponse.redirect (no body), before the page renders", () => {
  const src = readFileSync(join(ROOT, "proxy.ts"), "utf8");
  assert.match(src, /const root = tenantRootSlug\(req\.nextUrl\.pathname\);\s*if \(root\) return tenantRoot\(req, root\);/);
  assert.match(src, /NextResponse\.redirect\(new URL\(`\$\{tenantPlannerPath\(slug\)\}/);
  assert.match(src, /, 307\)/);
  // An agency's own domain: "/" (rewritten to "/{slug}") gets the same redirect.
  assert.match(src, /if \(tenantRootSlug\(path\) === slug\) return plannerRedirect\(req, slug\);/);
  // A slug that is no served tenant, or a lookup that fails, goes on to the page (404 / retryable error).
  assert.match(src, /catch \{\s*return NextResponse\.next\(\);\s*\}\s*return site \? plannerRedirect\(req, site\.slug\) : NextResponse\.next\(\);/);
});
