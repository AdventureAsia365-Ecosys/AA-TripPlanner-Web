// AA-837 (D10) / AA-995 — WCAG AA (4.5:1; all checked text is small) in BOTH themes for every pair that involves a
// TripPlanner-only `--tp-*` value (the sand and navy bands, the photo scrims), and no raw palette class in the map
// planner. Browser axe runs cover the rendered page (docs/implementation-notes/AA-837.md).
// AA-994 S5a: the `--aa-*` values come from the kit core (ui/core/tokens.css, a byte copy of CIS); their own pairs
// (ink / body / muted / accent-deep on card, bg, tints; on-accent on gold; red; neutral pills — the old PAIRS list
// here) are checked in AA-CIS-App lib/tokensContrast.test.mjs, so a token change fails there, before any sync.
// Run: npm test (node --test with type stripping; no extra dependencies).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { compositeOver, contrastRatio, parseHex, parseRgba, readThemeTokens } from "./contrast.ts";

// The kit core first, then the app file on top (the order of the imports in app/layout.tsx).
const core = readThemeTokens(readFileSync(new URL("../ui/core/tokens.css", import.meta.url), "utf8"));
const app = readThemeTokens(readFileSync(new URL("../ui/tokens.css", import.meta.url), "utf8"));
const themes = {
  light: { ...core.light, ...app.light },
  dark: { ...core.dark, ...app.dark },
};

test("the app tokens file defines only --tp-* values (every --aa-* value comes from the kit core)", () => {
  const css = readFileSync(new URL("../ui/tokens.css", import.meta.url), "utf8");
  assert.deepEqual(css.match(/(?<![\w-])--aa-[\w-]+\s*:/g) ?? [], []);
});

// AA-995 "Golden Hour" customer pages: the sand band and the navy band (header, CTA band, footer,
// no-photo tiles). Every pair is small text, so 4.5:1, in both themes.
const SITE_PAIRS: [string, string][] = [
  ["ink", "tp-surface-sand"], ["body", "tp-surface-sand"], ["muted", "tp-surface-sand"],
  ["accent-deep", "tp-surface-sand"],
  ["tp-on-band", "tp-band"], ["tp-on-band-muted", "tp-band"], ["tp-band-accent", "tp-band"],
  ["ink", "accent-tint"], ["accent-deep", "accent-tint"],
];

// Text over a photo: the worst case is a pure-white pixel under the scrim. [text, scrim, minimum]:
// small white text needs 4.5:1; the Satisfy hero kicker is large text (>= 28 px), so 3:1.
const SCRIM_PAIRS: [string, string, number][] = [
  ["#FFFFFF", "scrim", 4.5], // duration pill, photo captions, ribbon caption chips
  ["#FFFFFF", "tp-scrim-text", 4.5], // hero h1 and lede
  ["tp-band-accent", "tp-scrim-text", 3], // hero kicker in Satisfy (large text)
  ["tp-on-band", "tp-scrim-head", 4.5], // header links and the AI chip over the hero photo
  ["#FFFFFF", "tp-scrim-head", 4.5],
];

test("contrastRatio matches known WCAG values", () => {
  assert.equal(Math.round(contrastRatio("#000000", "#ffffff") * 100) / 100, 21);
  assert.equal(Math.round(contrastRatio("#DB9628", "#FFFFFF") * 100) / 100, 2.5); // the old gold label
  assert.equal(parseHex("rgba(0,0,0,0.5)"), null);
});

for (const theme of ["light", "dark"] as const) {
  test(`every customer-page band pair meets WCAG AA in the ${theme} theme`, () => {
    const t = themes[theme];
    const failures = SITE_PAIRS.map(([fg, bg]) => {
      assert.ok(t[fg] && t[bg], `token missing in ${theme}: ${t[fg] ? bg : fg}`);
      return { pair: `${fg} on ${bg}`, ratio: Math.round(contrastRatio(t[fg], t[bg]) * 100) / 100 };
    }).filter((r) => r.ratio < 4.5);
    assert.deepEqual(failures, []);
  });

  test(`text on photo scrims is readable over a white pixel in the ${theme} theme`, () => {
    const t = themes[theme];
    const failures = SCRIM_PAIRS.map(([fg, scrim, min]) => {
      assert.ok(t[scrim] && parseRgba(t[scrim]), `scrim missing or not rgba in ${theme}: ${scrim}`);
      const text = fg.startsWith("#") ? fg : t[fg];
      const ratio = contrastRatio(text, compositeOver(t[scrim], "#FFFFFF"));
      return { pair: `${fg} on ${scrim}`, ratio: Math.round(ratio * 100) / 100, min };
    }).filter((r) => r.ratio < r.min);
    assert.deepEqual(failures, []);
  });
}

test("compositeOver mixes a translucent scrim over a solid pixel", () => {
  assert.equal(compositeOver("rgba(0, 0, 0, 0.5)", "#FFFFFF"), "#808080");
  assert.equal(compositeOver("rgba(15, 20, 27, 1)", "#FFFFFF"), "#0f141b");
  // The spec's original .55 scrim is not AA-safe for white text (judges, S252): about 4.2:1.
  assert.ok(contrastRatio("#FFFFFF", compositeOver("rgba(15, 20, 27, 0.55)", "#FFFFFF")) < 4.5);
});

// Raw colours bypass the theme: a light-only hex/Tailwind palette class on the planner breaks
// dark mode and was the source of the 7 axe violations. Kit pages (components/site, app/*) put
// white text on photo scrims on purpose, so the scan covers the planner's own files.
const RAW_CLASS =
  /\b(?:bg|text|border|ring|from|to|via|divide|outline|placeholder|accent|fill|stroke|decoration)-(?:aa-[a-z-]+|white|black|(?:gray|slate|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-\d{2,3}|\[#[0-9a-fA-F]+\])/g;

test("the planner uses theme tokens, not raw palette classes", () => {
  const dir = new URL("../components/", import.meta.url);
  const files = [
    ...readdirSync(dir).filter((f) => f.endsWith(".tsx")).map((f) => new URL(f, dir)),
    new URL("../app/plan/PlannerApp.tsx", import.meta.url),
  ];
  const hits: string[] = [];
  for (const f of files) {
    const src = readFileSync(f, "utf8");
    for (const m of src.matchAll(RAW_CLASS)) hits.push(`${f.pathname.split("/frontend/")[1]}: ${m[0]}`);
  }
  assert.deepEqual(hits, []);
});
