// AA-886: the draft legal pages exist, say they are drafts, stay out of search engines, and are linked
// from the site footer and from the contact form (the only form that collects personal data).
// Run: npm test (node --test with type stripping; no extra dependencies).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { LEGAL_DRAFT_LABEL, LEGAL_LINKS, PRIVACY_PATH, TERMS_PATH } from "./legal.ts";

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

test("the site footer and the contact form link the legal pages", () => {
  for (const file of ["components/site/SiteFooter.tsx", "components/TripPanel.tsx"]) {
    assert.match(src(file), /LEGAL_LINKS\.map\(/, `${file} must render LEGAL_LINKS`);
  }
});
