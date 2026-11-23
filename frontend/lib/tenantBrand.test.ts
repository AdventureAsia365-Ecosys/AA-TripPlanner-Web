// AA-1160 (scope 3) — an agency's theme reaches the whole accent family and the band accent (lib/tenantBrand.ts).
// S285: with an accent and a deep colour set, the dashed "Start building" box (accent-border), tints and the band's
// "TRIP PLANNER" label stayed the kit's gold. Run: npm test.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { KIT_DARK_CARD, KIT_LIGHT, contrast, resolveBrandTheme } from "../ui/core/brandTheme.ts";
import {
  BAND_DARK, BAND_LIGHT, KIT_DARK_BG, bandAccent, completeTheme, darkFamily, mix, plannerBrandCss, readableShade,
} from "./tenantBrand.ts";

// An invented agency brand: a teal accent and a deep teal for text (the S285 rehearsal tenant had a theme like it).
const TEAL = { accent: "#1f6f8b", "accent-deep": "#14485a" };
const KIT_GOLDS = ["#db9628", "#875d12", "#f4e2c2", "#fbf3e3", "#dda64e", "#e8a838", "#f0c070", "#4a3a1c", "#332814",
  "#6b531f", "#e8b865"];

test("mix and readableShade", () => {
  assert.equal(mix("#000000", "#ffffff", 0.5), "#808080");
  assert.equal(mix("#1f6f8b", "#1f6f8b", 0.3), "#1f6f8b");
  const shade = readableShade("#1f6f8b", "#ffffff", [BAND_LIGHT]);
  assert.ok(shade && contrast(shade, BAND_LIGHT) >= 4.5);
  assert.equal(readableShade("#777777", "#777777", ["#787878"]), null);   // cannot be made to read
});

test("the light accent family is derived from the accent; the agency's own tokens win", () => {
  const full = completeTheme(TEAL) as Record<string, string>;
  assert.equal(full.accent, "#1f6f8b");
  assert.equal(full["accent-deep"], "#14485a");                          // the agency's own
  for (const t of ["accent-tint", "accent-soft", "accent-border"]) assert.match(full[t], /^#[0-9a-f]{6}$/);
  // Every derived token survives the kit's contrast pairs (nothing is dropped back to gold).
  const resolved = resolveBrandTheme(full);
  assert.deepEqual(resolved.dropped, []);
  for (const t of ["accent", "accent-deep", "accent-soft", "accent-tint", "accent-border"]) {
    assert.ok(resolved.light[t as "accent"], t);
  }
  // Only the accent given: a readable deep shade is derived too.
  const derived = completeTheme({ accent: "#1f6f8b" }) as Record<string, string>;
  for (const s of [KIT_LIGHT.card, KIT_LIGHT.bg, derived["accent-tint"], derived["accent-soft"]]) {
    assert.ok(contrast(derived["accent-deep"], s) >= 4.5, s);
  }
  assert.deepEqual(completeTheme({}), {});                                // no accent: unchanged
  assert.equal(completeTheme(null), null);
});

test("the dark family and the band accent read on their surfaces", () => {
  const dark = darkFamily(TEAL);
  for (const s of [KIT_DARK_CARD, KIT_DARK_BG, dark["accent-tint"]!, dark["accent-soft"]!]) {
    assert.ok(contrast(dark["accent-deep"]!, s) >= 4.5, s);
  }
  const band = bandAccent(TEAL);
  assert.ok(band.light && contrast(band.light, BAND_LIGHT) >= 4.5);
  assert.ok(band.dark && contrast(band.dark, BAND_DARK) >= 4.5);
  assert.deepEqual(bandAccent({ accent: "teal" }), { light: null, dark: null });
});

test("plannerBrandCss: no kit gold left under an agency theme, nothing added without one", () => {
  const css = plannerBrandCss(TEAL);
  for (const token of ["--aa-accent-border", "--aa-accent-tint", "--aa-accent-soft", "--tp-band-accent"]) {
    assert.ok(css.includes(`${token}:`), token);
  }
  assert.ok(css.includes("html[data-theme=dark] body{--aa-accent-deep:"), "the dark family block");
  for (const gold of KIT_GOLDS) assert.ok(!css.toLowerCase().includes(gold), gold);
  assert.ok(!/["'<>&]/.test(css), "the block renders as plain <style> text");
  assert.equal(plannerBrandCss({}), "");
  assert.equal(plannerBrandCss(null), "");
});

test("the surfaces this module checks against are the token files' values", () => {
  const core = readFileSync(new URL("../ui/core/tokens.css", import.meta.url), "utf8").toLowerCase();
  const own = readFileSync(new URL("../ui/tokens.css", import.meta.url), "utf8").toLowerCase();
  const darkCore = core.slice(core.indexOf('[data-theme="dark"]'));
  assert.ok(darkCore.includes(`--aa-bg:            ${KIT_DARK_BG}`));
  assert.ok(own.includes(`--tp-band:          ${BAND_LIGHT}`));
  assert.ok(own.slice(own.indexOf("dark")).includes(`--tp-band:          ${BAND_DARK}`));
});

test("every agency page renders the planner brand block, not the kit's alone", () => {
  const root = new URL("../app/[tenant]/", import.meta.url).pathname;
  const pages: string[] = [];
  const walk = (dir: string) => {
    for (const name of readdirSync(dir)) {
      const p = join(dir, name);
      if (statSync(p).isDirectory()) walk(p);
      else if (name === "page.tsx") pages.push(p);
    }
  };
  walk(root);
  for (const p of pages) {
    const src = readFileSync(p, "utf8");
    assert.ok(!src.includes("brandThemeCss("), `${p} uses the kit block alone`);
    if (src.includes("tenant-brand")) assert.ok(src.includes("plannerBrandCss("), p);
  }
});
