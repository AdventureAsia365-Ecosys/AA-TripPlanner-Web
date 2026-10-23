// AA-837 (D10): the map planner moved onto the k-* tokens and follows the site theme, so every
// text/background pair it uses must meet WCAG AA (4.5:1, all its text is small) in BOTH themes,
// and the planner must not slide back to raw palette classes. Browser axe runs cover the rendered
// page (docs/implementation-notes/AA-837.md); this guards the tokens and the class usage.
// Run: npm test (node --test with type stripping; no extra dependencies).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { contrastRatio, parseHex, readThemeTokens } from "./contrast.ts";

const css = readFileSync(new URL("../ui/tokens.css", import.meta.url), "utf8");
const themes = readThemeTokens(css);

// [text token, background token] — every pairing the planner renders at rest (hover-only fills
// included where text stays on them).
const PAIRS: [string, string][] = [
  ["ink", "card"], ["ink", "bg"], ["ink", "accent-tint"], ["ink", "neutral-bg"], ["ink", "line"],
  ["body", "card"], ["body", "bg"],
  ["muted", "card"], ["muted", "bg"], ["muted", "accent-tint"], ["muted", "neutral-bg"],
  ["accent-deep", "card"], ["accent-deep", "bg"], ["accent-deep", "accent-tint"], ["accent-deep", "neutral-bg"],
  ["on-accent", "accent"], // gold buttons, day-number dots, filter count
  ["bg", "ink"], // inverted pills (Map | List, day counter, Explore/Your trip badge)
  ["red", "card"], ["red", "red-soft"], ["red", "bg"],
  ["neutral-fg", "neutral-bg"],
];

test("contrastRatio matches known WCAG values", () => {
  assert.equal(Math.round(contrastRatio("#000000", "#ffffff") * 100) / 100, 21);
  assert.equal(Math.round(contrastRatio("#DB9628", "#FFFFFF") * 100) / 100, 2.5); // the old gold label
  assert.equal(parseHex("rgba(0,0,0,0.5)"), null);
});

for (const theme of ["light", "dark"] as const) {
  test(`every planner text pair meets WCAG AA in the ${theme} theme`, () => {
    const t = themes[theme];
    const failures = PAIRS.map(([fg, bg]) => {
      assert.ok(t[fg] && t[bg], `token missing in ${theme}: --aa-${t[fg] ? bg : fg}`);
      const ratio = contrastRatio(t[fg], t[bg]);
      return { pair: `--aa-${fg} on --aa-${bg}`, ratio: Math.round(ratio * 100) / 100 };
    }).filter((r) => r.ratio < 4.5);
    assert.deepEqual(failures, []);
  });
}

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
