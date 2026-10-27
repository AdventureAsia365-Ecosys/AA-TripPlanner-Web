// AA-812 — tour page (PDP), SSR + ISR from the CIS catalog (contract catalog.tour v1) through the
// browse Lambda. No price: there is no price source yet (prices belong to AA-Booking, ADR 0005), so
// the "from" price is hidden rather than invented.
import type { Metadata } from "next";
import Link from "next/link";
import { notFound, permanentRedirect } from "next/navigation";
import AiLabel, { AI_BRAND_SHORT, AI_CATALOG_EXPLANATION, AI_CATALOG_LABEL } from "@/components/AiLabel";
import JsonLd from "@/components/site/JsonLd";
import SiteFooter from "@/components/site/SiteFooter";
import SiteHeader from "@/components/site/SiteHeader";
import StaticMap from "@/components/site/StaticMap";
import type { MapPoint } from "@/lib/tourMap";
import { getTour, siteUrl } from "@/lib/catalog";
import type { TourDetail } from "@/lib/catalogTypes";
import { clip, coverAlt, daysText, splitLines, titleCase } from "@/lib/text";
import { routeLines, routePoints } from "@/lib/tourMap";
import { Badge, ButtonLink, Icon, Photo } from "@/ui";

// Route segment config must be a literal (Next 15 reads it statically and fails the build on an
// imported constant). Keep it equal to CATALOG_REVALIDATE_S (lib/segmentConfig.test.ts checks).
export const revalidate = 1800;

// Pages are rendered on first request and cached (ISR); nothing is pre-built at deploy time.
export function generateStaticParams() {
  return [];
}

// Next 15: route params arrive as a Promise (async request APIs).
type Props = { params: Promise<{ slug: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { slug } = await params;
  const res = await getTour(slug.toLowerCase());
  if (!res.ok) return { title: "Tour", robots: { index: false } };
  const t = res.data;
  const description = clip(t.summary || t.subtitle || t.description || `${t.title} — an Adventure Asia tour.`);
  return {
    title: `${t.title}${t.country ? ` — ${t.country}` : ""}`,
    description,
    alternates: { canonical: `/tours/${t.slug}` },
    // A retired tour keeps its page for old links but leaves search results (and the sitemap).
    ...(t.lifecycle_stage === "retired" ? { robots: { index: false, follow: true } } : {}),
    openGraph: {
      title: t.title,
      description,
      type: "website",
      url: `/tours/${t.slug}`,
      // A destination fallback cover keeps its "destination photo" alt here too.
      images: t.cover_url ? [{ url: t.cover_url, alt: coverAlt(t) }] : undefined,
    },
  };
}

// tour_id changes when CIS re-uploads the tour; source_id does not, so the planner resolves the
// current tour_id from it (a cached or bookmarked link keeps working).
function planHref(t: TourDetail, ask = false): string {
  const p = new URLSearchParams({ tour: t.tour_id, source: t.source_id });
  if (t.country) p.set("country", t.country);
  if (t.duration_days) p.set("days", String(Math.max(2, Math.min(30, t.duration_days))));
  if (ask) p.set("ask", "1");
  return `/plan?${p}`;
}

function tripJsonLd(t: TourDetail, points: MapPoint[]) {
  const url = `${siteUrl()}/tours/${t.slug}`;
  return {
    "@context": "https://schema.org",
    "@type": "TouristTrip",
    name: t.title,
    description: t.summary || t.subtitle || undefined,
    url,
    image: t.photos.slice(0, 5).map((p) => p.url).filter(Boolean),
    // touristType is the audience; activities belong in keywords.
    keywords: t.activities.length ? t.activities.slice(0, 8).join(", ") : undefined,
    provider: { "@type": "TravelAgency", name: "Adventure Asia", url: "https://adventure.asia/" },
    itinerary: {
      "@type": "ItemList",
      numberOfItems: t.days.length,
      itemListElement: t.days.map((d) => ({
        "@type": "ListItem",
        position: d.day_number,
        name: `Day ${d.day_number}: ${d.title}`,
        description: clip(d.body, 300),
      })),
    },
    ...(points.length
      ? {
          contentLocation: points.slice(0, 10).map((p) => ({
            "@type": "Place",
            name: p.name,
            geo: { "@type": "GeoCoordinates", latitude: p.lat, longitude: p.lng },
          })),
        }
      : {}),
  };
}

function Gallery({ t }: { t: TourDetail }) {
  const photos = t.photos.length ? t.photos : [];
  if (photos.length === 0) {
    // No tour photo: the API may send a destination photo instead (cover_source), captioned so it
    // never reads as a picture of this tour.
    const fallback = t.cover_url && t.cover_source !== "tour" && t.cover_place;
    return (
      <figure className="relative">
        <Photo src={t.cover_url} alt={coverAlt(t)} eager className="aspect-[16/9] w-full rounded-2xl" />
        {fallback && (
          <figcaption className="absolute bottom-3 left-3 rounded-full bg-black/70 px-3 py-1 text-[12px] font-medium text-white">
            Destination photo · {t.cover_place}
          </figcaption>
        )}
      </figure>
    );
  }
  const [first, ...rest] = photos;
  return (
    <div className="grid gap-2 sm:grid-cols-4 sm:grid-rows-2">
      <Photo src={first.url} alt={first.place_label || t.title} eager
             className="aspect-[16/10] w-full rounded-2xl sm:col-span-3 sm:row-span-2 sm:aspect-auto sm:h-full" />
      {rest.length > 0 && (
        <ul className="-mx-4 flex snap-x gap-2 overflow-x-auto px-4 sm:mx-0 sm:contents sm:px-0" aria-label="More photos">
          {rest.slice(0, 6).map((p, i) => (
            <li key={p.photo_id} className={`shrink-0 snap-start ${i >= 2 ? "sm:hidden" : ""}`}>
              <Photo src={p.url_small ?? p.url} alt={p.place_label || `${t.title} photo ${i + 2}`}
                     className="aspect-[4/3] w-40 rounded-xl sm:h-full sm:w-full" />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function CtaCard({ t }: { t: TourDetail }) {
  return (
    <div className="rounded-2xl border border-k-line bg-k-card p-5">
      <p className="text-[13px] leading-relaxed text-k-muted">
        Every Adventure Asia trip is tailor-made: start from this itinerary, change what you like, and an advisor
        prices and confirms it with you.
      </p>
      <div className="mt-4 flex flex-col gap-2">
        <ButtonLink href={planHref(t)} variant="primary" size="lg">
          <Icon name="route" className="h-4 w-4" /> Customise this trip
        </ButtonLink>
        <ButtonLink href={planHref(t, true)} variant="secondary" size="lg">
          <Icon name="chat" className="h-4 w-4" /> Ask an advisor
        </ButtonLink>
      </div>
    </div>
  );
}

export default async function TourPage({ params }: Props) {
  const { slug } = await params;
  // Slugs are lowercase; /tours/Hanoi-And-... moves to the canonical URL instead of a 404.
  const lower = slug.toLowerCase();
  if (lower !== slug) permanentRedirect(`/tours/${lower}`);
  const res = await getTour(slug);
  if (!res.ok) {
    // AA-986 L6: a retired tour is answered 410 by proxy.ts; until its 30-min list catches up, a 410 from the
    // browse Lambda shows the not-found page (404) rather than an error.
    if (res.status === 404 || res.status === 410) notFound();
    throw new Error(`tour read failed (${res.status})`);
  }
  const t = res.data;
  // The slug follows the title; an old link resolves by its source-id suffix and moves here.
  if (t.slug !== slug) permanentRedirect(`/tours/${t.slug}`);

  const points = routePoints(t);
  // AA-847 slice 6d: rivers, roads and trails of the tour, drawn as lines (never pins)
  const lines = routeLines(t);
  const inclusions = splitLines(t.inclusions);
  const exclusions = splitLines(t.exclusions);
  const days = daysText(t.duration_days, t.duration_raw);

  return (
    <div className="aa-site pb-24 lg:pb-0">
      <JsonLd data={tripJsonLd(t, points)} />
      <SiteHeader />

      <main id="main" className="mx-auto max-w-6xl px-4 pt-4">
        <nav aria-label="Breadcrumb" className="text-[12px] text-k-muted">
          <Link href="/" className="hover:text-k-accent-deep">Home</Link>
          {t.country && (
            <>
              {" / "}
              <Link href={`/destinations/${t.country_slug}`} className="hover:text-k-accent-deep">{t.country}</Link>
            </>
          )}
        </nav>

        <div className="mt-3">
          <Gallery t={t} />
        </div>

        <div className="mt-6 grid gap-8 lg:grid-cols-[1fr_320px]">
          <article className="min-w-0">
            <div className="flex flex-wrap gap-2">
              {t.country && (
                <Link href={`/destinations/${t.country_slug}`}>
                  <Badge tone="accent">{t.country}</Badge>
                </Link>
              )}
              {days && <Badge tone="neutral">{days}</Badge>}
              {t.group_size && <Badge tone="neutral">Group {t.group_size}</Badge>}
              {t.lifecycle_stage === "retired" && <Badge tone="info">No longer offered as is</Badge>}
            </div>
            <h1 className="mt-3 text-[28px] font-semibold leading-tight sm:text-[36px]">{t.title}</h1>
            {t.subtitle && <p className="mt-2 text-[15px] text-k-muted">{t.subtitle}</p>}
            {t.summary && <p className="mt-4 text-[15px] leading-relaxed text-k-body">{t.summary}</p>}
            {/* AA-819 (D4): the tour text below is the CIS writer model's output (automated checks). */}
            {(t.summary || t.subtitle) && (
              <AiLabel tone="kit" className="mt-2" label={AI_CATALOG_LABEL} shortLabel={AI_BRAND_SHORT} explanation={AI_CATALOG_EXPLANATION} />
            )}
            {t.best_time_to_go && (
              <p className="mt-3 flex items-center gap-2 text-[13px] text-k-muted">
                <Icon name="calendar" className="h-4 w-4 text-k-accent" /> Best time to go: {t.best_time_to_go}
              </p>
            )}

            {t.highlights.length > 0 && (
              <section className="mt-8" aria-labelledby="highlights">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <h2 id="highlights" className="text-[22px] font-semibold">Highlights</h2>
                  <AiLabel tone="kit" label={AI_CATALOG_LABEL} shortLabel={AI_BRAND_SHORT} explanation={AI_CATALOG_EXPLANATION} />
                </div>
                <ul className="mt-3 grid gap-2 sm:grid-cols-2">
                  {t.highlights.map((h) => (
                    <li key={h} className="flex gap-2 text-[14px] leading-relaxed text-k-body">
                      <Icon name="check" className="mt-1 h-4 w-4 shrink-0 text-k-accent" /> {h}
                    </li>
                  ))}
                </ul>
              </section>
            )}

            <section className="mt-8" aria-labelledby="itinerary">
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                <h2 id="itinerary" className="text-[22px] font-semibold">Day by day</h2>
                {(t.days.length > 0 || t.itinerary_text?.trim()) && (
                  <AiLabel tone="kit" label={AI_CATALOG_LABEL} shortLabel={AI_BRAND_SHORT} explanation={AI_CATALOG_EXPLANATION} />
                )}
              </div>
              {t.days.length === 0 && t.itinerary_text?.trim() ? (
                // Legacy itinerary text that is not in "Day N — Title" blocks: show it as written.
                <div className="mt-3 whitespace-pre-line rounded-2xl border border-k-line bg-k-card p-4 text-[14px] leading-relaxed text-k-body">
                  {t.itinerary_text.trim()}
                </div>
              ) : t.days.length === 0 ? (
                <p className="mt-3 rounded-2xl border border-dashed border-k-line bg-k-card p-4 text-[13px] text-k-muted">
                  The day-by-day itinerary for this tour is being prepared. An advisor can walk you through it.
                </p>
              ) : (
                <div className="mt-3 divide-y divide-k-line overflow-hidden rounded-2xl border border-k-line bg-k-card">
                  {t.days.map((d, i) => (
                    <details key={d.day_number} open={i === 0} className="group">
                      <summary className="flex cursor-pointer list-none items-center gap-3 px-4 py-3 [&::-webkit-details-marker]:hidden">
                        <span className="inline-flex h-8 min-w-8 shrink-0 items-center justify-center rounded-full bg-k-accent-tint px-2 text-[12px] font-bold text-k-accent-deep">
                          {d.day_number}
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="block text-[14px] font-semibold text-k-ink">{d.title}</span>
                          {d.places.length > 0 && (
                            <span className="block truncate text-[12px] text-k-muted">{d.places.join(" · ")}</span>
                          )}
                        </span>
                        <Icon name="chevron" className="h-4 w-4 shrink-0 text-k-muted transition group-open:rotate-180" />
                      </summary>
                      <div className="whitespace-pre-line px-4 pb-4 pl-[60px] text-[14px] leading-relaxed text-k-body">
                        {d.body}
                      </div>
                    </details>
                  ))}
                </div>
              )}
            </section>

            {(points.length > 0 || lines.length > 0) && (
              <section className="mt-8" aria-labelledby="route-map">
                <h2 id="route-map" className="text-[22px] font-semibold">Where you go</h2>
                <div className="mt-3 grid gap-4 md:grid-cols-[1fr_220px]">
                  <StaticMap points={points} lines={lines} route title={`${t.title} route`} caption="Numbers follow the itinerary order." />
                  <ol className="space-y-1.5 text-[13px] text-k-body">
                    {points.map((p, i) => (
                      <li key={`${p.name}-${i}`} className="flex items-center gap-2">
                        <span className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-k-accent text-[10px] font-bold text-k-on-accent">
                          {i + 1}
                        </span>
                        {p.name}
                      </li>
                    ))}
                    {lines.map((l) => (
                      <li key={`line-${l.name}`} className="flex items-center gap-2">
                        <span aria-label="Route" className="inline-flex h-5 w-5 shrink-0 items-center justify-center text-[13px] text-k-accent-deep">
                          〰
                        </span>
                        {l.name}
                      </li>
                    ))}
                  </ol>
                </div>
              </section>
            )}

            {(inclusions.length > 0 || exclusions.length > 0) && (
              <section className="mt-8 grid gap-4 sm:grid-cols-2" aria-label="What's included">
                {inclusions.length > 0 && (
                  <div className="rounded-2xl border border-k-line bg-k-card p-4">
                    <h2 className="text-[18px] font-semibold">Included</h2>
                    <ul className="mt-2 space-y-1.5">
                      {inclusions.map((x) => (
                        <li key={x} className="flex gap-2 text-[13px] text-k-body">
                          <Icon name="check" className="mt-0.5 h-4 w-4 shrink-0 text-k-success" /> {x}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {exclusions.length > 0 && (
                  <div className="rounded-2xl border border-k-line bg-k-card p-4">
                    <h2 className="text-[18px] font-semibold">Not included</h2>
                    <ul className="mt-2 space-y-1.5">
                      {exclusions.map((x) => (
                        <li key={x} className="flex gap-2 text-[13px] text-k-body">
                          <Icon name="close" className="mt-0.5 h-4 w-4 shrink-0 text-k-muted" /> {x}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </section>
            )}

            {t.activities.length > 0 && (
              <p className="mt-6 text-[12px] text-k-muted">
                Activities: {t.activities.map(titleCase).join(" · ")}
              </p>
            )}
          </article>

          <aside className="hidden lg:block">
            <div className="sticky top-20">
              <CtaCard t={t} />
            </div>
          </aside>
        </div>
      </main>

      {/* Mobile sticky CTA bar */}
      <div className="fixed inset-x-0 bottom-0 z-30 border-t border-k-line bg-k-card px-4 py-3 lg:hidden">
        <div className="mx-auto flex max-w-6xl gap-2">
          <ButtonLink href={planHref(t)} variant="primary" size="md" className="flex-1 whitespace-nowrap !px-3 normal-case !tracking-normal">
            Customise this trip
          </ButtonLink>
          <ButtonLink href={planHref(t, true)} variant="secondary" size="md" className="flex-1 whitespace-nowrap !px-3 normal-case !tracking-normal">
            Ask an advisor
          </ButtonLink>
        </div>
      </div>

      <SiteFooter />
    </div>
  );
}
