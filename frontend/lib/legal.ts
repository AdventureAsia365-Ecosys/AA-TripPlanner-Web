// AA-886 — the Trip Planner's legal pages. Both pages are DRAFTS: counsel has not reviewed them, so
// every page shows LEGAL_DRAFT_LABEL in a banner and asks search engines not to index it. When counsel
// approves a text, update LEGAL_VERSION / LEGAL_UPDATED and drop the draft banner in the same PR.

export const PRIVACY_PATH = "/privacy";
export const TERMS_PATH = "/terms";

/** The banner title on every legal page while the text is not approved. */
export const LEGAL_DRAFT_LABEL = "Draft — pending legal review";

/** Draft version and date shown under the page title. */
export const LEGAL_VERSION = "Draft v0.1";
export const LEGAL_UPDATED = "20 October 2026";

/** The contact address adventure.asia itself publishes (same as the CIS portal's SUPPORT_EMAIL). */
export const LEGAL_CONTACT_EMAIL = "info@adventure.asia";

/** Adventure Asia's company-wide privacy policy (adventure.asia, live 200 on 20/10/2026). */
export const COMPANY_PRIVACY_URL = "https://adventure.asia/privacy-policy";

/** The legal links shown in the site footer and next to the contact form, in this order. */
export const LEGAL_LINKS: ReadonlyArray<{ href: string; label: string }> = [
  { href: PRIVACY_PATH, label: "Privacy" },
  { href: TERMS_PATH, label: "Terms" },
];
