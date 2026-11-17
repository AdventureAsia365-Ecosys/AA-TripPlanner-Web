// AA-1115 — an agency page's language on <html lang> (app/[tenant]/layout.tsx). Only "en" or "vi" ever reach the
// script (lib/tenantSite.ts asTenant reads any other locale as "en"), and it is JSON-quoted anyway. No next/* import,
// so `npm test` runs it.

export type TenantLangCode = "en" | "vi";

export function tenantLang(locale: string | null | undefined): TenantLangCode {
  return locale === "vi" ? "vi" : "en";
}

/** The inline script that sets <html lang> before paint (the root layout renders lang="en"). */
export function tenantLangScript(locale: string | null | undefined): string {
  return `document.documentElement.lang=${JSON.stringify(tenantLang(locale))};`;
}
