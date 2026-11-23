// AA-1094 (root ADR 0019 d10, S278 audit M20 / M21) — /{slug}/tours/{tour}: one tour on an agency tenant's own site,
// with the agency's own text (CIS view catalog.v_tenant_tour_v1 through the browse Lambda) under the agency's brand:
// its trading name in the bar, its brand tokens (the same kit core brand-token block as /{slug}/plan), no Adventure
// Asia logo, no AA price (the agency is the merchant of record) and no account link (travellers are guests, d11).
// AA-1152 (B2 option C): the agency's OWN "from" price when its CRM publishes one (components/site/AgencyFromPrice.tsx).
// The planner's tour list on /{slug}/plan opens these pages; "Plan this trip" goes back to the agency's planner.
// AA-1130 (S283 audit T1): the call to action also sits near the top, under the facts ("Make it yours", like the AA
// tour page's card), so it is not 5,000 px down after a long itinerary; the page ends with the agency's own Privacy
// and Terms links (the agency bar carries them too) and the AI notice links the agency's "Use of AI" terms.
// An unknown or unserved slug, a tour the agency did not make its own, or one AA no longer serves is the 404 page; a
// lookup that cannot answer throws to app/[tenant]/error.tsx (retryable). An older tour slug (a title change) answers
// 308 to the current one, as the AA tour pages do.
import type { Metadata } from "next";
import { notFound, permanentRedirect } from "next/navigation";
import Header from "@/components/Header";
import LcpImage from "@/components/site/LcpImage";
import AgencyFromPrice from "@/components/site/AgencyFromPrice";
import { getTenantTour } from "@/lib/catalog";
import { clip, daysText } from "@/lib/text";
import { isTenantSlug, resolveTenant } from "@/lib/tenantSite";
import { itineraryParagraphs, tenantPlanHref, tenantTextMark, tenantTourPath } from "@/lib/tenantTours";
import { legalPaths } from "@/lib/legal";
import { plannerBrandCss } from "@/lib/tenantBrand";
import { AiNotice, ButtonLink, Icon } from "@/ui";

type Props = { params: Promise<{ tenant: string; tour: string }> };

async function load(tenant: string, tour: string) {
  if (!isTenantSlug(tenant)) return null;
  const site = await resolveTenant(tenant);
  const path = site ? tenantTourPath(site.slug, tour.toLowerCase()) : null;
  if (!site || !path) return null;
  const res = await getTenantTour(path);
  if (!res.ok) {
    if (res.status === 404) return null;
    throw new Error(`tenant tour unavailable (${res.status})`);
  }
  return { site, t: res.data };
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { tenant, tour } = await params;
  const found = await load(tenant, tour).catch(() => null);
  if (!found) return { title: "Tour", robots: { index: false, follow: false } };
  const { t } = found;
  return {
    // AA-1115: "· <agency>" comes from the /{slug} layout's title template.
    title: t.seo_title || t.title,
    description: clip(t.seo_meta || t.summary || t.subtitle || t.title),
    // An agency's site is not indexed yet (canonical URLs per tenant are a later slice).
    robots: { index: false, follow: true },
  };
}

export default async function TenantTourPage({ params }: Props) {
  const { tenant, tour } = await params;
  const found = await load(tenant, tour);
  if (!found) notFound();
  const { site, t } = found;
  if (t.slug !== tour) permanentRedirect(`/${site.slug}/tours/${t.slug}`);
  const brand = plannerBrandCss(site.theme);
  const mark = tenantTextMark(t.ai);
  const days = daysText(t.duration_days, t.duration_raw);
  const facts = [
    days && { icon: "calendar", label: "Duration", value: days },
    t.country && { icon: "pin", label: "Country", value: t.country },
    t.best_time_to_go && { icon: "sun", label: "Best time to go", value: t.best_time_to_go },
  ].filter(Boolean) as { icon: string; label: string; value: string }[];
  const paragraphs = itineraryParagraphs(t.itinerary_text);
  const planHref = tenantPlanHref(site.slug, t);
  const askHref = tenantPlanHref(site.slug, t, true);
  const legal = legalPaths(site.slug);

  return (
    <div className="min-h-screen bg-k-bg text-k-ink" data-testid="tenant-tour-page">
      {brand && <style data-testid="tenant-brand">{brand}</style>}
      <Header agency={{ slug: site.slug, name: site.name }} />
      <main className="mx-auto max-w-[880px] px-4 pb-24 pt-6 md:px-6 md:pt-10">
        <a href={`/${site.slug}/plan`} className="aa-focus inline-flex items-center gap-1 text-small text-k-muted hover:text-k-accent-deep">
          <Icon name="chevron-left" className="h-4 w-4" />
          Back to the planner
        </a>

        {t.cover_url && (
          <LcpImage
            src={t.cover_url}
            alt={t.cover_source === "tour" ? t.title : `${t.cover_place ?? t.country ?? "Destination"} (destination photo)`}
            fetchPriority="high"
            className="mt-5 aspect-[16/9] w-full rounded-panel object-cover"
          />
        )}

        <h1 className="mt-6 text-h1 text-k-ink" data-testid="tenant-tour-title">
          {t.title}
        </h1>
        {t.subtitle && <p className="mt-2 text-lead text-k-body">{t.subtitle}</p>}

        {facts.length > 0 && (
          <dl className="mt-5 grid gap-3 sm:grid-cols-3">
            {facts.map((f) => (
              <div key={f.label} className="flex items-start gap-2 rounded-panel border border-k-line bg-k-card p-3">
                <Icon name={f.icon} className="mt-0.5 h-4 w-4 shrink-0 text-k-accent-deep" />
                <div className="min-w-0">
                  <dt className="text-caption text-k-muted">{f.label}</dt>
                  <dd className="text-small font-semibold text-k-ink">{f.value}</dd>
                </div>
              </div>
            ))}
          </dl>
        )}

        <section
          aria-labelledby="make-it-yours"
          className="mt-6 rounded-panel border border-k-line bg-k-card p-4 md:p-5"
          data-testid="tenant-tour-cta-top"
        >
          <h2 id="make-it-yours" className="text-h3 text-k-ink">
            Make it yours
          </h2>
          <p className="mt-1 max-w-[60ch] text-small text-k-muted">
            Start from this itinerary on the map, change what you like, and {site.name} confirms every detail and price
            in your quote.
          </p>
          <AgencyFromPrice price={t.agency_price} agency={site.name} />
          <div className="mt-4 flex flex-col gap-2 sm:flex-row">
            <ButtonLink href={planHref} variant="primary" size="lg">
              <Icon name="route" className="h-4 w-4" /> Customise this trip
            </ButtonLink>
            <ButtonLink href={askHref} variant="secondary" size="lg">
              <Icon name="chat" className="h-4 w-4" /> Ask {site.name}
            </ButtonLink>
          </div>
        </section>

        {t.summary && (
          <p className="mt-6 max-w-[72ch] text-body text-k-body" {...mark}>
            {t.summary}
          </p>
        )}

        {t.highlights.length > 0 && (
          <section className="mt-8" aria-labelledby="highlights" {...mark}>
            <h2 id="highlights" className="text-h3 text-k-ink">
              Highlights
            </h2>
            <ul className="mt-3 space-y-2">
              {t.highlights.map((h, i) => (
                <li key={i} className="flex gap-2 text-body text-k-body">
                  <Icon name="check" className="mt-1 h-4 w-4 shrink-0 text-k-accent-deep" />
                  <span>{h}</span>
                </li>
              ))}
            </ul>
          </section>
        )}

        {paragraphs.length > 0 && (
          <section className="mt-8" aria-labelledby="itinerary" {...mark}>
            <h2 id="itinerary" className="text-h3 text-k-ink">
              The itinerary
            </h2>
            {paragraphs.map((p, i) => (
              <p key={i} className="mt-3 max-w-[72ch] whitespace-pre-line text-body text-k-body">
                {p}
              </p>
            ))}
          </section>
        )}

        <AiNotice className="mt-6 max-w-[72ch]" testId="tenant-tour-ai-notice" termsHref={legal.termsAi} />

        <div className="mt-10 flex flex-wrap items-center gap-3 border-t border-k-line pt-6">
          <ButtonLink href={planHref} variant="primary" size="lg" data-testid="tenant-tour-plan">
            Plan this trip
          </ButtonLink>
          <p className="text-small text-k-muted">{site.name} confirms every detail and price in your quote.</p>
        </div>

        <footer className="mt-10 border-t border-k-line pt-4 text-caption text-k-muted" data-testid="tenant-tour-legal">
          <ul className="flex flex-wrap gap-4">
            {legal.links.map((l) => (
              <li key={l.href}>
                <a href={l.href} className="aa-focus underline-offset-2 hover:text-k-ink hover:underline">
                  {l.label}
                </a>
              </li>
            ))}
            <li>
              <a href={legal.termsAi} className="aa-focus underline-offset-2 hover:text-k-ink hover:underline">
                Use of AI
              </a>
            </li>
          </ul>
        </footer>
      </main>
    </div>
  );
}
