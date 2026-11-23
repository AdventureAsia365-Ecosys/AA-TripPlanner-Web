// AA-886 — the Trip Planner's legal pages. Both pages are DRAFTS: counsel has not reviewed them, so
// every page shows LEGAL_DRAFT_LABEL in a banner and asks search engines not to index it. When counsel
// approves a text, update LEGAL_VERSION / LEGAL_UPDATED and drop the draft banner in the same PR.

export const PRIVACY_PATH = "/privacy";
export const TERMS_PATH = "/terms";
/** AA-1033: the "Use of AI" section of the terms (linked from the footer AI notice). */
export const TERMS_AI_PATH = "/terms#ai";

/** The banner title on every legal page while the text is not approved. */
export const LEGAL_DRAFT_LABEL = "Draft — pending legal review";

/** Draft version and date shown under the page title. */
export const LEGAL_VERSION = "Draft v0.2";
export const LEGAL_UPDATED = "3 November 2026";

/**
 * AA-820 (S266): the usage policy of the provider whose model writes the site's AI text (Claude through Amazon Bedrock,
 * root docs/architecture/ai-use-register.md row `tp_compose`). Travellers are end users of a product that integrates
 * Claude, so the terms link it (root docs/legal/tenant-ai-schedule.md §2 (c)).
 */
export const ANTHROPIC_USAGE_POLICY_URL = "https://www.anthropic.com/legal/aup";

// AA-1115: Adventure Asia's contact address and company privacy policy moved to lib/brand.ts (AA_LEGAL_CONTACT_EMAIL,
// AA_COMPANY_PRIVACY_URL): this file is read by an agency's planner too, which never names Adventure Asia.

/** The legal links shown in the site footer and next to the contact form, in this order. */
export const LEGAL_LINKS: ReadonlyArray<{ href: string; label: string }> = [
  { href: PRIVACY_PATH, label: "Privacy" },
  { href: TERMS_PATH, label: "Terms" },
];

/**
 * AA-1115: the legal pages a planner links. An agency's planner (/{slug}/plan, its slug given) links the agency's own
 * pages, /{slug}/privacy and /{slug}/terms (app/[tenant]/privacy, app/[tenant]/terms: the agency is responsible for
 * the trips its travellers send it), never Adventure Asia's; AA's own planner and the AA-hosted demo tenant (null)
 * keep /privacy and /terms.
 */
export interface LegalPaths {
  privacy: string;
  terms: string;
  termsAi: string;
  links: ReadonlyArray<{ href: string; label: string }>;
}

export function legalPaths(agencySlug: string | null, lang: "en" | "vi" = "en"): LegalPaths {
  if (!agencySlug) return { privacy: PRIVACY_PATH, terms: TERMS_PATH, termsAi: TERMS_AI_PATH, links: LEGAL_LINKS };
  const privacy = `/${agencySlug}${PRIVACY_PATH}`;
  const terms = `/${agencySlug}${TERMS_PATH}`;
  return {
    privacy,
    terms,
    termsAi: `${terms}#ai`,
    links: [
      // AA-1160: the link labels follow the agency planner's language (the pages themselves stay English drafts).
      { href: privacy, label: lang === "vi" ? "Quyền riêng tư" : "Privacy" },
      { href: terms, label: lang === "vi" ? "Điều khoản" : "Terms" },
    ],
  };
}
