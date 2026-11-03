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

/** The contact address adventure.asia itself publishes (same as the CIS portal's SUPPORT_EMAIL). */
export const LEGAL_CONTACT_EMAIL = "info@adventure.asia";

/** Adventure Asia's company-wide privacy policy (adventure.asia, live 200 on 20/10/2026). */
export const COMPANY_PRIVACY_URL = "https://adventure.asia/privacy-policy";

/** The legal links shown in the site footer and next to the contact form, in this order. */
export const LEGAL_LINKS: ReadonlyArray<{ href: string; label: string }> = [
  { href: PRIVACY_PATH, label: "Privacy" },
  { href: TERMS_PATH, label: "Terms" },
];
