// TL-24 (S262) — until go-live every deployment is a preview: noindex everywhere, no sitemap line, a footer note.
// Run: npm test (node --test with type stripping; no extra dependencies).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  PREVIEW_ROBOTS_HEADER,
  PREVIEW_SITE_NOTE,
  SITE_INDEXING_ENV,
  isLiveSite,
  layoutRobots,
  robotsFor,
} from "./siteMode.ts";
import { previewHeaders } from "../next.config.mjs";

const src = (rel: string) => readFileSync(new URL(`../${rel}`, import.meta.url), "utf8");
const BASE = "https://planner.example.test";

test("only SITE_INDEXING=on marks the live site", () => {
  assert.equal(SITE_INDEXING_ENV, "SITE_INDEXING");
  for (const v of ["on", "ON", " On "]) assert.equal(isLiveSite({ SITE_INDEXING: v }), true, v);
  for (const v of [undefined, "", "off", "true", "1", "yes", "production"]) {
    assert.equal(isLiveSite({ SITE_INDEXING: v }), false, String(v));
  }
  // The Dev host is a Vercel production deployment: VERCEL_ENV alone must not make it live.
  assert.equal(isLiveSite({ VERCEL_ENV: "production" }), false);
});

test("a preview asks robots not to index or follow; the live site leaves it to each page", () => {
  assert.deepEqual(layoutRobots(false), { index: false, follow: false });
  assert.equal(layoutRobots(true), undefined);
});

test("robots.txt keeps the crawl rules on a preview but lists the sitemap only on the live site", () => {
  const rules = [{ userAgent: "*", allow: "/", disallow: ["/api/", "/plan"] }];
  assert.deepEqual(robotsFor(true, BASE), { rules, sitemap: `${BASE}/sitemap.xml` });
  assert.deepEqual(robotsFor(false, BASE), { rules });
  // Never a site-wide Disallow: crawlers must reach a page to read its noindex.
  const preview = robotsFor(false, BASE).rules;
  for (const r of Array.isArray(preview) ? preview : [preview]) {
    assert.ok(![r.disallow].flat().includes("/"), "a preview must not disallow the whole site");
  }
});

test("next.config.mjs sends X-Robots-Tag on a preview, with the same rule as isLiveSite()", () => {
  for (const env of [{}, { SITE_INDEXING: "off" }, { SITE_INDEXING: "on" }, { SITE_INDEXING: " ON " }]) {
    const headers = previewHeaders(env);
    if (isLiveSite(env)) assert.deepEqual(headers, [], JSON.stringify(env));
    else assert.deepEqual(headers, [{ key: "X-Robots-Tag", value: PREVIEW_ROBOTS_HEADER }], JSON.stringify(env));
  }
  assert.match(src("next.config.mjs"), /headers: \[\.\.\.SECURITY_HEADERS, \.\.\.previewHeaders\(\)\]/);
});

test("the root layout, robots.ts and the footer use the site mode", () => {
  assert.match(src("app/layout.tsx"), /robots: layoutRobots\(isLiveSite\(\)\)/);
  assert.match(src("app/robots.ts"), /robotsFor\(isLiveSite\(\), siteUrl\(\)\)/);
  const footer = src("components/site/SiteFooter.tsx");
  assert.match(footer, /const preview = !isLiveSite\(\)/);
  assert.match(footer, /\{PREVIEW_SITE_NOTE\}/);
});

test("the preview note says what the site is, in plain English, without issue ids", () => {
  assert.match(PREVIEW_SITE_NOTE, /^Preview site/);
  assert.match(PREVIEW_SITE_NOTE, /not handled as real enquiries/);
  assert.doesNotMatch(PREVIEW_SITE_NOTE, /\b(?:AA|TL)-\d+/);
});
