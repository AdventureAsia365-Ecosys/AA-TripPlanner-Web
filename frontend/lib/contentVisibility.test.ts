// AA-995 (spec §6 step 5, R14) — content-visibility on below-fold sections only, with a remembered block size.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { cvSize } from "./contentVisibility.ts";

const read = (f: string) => readFileSync(new URL(`../${f}`, import.meta.url), "utf8");
const css = read("app/globals.css").replace(/\/\*[\s\S]*?\*\//g, "");

test("cvSize sets the estimate as a whole px value", () => {
  assert.deepEqual(cvSize(420), { "--tp-cv-h": "420px" });
  assert.deepEqual(cvSize(-3), { "--tp-cv-h": "0px" });
  assert.deepEqual(cvSize(99.6), { "--tp-cv-h": "100px" });
});

test(".tp-cv: auto + a remembered block size only (no fake inline size for grid / flex parents)", () => {
  const rule = css.match(/\.tp-cv\s*\{([^}]*)\}/);
  assert.ok(rule, ".tp-cv rule exists");
  assert.match(rule[1], /content-visibility:\s*auto;/);
  assert.match(rule[1], /contain-intrinsic-block-size:\s*auto\s+var\(--tp-cv-h/);
  assert.doesNotMatch(rule[1], /contain-intrinsic-size\s*:/);
});

test("the first screens and the sections with an AI label popover stay off content-visibility", () => {
  const dest = read("app/destinations/[country]/page.tsx");
  // The tours section is the #tours anchor target ("See the tours" in the hero): never skipped.
  assert.match(dest, /<section id="tours"(?![^>]*tp-cv)[^>]*>/);
  const tour = read("app/tours/[slug]/page.tsx");
  assert.match(tour, /<section(?![^>]*tp-cv)[^>]*aria-labelledby="itinerary"/);
  assert.match(tour, /<section(?![^>]*tp-cv)[^>]*aria-labelledby="highlights"/);
  // Below-fold sections do use it.
  assert.match(tour, /className="tp-cv[^"]*"[^>]*aria-labelledby="route-map"/);
  assert.match(dest, /aria-labelledby="places" className="tp-cv/);
});
