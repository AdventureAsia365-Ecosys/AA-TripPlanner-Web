// AA-904: Next 15 reads route segment config (`export const revalidate`) statically, so the pages
// must use a number literal, not the imported CATALOG_REVALIDATE_S (the build fails on it). This
// test keeps the literals equal to the constant, so the page ISR window and the catalog fetch
// revalidate cannot drift apart.
// Run: npm test (node --test with type stripping; no extra dependencies).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { CATALOG_REVALIDATE_S } from "./catalog.ts";

const CATALOG_PAGES = [
  "app/page.tsx",
  "app/destinations/page.tsx",
  "app/destinations/[country]/page.tsx",
  "app/tours/[slug]/page.tsx",
];

for (const page of CATALOG_PAGES) {
  test(`${page} revalidates with the catalog TTL as a literal`, () => {
    const src = readFileSync(new URL(`../${page}`, import.meta.url), "utf8");
    const m = src.match(/^export const revalidate = (\d+);$/m);
    assert.ok(m, `${page}: expected "export const revalidate = <number>;"`);
    assert.equal(Number(m[1]), CATALOG_REVALIDATE_S);
  });
}
