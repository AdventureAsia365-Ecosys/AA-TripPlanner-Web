// AA-1033: the traveller AI notice. The sentence comes from the kit core copy (ui/core/aiNotice.messages.ts, tokens
// profile of the CIS kit), and ui/AiNotice.tsx renders it on the tour page (under the day-by-day itinerary), in the
// planner under the narration, and in the site footer with a link to the terms' "Use of AI" section (/terms#ai).
// Run: npm test (node --test with type stripping).
import { test } from "node:test";
import assert from "node:assert/strict";
import { copyFileSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { TERMS_AI_PATH, TERMS_PATH } from "./legal.ts";

const src = (rel: string) => readFileSync(new URL(`../${rel}`, import.meta.url), "utf8");

/** The core module, loaded from a temp copy whose relative import carries the .ts extension (type stripping needs it). */
async function loadCore(): Promise<{ aiNoticeText: (v: string, lang?: string) => string; AI_NOTICE_VARIANTS: string[] }> {
  const dir = mkdtempSync(join(tmpdir(), "tp-ai-notice-"));
  try {
    const core = new URL("../ui/core/", import.meta.url);
    copyFileSync(new URL("i18n.ts", core), join(dir, "i18n.ts"));
    const text = readFileSync(new URL("aiNotice.messages.ts", core), "utf8").replace(/from "\.\/i18n"/, 'from "./i18n.ts"');
    writeFileSync(join(dir, "aiNotice.messages.ts"), text);
    return await import(pathToFileURL(join(dir, "aiNotice.messages.ts")).href);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

test("the kit core copy carries the traveller sentence: mistakes, and the official quote confirms", async () => {
  const { aiNoticeText, AI_NOTICE_VARIANTS } = await loadCore();
  assert.ok(AI_NOTICE_VARIANTS.includes("traveller"));
  const en = aiNoticeText("traveller", "en");
  assert.match(en, /written with the help of AI and may contain mistakes/);
  assert.match(en, /Prices, itineraries and visa conditions are confirmed in your official quote\./);
  assert.doesNotMatch(en, /liab|not responsible|disclaim/i);
  assert.notEqual(aiNoticeText("traveller", "vi"), en);
});

test("ui/AiNotice renders the core sentence as a note (never its own copy of the text)", () => {
  const s = src("ui/AiNotice.tsx");
  assert.match(s, /from "\.\/core\/aiNotice\.messages"/);
  assert.match(s, /aiNoticeText\("traveller"/);
  assert.match(s, /role="note"/);
  assert.doesNotMatch(s, /may contain mistakes/, "the sentence lives in the kit core copy only");
  assert.match(src("ui/index.ts"), /export \{ AiNotice\b/);
});

test("the notice shows on the tour page, in the planner narration and in the site footer", () => {
  const tour = src("app/tours/[slug]/page.tsx");
  const notice = tour.indexOf('<AiNotice className="mt-5 max-w-[72ch]" testId="tour-ai-notice"');
  assert.ok(notice > tour.indexOf('id="itinerary-days"'), "under the day-by-day timeline");
  assert.ok(notice < tour.indexOf('aria-labelledby="route-map"'), "inside the itinerary section");
  assert.match(src("components/TripPanel.tsx"), /<AiNotice size="sm" testId="narration-ai-notice" \/>/);
  const footer = src("components/site/SiteFooter.tsx");
  assert.match(footer, /<AiNotice tone="band" testId="footer-ai-notice"/);
  assert.match(footer, /href=\{TERMS_AI_PATH\}/);
  assert.equal(TERMS_AI_PATH, `${TERMS_PATH}#ai`);
});

test("terms §3 'Use of AI': what AI writes, that it can be wrong, for information, and what binds", () => {
  const m = src("app/terms/page.tsx").match(/<LegalSection id="ai".*?<\/LegalSection>/s);
  assert.ok(m, 'terms: section id="ai" not found');
  const ai = m[0].replace(/\s+/g, " ");
  assert.match(ai, /title="3\. Use of AI"/);
  for (const phrase of [
    "tour descriptions",
    "narration",
    "AI can make mistakes",
    "for information",
    "confirmed in your official quote",
    "booking conditions you accept",
  ]) {
    assert.ok(ai.includes(phrase), `terms §3 must say: ${phrase}`);
  }
  assert.doesNotMatch(ai, /liab|not responsible|disclaim|\[to be confirmed\]/i);
});
