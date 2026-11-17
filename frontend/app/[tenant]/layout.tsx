// AA-1115 (root ADR 0019 d10, S282 audit 6.3) — every page under /{slug} (and an agency's own domain, which proxy.ts
// rewrites onto these paths) is the agency's site, not Adventure Asia's:
//   * metadata: the agency's name replaces every brand-bearing key of the root layout's defaults (title default and
//     template, description, og:site_name; lib/brand.ts tenantMetadata), so the tab title, the description and a link
//     preview never name Adventure Asia. A slug that does not resolve gets a neutral title (its page is the 404);
//   * language: the agency's locale (CIS site settings, "en" | "vi") is the language of the page. The root layout
//     renders <html lang="en"> for every page (one root layout, so AA's pages stay static), so this layout marks its
//     content with the locale on the server (the wrapper's `lang`) and sets <html lang> before paint (the inline
//     script, as the root layout's theme script) and after a client-side navigation (TenantLang).
// The lookup is the cached one of the pages (lib/tenantSite.ts); a lookup that cannot answer gives the neutral
// metadata here, and the page itself throws it to app/[tenant]/error.tsx.
import type { Metadata } from "next";
import TenantLang from "@/components/TenantLang";
import { tenantMetadata } from "@/lib/brand";
import { tenantLang, tenantLangScript } from "@/lib/tenantLang";
import { isTenantSlug, resolveTenant, type TenantSite } from "@/lib/tenantSite";

type Params = { params: Promise<{ tenant: string }> };

async function siteOf(tenant: string): Promise<TenantSite | null> {
  return isTenantSlug(tenant) ? resolveTenant(tenant).catch(() => null) : null;
}

export async function generateMetadata({ params }: Params): Promise<Metadata> {
  const { tenant } = await params;
  return tenantMetadata(await siteOf(tenant));
}

export default async function TenantLayout({ children, params }: Params & { children: React.ReactNode }) {
  const { tenant } = await params;
  const lang = tenantLang((await siteOf(tenant))?.locale);
  return (
    // display: contents, so the planner's full-height layout is unchanged.
    <div lang={lang} className="contents" data-testid="tenant-lang">
      <script dangerouslySetInnerHTML={{ __html: tenantLangScript(lang) }} />
      <TenantLang lang={lang} />
      {children}
    </div>
  );
}
