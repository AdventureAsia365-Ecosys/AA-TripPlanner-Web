// AA-995 review — guards for the photo ribbon's CSS and markup (components/site/PhotoRibbon.tsx,
// app/globals.css .tp-ribbon*), two bugs that tsc, eslint and the build cannot see:
//  1. The rule that starts the marquee out-ranks the pause rules ((0,6,1) vs (0,4,1)). If it used the
//     `animation` shorthand, the shorthand would reset animation-play-state to running and the pause
//     button, the off-screen pause and the hover pause would never apply (WCAG 2.2.2).
//  2. The duplicate tiles (copy B, fill-rule repeats) must stay clickable: `inert` removes them from
//     hit-testing, so half of every loop would be dead photos. They are kept out of the tab order and the
//     accessibility tree with tabIndex -1 + aria-hidden instead.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const css = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");

// A selector list split on its top-level commas only (a comma inside :has(...) / :is(...) is part of one selector).
function splitSelectors(list: string): string[] {
  const out: string[] = [];
  let depth = 0;
  let cur = "";
  for (const ch of list) {
    if (ch === "(") depth++;
    if (ch === ")") depth--;
    if (ch === "," && depth === 0) {
      out.push(cur);
      cur = "";
    } else cur += ch;
  }
  out.push(cur);
  return out.map((s) => s.trim()).filter(Boolean);
}

// Innermost `selector { declarations }` blocks; an @media wrapper is skipped because its body has braces.
function rules(src: string): { selectors: string[]; body: string }[] {
  const out: { selectors: string[]; body: string }[] = [];
  for (const m of src.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    out.push({ selectors: splitSelectors(m[1]), body: m[2] });
  }
  return out;
}

const trackRules = rules(css).filter((r) => r.selectors.some((s) => /\.tp-ribbon-track$/.test(s)));

test("no .tp-ribbon-track rule uses the animation shorthand (it would reset the play state)", () => {
  assert.ok(trackRules.length > 0, "expected ribbon track rules in app/globals.css");
  for (const r of trackRules) {
    assert.doesNotMatch(r.body, /(^|[;\s])animation\s*:/, `shorthand on: ${r.selectors.join(", ")}`);
    assert.doesNotMatch(r.body, /animation-play-state\s*:\s*running/, `forces running on: ${r.selectors.join(", ")}`);
  }
});

test("the marquee runs from longhands, and pause, off-screen and hover each set animation-play-state: paused", () => {
  const anim = trackRules.find((r) => /animation-name\s*:\s*tp-marquee/.test(r.body));
  assert.ok(anim, "the marquee rule sets animation-name: tp-marquee");
  const paused = trackRules.filter((r) => /animation-play-state\s*:\s*paused/.test(r.body)).flatMap((r) => r.selectors);
  for (const want of [/\[data-paused\]/, /\[data-offscreen\]/, /\.tp-ribbon-viewport:hover/]) {
    assert.ok(paused.some((s) => want.test(s)), `no paused rule for ${want}`);
  }
});

test("every moving-ribbon rule stops when the viewport itself or anything inside it has keyboard focus", () => {
  // A link-less ribbon (no tile to Tab to) makes .tp-ribbon-viewport the focus target; a descendant-only
  // `.tp-ribbon-viewport :focus-visible` never matches it, so the marquee kept moving under overflow: hidden.
  const moving = rules(css)
    .flatMap((r) => r.selectors)
    .filter((s) => /\.tp-ribbon\[data-animate\]:not\(:has\(/.test(s));
  assert.ok(moving.length >= 6, `expected the still-row switch on the marquee rules, found ${moving.length}`);
  for (const s of moving) {
    assert.match(s, /:has\([^)]*\.tp-ribbon-viewport:focus-visible/, `viewport focus not covered: ${s}`);
    assert.match(s, /:has\([^)]*\.tp-ribbon-viewport :focus-visible/, `focus inside not covered: ${s}`);
  }
});

test("the ribbon's duplicate tiles are aria-hidden with tabIndex -1, never inert", () => {
  const tsx = readFileSync(new URL("../components/site/PhotoRibbon.tsx", import.meta.url), "utf8");
  const code = tsx.replace(/\{\/\*[\s\S]*?\*\/\}/g, "").replace(/^\s*\/\/.*$/gm, "");
  assert.doesNotMatch(code, /\binert\b/);
  assert.match(code, /tabIndex=\{hidden \? -1 : undefined\}/);
  assert.match(code, /"data-repeat": "", "aria-hidden": true/);
  assert.match(code, /<ul aria-hidden="true" className="tp-ribbon-list tp-ribbon-copy">/);
});
