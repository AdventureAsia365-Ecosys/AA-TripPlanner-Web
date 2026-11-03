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
  assert.match(s, /aiNoticeText\(variant, "en"\)/);
  assert.match(s, /variant = "traveller"/, "the traveller sentence stays the default (AA-1040 adds narration)");
  assert.match(s, /role="note"/);
  assert.doesNotMatch(s, /may contain mistakes/, "the sentence lives in the kit core copy only");
  assert.match(src("ui/index.ts"), /export \{ AiNotice\b/);
});

test("the notice shows on the tour page, in the planner narration and in the site footer", () => {
  const tour = src("app/tours/[slug]/page.tsx");
  const notice = tour.indexOf('<AiNotice className="mt-5 max-w-[72ch]" testId="tour-ai-notice"');
  assert.ok(notice > tour.indexOf('id="itinerary-days"'), "under the day-by-day timeline");
  assert.ok(notice < tour.indexOf('aria-labelledby="route-map"'), "inside the itinerary section");
  assert.match(src("components/TripPanel.tsx"),
    /<AiNotice size="sm" variant=\{narrationNoticeVariant\(narrationSource\)\} termsHref=\{TERMS_AI_PATH\}\s+testId="narration-ai-notice" \/>/);
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

test("the planner (no site footer) shows the notice and the 'Use of AI' link under its AI-written experience text", () => {
  // /plan renders no SiteFooter, so each planner surface with CIS writer-model text carries its own notice.
  assert.doesNotMatch(src("app/plan/page.tsx") + src("app/plan/PlannerApp.tsx") + src("app/plan/layout.tsx"), /SiteFooter/);
  const popup = src("components/DestinationPopup.tsx");
  const popupNotice = popup.indexOf('<AiNotice size="sm" termsHref={TERMS_AI_PATH} className="mt-2" testId="popup-ai-notice" />');
  assert.ok(popupNotice > popup.indexOf("</ul>"), "map popup: under the experience list, outside its scroll area");
  assert.match(popup, /detail\?\.components\.some\(\(c\) => c\.text_extract\) && \(\s*<AiNotice/, "only when a description shows");
  const stop = src("components/StopActivities.tsx");
  const stopNotice = stop.indexOf('<AiNotice size="sm" termsHref={TERMS_AI_PATH} className="mt-1" testId="stop-activities-ai-notice" />');
  assert.ok(stopNotice > stop.indexOf("<AiLabel"), "stop activities: next to the AI label of the snippets");
  for (const f of [popup, stop]) assert.match(f, /import \{ TERMS_AI_PATH \} from "@\/lib\/legal"/);
  const ui = src("ui/AiNotice.tsx");
  assert.match(ui, /href=\{termsHref\}\s+target="_blank"\s+rel="noopener noreferrer"/, "a new tab keeps the trip");
  assert.match(ui, />\s*Use of AI\s*</);
});

test("AA-1040: the narration sentence (written by AI on request) and which narration gets it", async () => {
  const { aiNoticeText, AI_NOTICE_VARIANTS } = await loadCore();
  assert.ok(AI_NOTICE_VARIANTS.includes("narration"));
  const en = aiNoticeText("narration", "en");
  assert.match(en, /written by AI when you asked for it/);
  assert.match(en, /confirmed in your official quote/);
  const { narrationNoticeVariant } = await import("./aiNotices.ts");
  assert.equal(narrationNoticeVariant("llm"), "narration");
  assert.equal(narrationNoticeVariant("mixed"), "narration");
  for (const s of ["master", "master_basic", "basic", null, undefined]) assert.equal(narrationNoticeVariant(s), "traveller");
  const panel = src("components/TripPanel.tsx");
  assert.match(panel, /data-testid="narration-text" data-ai-generated="true"/, "the narration carries the machine mark");
  assert.match(panel, /data-ai-source=\{narrationSource \?\? undefined\}/);
});

test("AA-1040: catalog tour text blocks carry the machine mark, never the whole page", async () => {
  const { catalogTextMark } = await import("./aiNotices.ts");
  assert.deepEqual(catalogTextMark("2026-10-02T03:00:00+00:00", "2026-09-01T03:00:00+00:00"), {
    "data-ai-generated": "true", "data-ai-source": "catalog", "data-ai-generated-at": "2026-10-02T03:00:00+00:00",
  });
  assert.equal(catalogTextMark(null, "2026-09-01T03:00:00+00:00")["data-ai-generated-at"], "2026-09-01T03:00:00+00:00");
  assert.equal("data-ai-generated-at" in catalogTextMark(null, null), false);
  const page = src("app/tours/[slug]/page.tsx");
  // summary, highlights, legacy itinerary text and each day body: four marked blocks, no page-level meta tag
  assert.equal((page.match(/\{\.\.\.aiMark\}/g) ?? []).length, 4);
  assert.doesNotMatch(page, /name="ai-generated"/);
});
