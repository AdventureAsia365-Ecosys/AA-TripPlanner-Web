// AA-886: the draft legal pages exist, say they are drafts, stay out of search engines, and are linked
// from the site footer and from the contact form (the only form that collects personal data).
// Run: npm test (node --test with type stripping; no extra dependencies).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  ANTHROPIC_USAGE_POLICY_URL,
  LEGAL_DRAFT_LABEL,
  LEGAL_LINKS,
  PRIVACY_PATH,
  TERMS_AI_PATH,
  TERMS_PATH,
  legalPaths,
} from "./legal.ts";

const src = (rel: string) => readFileSync(new URL(`../${rel}`, import.meta.url), "utf8");

test("legal links are /privacy then /terms", () => {
  assert.deepEqual(
    LEGAL_LINKS.map((l) => l.href),
    [PRIVACY_PATH, TERMS_PATH],
  );
  assert.deepEqual([PRIVACY_PATH, TERMS_PATH], ["/privacy", "/terms"]);
});

test("the draft label says the text is not approved", () => {
  assert.match(LEGAL_DRAFT_LABEL, /draft/i);
  assert.match(LEGAL_DRAFT_LABEL, /legal review/i);
});

for (const page of ["app/privacy/page.tsx", "app/terms/page.tsx"]) {
  test(`${page} renders the legal shell (draft banner) and is noindex`, () => {
    const s = src(page);
    assert.match(s, /<LegalPage\b/, "page must use the LegalPage shell, which always shows the draft banner");
    assert.match(s, /robots:\s*\{\s*index:\s*false/, "a draft legal page must not be indexed");
  });
}

test("the legal shell always renders the draft banner", () => {
  const s = src("components/site/LegalPage.tsx");
  assert.match(s, /LEGAL_DRAFT_LABEL/);
  assert.match(s, /<DraftBanner \/>/);
});

test("the site footer and the contact forms link the legal pages", () => {
  assert.match(src("components/site/SiteFooter.tsx"), /LEGAL_LINKS\.map\(/, "the site footer must render LEGAL_LINKS");
  // AA-1115: the planner's forms link the page's own legal pages (AA's, or an agency's /{slug}/privacy and /terms).
  for (const file of ["components/TripPanel.tsx", "components/LeadForm.tsx"]) {
    assert.match(src(file), /legal\.links\.map\(/, `${file} must render the page's legal links`);
  }
});

test("AA-1115: an agency's planner links the agency's own legal pages, AA's planner keeps /privacy and /terms", () => {
  assert.deepEqual(legalPaths(null), { privacy: PRIVACY_PATH, terms: TERMS_PATH, termsAi: TERMS_AI_PATH, links: LEGAL_LINKS });
  const a = legalPaths("quillmere-voyages");
  assert.equal(a.privacy, "/quillmere-voyages/privacy");
  assert.equal(a.terms, "/quillmere-voyages/terms");
  assert.equal(a.termsAi, "/quillmere-voyages/terms#ai");
  assert.deepEqual(a.links.map((l) => l.href), [a.privacy, a.terms]);
});

// AA-820 (S266): the "Use of AI" section names the model that writes the narration and links the Anthropic Usage
// Policy (root docs/legal/tenant-ai-schedule.md §2 (c)); travellers are end users, not tenants.
test("the terms' AI section names the writer and links the Anthropic Usage Policy", () => {
  assert.equal(ANTHROPIC_USAGE_POLICY_URL, "https://www.anthropic.com/legal/aup");
  const s = src("app/terms/page.tsx");
  const ai = s.slice(s.indexOf('<LegalSection id="ai"'), s.indexOf("</LegalSection>", s.indexOf('<LegalSection id="ai"')));
  const flat = ai.replace(/\s+/g, " ");
  assert.match(flat, /href=\{ANTHROPIC_USAGE_POLICY_URL\}/);
  assert.match(flat, /Anthropic&apos;s Claude, through Amazon Bedrock/);
  assert.match(flat, /Adventure Asia&apos;s own account/);
  assert.match(flat, /AI-written text is labelled as such/);
});
