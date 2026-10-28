// AA-995 (spec §1, map-first graft) — the Golden Hour customer pages use the token scales of ui/tokens.css
// (Tailwind text-display|h1|h3|lead|body|small|caption|kicker|script, rounded-card…, shadow-e1…,
// duration-fast…, ease-std…). Raw font sizes, hex colours, raw durations / easings, rgba() shadows and the
// old rounded-2xl / shadow-aa are not allowed in the files below, so a later edit cannot slide back.
// S254 (slices 5–6+): the destination, tour, destinations-index, trips, sign-in, legal, not-found and error pages.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const FILES = [
  "app/page.tsx",
  "app/destinations/page.tsx",
  "app/destinations/[country]/page.tsx",
  "app/not-found.tsx",
  "app/sign-in/page.tsx",
  "app/tours/[slug]/page.tsx",
  "app/trips/page.tsx",
  "components/MyTrips.tsx",
  "components/SignInForm.tsx",
  "components/site/LegalPage.tsx",
  "components/site/PageHero.tsx",
  "components/site/PageIntro.tsx",
  "components/site/StaticMap.tsx",
  "ui/RouteError.tsx",
  "ui/States.tsx",
  "components/site/CtaBand.tsx",
  "components/site/DestinationTile.tsx",
  "components/site/HeaderScrollState.tsx",
  "components/site/HomeHero.tsx",
  "components/site/HomeSearch.tsx",
  "components/site/MobileMenu.tsx",
  "components/site/NoPhotoTile.tsx",
  "components/site/PhotoRibbon.tsx",
  "components/site/RevealObserver.tsx",
  "components/site/RibbonControl.tsx",
  "components/site/SectionTitle.tsx",
  "components/site/SiteFooter.tsx",
  "components/site/SiteHeader.tsx",
  "components/site/TourCard.tsx",
  "components/site/TourCarousel.tsx",
  "components/site/TrustStrip.tsx",
  "components/site/WaysIn.tsx",
  // S254 round 10: the footer "Reduce motion" toggle and the timeline "Expand all".
  "components/site/MotionToggle.tsx",
  "components/site/DayToggleAll.tsx",
];

const BANNED: [string, RegExp][] = [
  ["raw font size (use text-display|h1|h3|lead|body|small|caption|kicker|script)", /\btext-\[\d+(?:\.\d+)?(?:px|rem)\]/g],
  ["raw hex colour (use a k-* token)", /#[0-9a-fA-F]{3,8}\b/g],
  ["raw duration (use duration-instant|fast|base|slow|reveal)", /\bduration-(?:\d+|\[[^\]]+\])/g],
  ["raw easing (use ease-std|out|in)", /\bease-(?:linear|in-out|\[[^\]]+\])/g],
  ["raw rgba() (use a token)", /rgba?\(/g],
  ["old radius / shadow (use rounded-card|tile|panel|pill, shadow-e1|e2|e3)", /\b(?:rounded-2xl|shadow-aa(?:-sm)?)\b/g],
  ["Tailwind palette colour (use a k-* token)", /\b(?:bg|text|border)-(?:gray|slate|zinc|neutral|stone|black)-?\d*\b/g],
];

test("customer pages use the AA-995 token scales, not raw values", () => {
  const hits: string[] = [];
  for (const f of FILES) {
    const src = readFileSync(new URL(`../${f}`, import.meta.url), "utf8")
      // comments may name what is banned
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/(^|[^:])\/\/.*$/gm, "$1");
    for (const [why, re] of BANNED) for (const m of src.matchAll(re)) hits.push(`${f}: ${m[0]} — ${why}`);
  }
  assert.deepEqual(hits, []);
});
