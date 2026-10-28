// AA-812 — tour page (PDP), SSR + ISR from the CIS catalog (contract catalog.tour v1) through the
// browse Lambda. No price: there is no price source yet (prices belong to AA-Booking, ADR 0005), so
// the "from" price is hidden rather than invented.
// AA-995 "Golden Hour" (spec §4.3): solid navy header → breadcrumb → gallery (the LCP: a swipe strip below md, a
// 1 + 2 mosaic from md; the first photo resolved to its CDN URL on the server) → title block, key facts, activity
// chips → highlights → day-by-day timeline → where you go → included / not included, next to a sticky "Make it yours"
// card (lg) or above a sticky bottom bar (below lg) → navy CTA band (long itineraries) → "More from this trip" ribbon
// (>= 8 unique photos after the gallery's 3). Same single catalog read as before; "Similar tours" is not built (it would add
// a browse read per tour page, spec R11).
import type { Metadata } from "next";
import Link from "next/link";
import { notFound, permanentRedirect } from "next/navigation";
import AiLabel, { AI_BRAND_SHORT, AI_CATALOG_EXPLANATION, AI_CATALOG_LABEL } from "@/components/AiLabel";
import CtaBand, { CTA_REASSURANCES } from "@/components/site/CtaBand";
import DayToggleAll from "@/components/site/DayToggleAll";
import JsonLd from "@/components/site/JsonLd";
import NoPhotoTile from "@/components/site/NoPhotoTile";
import PhotoRibbon from "@/components/site/PhotoRibbon";
import RevealObserver from "@/components/site/RevealObserver";
import SiteFooter from "@/components/site/SiteFooter";
import SiteHeader from "@/components/site/SiteHeader";
import StaticMap from "@/components/site/StaticMap";
import type { MapPoint } from "@/lib/tourMap";
import { getTour, siteUrl } from "@/lib/catalog";
import type { CatalogPhoto, TourDetail } from "@/lib/catalogTypes";
import { resolveHeroPhoto, type HeroPhoto } from "@/lib/photoResolve";
import { RIBBON_MIN_ANIMATED, uniqueRibbonItems, type RibbonItem } from "@/lib/photos";
import { cvSize } from "@/lib/contentVisibility";
import { clip, coverAlt, daysText, splitLines, titleCase } from "@/lib/text";
import { routeLines, routePoints } from "@/lib/tourMap";
import { Badge, ButtonLink, Icon } from "@/ui";

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

// How many photos the gallery strip shows below md (the mosaic shows 3 from md); the ribbon takes the photos after the
// mosaic's 3 when at least 8 unique ones remain (spec §4.3 row 12), so it always moves. Counted on the same deduped
// list PhotoRibbon renders, so the gallery chip never links to a ribbon that is not there.
const STRIP_PHOTOS = 7;
const MOSAIC_PHOTOS = 3;
// The navy CTA band only on long pages (spec: > 3 viewports); a server page cannot measure, so long = 5+ days.
const CTA_BAND_MIN_DAYS = 5;
// Spec §4.3 row 5: up to 5 activity chips, then "+N more".
const ACTIVITY_CHIPS = 5;
// "Expand all / Collapse all" over the timeline only when there is more than one day to open.
const DAY_TOGGLE_MIN_DAYS = 2;

function SectionHeading({ id, children, ai = false }: { id: string; children: string; ai?: boolean }) {
  return (
    <div className="flex flex-wrap items-end gap-x-4 gap-y-2">
      <div>
        <h2 id={id} className="font-script text-script-sm text-k-ink">{children}</h2>
        <span aria-hidden className="mt-2 block h-0.5 w-10 rounded-pill bg-k-accent" />
      </div>
      {ai && <AiLabel tone="kit" label={AI_CATALOG_LABEL} shortLabel={AI_BRAND_SHORT} explanation={AI_CATALOG_EXPLANATION} />}
    </div>
  );
}

// The CDN preconnect for the LCP image (React 19 hoists the <link> to <head>, as in PageHero).
function HeroPreconnect({ hero }: { hero: HeroPhoto | null }) {
  return hero?.preconnect ? <link rel="preconnect" href={hero.preconnect} /> : null;
}

// `photos`: the tour photos that have a URL (filtered once in TourPage); `ribbon`: id and photo count of the
// "More from this trip" ribbon when the page renders one.
function Gallery({
  t,
  photos,
  hero,
  ribbon,
}: {
  t: TourDetail;
  photos: CatalogPhoto[];
  hero: HeroPhoto | null;
  ribbon: { id: string; count: number } | null;
}) {
  if (photos.length === 0) {
    // No tour photo: the API may send a destination photo instead (cover_source), captioned so it never reads as a
    // picture of this tour. No photo at all: the designed no-photo tile, never the camera icon.
    const fallback = t.cover_url && t.cover_source !== "tour" && t.cover_place;
    return (
      <div className="mx-auto max-w-page md:px-gutter">
        <HeroPreconnect hero={hero} />
        <figure className="relative h-[260px] overflow-hidden bg-k-hero-bg md:h-[440px] md:rounded-panel">
          {hero ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={hero.src} srcSet={hero.srcSet ?? undefined} sizes="(min-width: 1248px) 1184px, 100vw" alt={coverAlt(t)}
                 width={1600} height={900} fetchPriority="high" loading="eager" decoding="async" className="h-full w-full object-cover" />
          ) : (
            <NoPhotoTile name={t.country ?? "Adventure Asia"} caption={t.title} className="h-full w-full" />
          )}
          {hero && fallback && (
            <figcaption className="absolute bottom-3 left-3 max-w-[calc(100%-24px)] truncate rounded-pill bg-k-scrim px-3 py-1 text-caption text-white">
              Destination photo · {t.cover_place}
            </figcaption>
          )}
        </figure>
      </div>
    );
  }
  const shown = photos.slice(0, STRIP_PHOTOS);
  // From md the mosaic: one large photo (2 rows) and up to two stacked; with 1 or 2 photos they fill the frame.
  const span = (i: number) => {
    if (photos.length === 1) return "md:col-span-2 md:row-span-2";
    if (i === 0) return "md:row-span-2";
    if (photos.length === 2) return "md:row-span-2";
    return "";
  };
  return (
    <div className="relative mx-auto max-w-page md:px-gutter">
      <HeroPreconnect hero={hero} />
      {/* Below md: a swipe strip (focusable, so arrow keys scroll it). From md: the mosaic, no scrolling. */}
      <ul
        aria-label="Tour photos"
        tabIndex={0}
        className="tp-snap-row flex gap-2 overflow-x-auto px-gutter md:grid md:h-[440px] md:grid-cols-[2fr_1fr] md:grid-rows-2 md:overflow-hidden md:rounded-panel md:px-0"
      >
        {shown.map((p, i) => (
          <li
            key={p.photo_id}
            className={`relative h-[260px] ${photos.length === 1 ? "w-full" : "w-[88%]"} shrink-0 overflow-hidden rounded-tile bg-k-hero-bg md:h-auto md:w-auto md:rounded-none ${span(i)} ${i >= MOSAIC_PHOTOS ? "md:hidden" : ""}`}
          >
            {i === 0 && hero ? (
              // The LCP image: resolved CDN URL, eager, high priority.
              // eslint-disable-next-line @next/next/no-img-element
              <img src={hero.src} srcSet={hero.srcSet ?? undefined} sizes="(min-width: 768px) 66vw, 88vw"
                   alt={p.place_label || t.title} width={1600} height={900} fetchPriority="high" loading="eager" decoding="async"
                   className="h-full w-full object-cover" />
            ) : (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={((i === 0 ? p.url : p.url_small) ?? p.url ?? p.url_small) as string} alt={p.place_label || `${t.title} photo ${i + 1}`}
                   width={600} height={450} loading={i === 0 ? "eager" : "lazy"} decoding="async" className="h-full w-full object-cover" />
            )}
          </li>
        ))}
      </ul>
      {photos.length > 1 && (
        ribbon ? (
          <a href={`#${ribbon.id}`}
             className="absolute bottom-3 left-[calc(var(--tp-gutter)+12px)] inline-flex min-h-8 items-center gap-1.5 rounded-pill bg-k-scrim px-3 text-caption text-white hover:underline md:left-[calc(var(--tp-gutter)+16px)] md:bottom-4">
            <Icon name="camera" className="h-4 w-4" /> View {ribbon.count} more photos
          </a>
        ) : (
          <span className="pointer-events-none absolute bottom-3 left-[calc(var(--tp-gutter)+12px)] inline-flex min-h-8 items-center gap-1.5 rounded-pill bg-k-scrim px-3 text-caption text-white md:bottom-4 md:left-[calc(var(--tp-gutter)+16px)]">
            <Icon name="camera" className="h-4 w-4" /> {photos.length} photos
          </span>
        )
      )}
    </div>
  );
}

function Reassurances() {
  return (
    <ul className="space-y-2 text-small text-k-body">
      {CTA_REASSURANCES.map((r) => (
        <li key={r} className="flex items-start gap-2">
          <Icon name="check" className="mt-0.5 h-4 w-4 shrink-0 text-k-accent-deep" /> {r}
        </li>
      ))}
    </ul>
  );
}

function CtaCard({ t }: { t: TourDetail }) {
  return (
    <div className="rounded-panel border border-k-line bg-k-card p-6 shadow-e3">
      <p className="font-script text-script-sm text-k-ink">Make it yours</p>
      <p className="mt-3 text-small text-k-muted">
        Every Adventure Asia trip is tailor-made: start from this itinerary, change what you like, and an advisor
        prices and confirms it with you.
      </p>
      <div className="mt-4">
        <Reassurances />
      </div>
      <div className="mt-6 flex flex-col gap-2">
        <ButtonLink href={planHref(t)} variant="primary" size="lg" className="min-h-12">
          <Icon name="route" className="h-4 w-4" /> Customise this trip
        </ButtonLink>
        <ButtonLink href={planHref(t, true)} variant="secondary" size="lg" className="min-h-12">
          <Icon name="chat" className="h-4 w-4" /> Ask an advisor
        </ButtonLink>
      </div>
    </div>
  );
}

function KeyFacts({ facts }: { facts: { icon: string; label: string; value: string }[] }) {
  if (facts.length === 0) return null;
  return (
    <dl className="mt-6 grid grid-cols-2 gap-3 md:grid-cols-4">
      {facts.map((f) => (
        <div key={f.label} className="flex min-w-0 items-start gap-3 rounded-tile border border-k-line bg-k-card p-3">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-k-accent-tint">
            <Icon name={f.icon} className="h-4 w-4 text-k-accent-deep" />
          </span>
          <div className="min-w-0">
            <dt className="text-caption font-normal text-k-muted">{f.label}</dt>
            <dd className="text-small font-semibold text-k-ink">{f.value}</dd>
          </div>
        </div>
      ))}
    </dl>
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
  const placeCount = new Set([...points.map((p) => p.name), ...lines.map((l) => l.name ?? "")].filter(Boolean)).size;
  const facts = [
    days && { icon: "calendar", label: "Duration", value: days },
    placeCount > 0 && { icon: "pin", label: "Places", value: `${placeCount} place${placeCount > 1 ? "s" : ""}` },
    t.best_time_to_go && { icon: "sun", label: "Best time to go", value: t.best_time_to_go },
    t.group_size && { icon: "users", label: "Group size", value: t.group_size },
  ].filter(Boolean) as { icon: string; label: string; value: string }[];
  // Photos without any URL are dropped once here, so the gallery never renders an empty <img src> and the destination
  // cover fallback (with its "Destination photo" caption) applies only when no tour photo can be shown.
  const photos = t.photos.filter((p) => p.url || p.url_small);
  const ribbonItems: RibbonItem[] = uniqueRibbonItems(
    photos.slice(MOSAIC_PHOTOS).map((p, i) => ({
      href: null,
      src: p.url_small ?? p.url,
      alt: p.place_label || `${t.title} photo ${i + MOSAIC_PHOTOS + 1}`,
      caption: p.place_label || t.country || t.title,
    })),
  );
  const ribbon = ribbonItems.length >= RIBBON_MIN_ANIMATED ? { id: "more-photos", count: ribbonItems.length } : null;
  // The LCP image: the first tour photo, else the fallback cover, resolved to its CDN URL on the server.
  const hero = await resolveHeroPhoto(photos[0] ? (photos[0].url ?? photos[0].url_small) : t.cover_url);

  return (
    <div className="aa-site pb-[calc(80px+env(safe-area-inset-bottom))] lg:pb-0">
      <JsonLd data={tripJsonLd(t, points)} />
      <SiteHeader />

      <main id="main">
        <nav aria-label="Breadcrumb" className="mx-auto max-w-page px-gutter pb-3 pt-4 text-caption font-normal text-k-muted">
          <Link href="/" className="hover:text-k-accent-deep">Home</Link>
          <span aria-hidden> / </span>
          <Link href="/destinations" className="hover:text-k-accent-deep">Destinations</Link>
          {t.country && (
            <>
              <span aria-hidden> / </span>
              <Link href={`/destinations/${t.country_slug}`} className="hover:text-k-accent-deep">{t.country}</Link>
            </>
          )}
        </nav>

        <Gallery t={t} photos={photos} hero={hero} ribbon={ribbon} />

        <div className="mx-auto grid max-w-page gap-10 px-gutter pb-section pt-8 lg:grid-cols-12">
          <article className="min-w-0 lg:col-span-8">
            <div className="flex flex-wrap gap-2">
              {t.country && (
                <Link href={`/destinations/${t.country_slug}`} className="rounded-full">
                  <Badge tone="accent">{t.country}</Badge>
                </Link>
              )}
              {t.lifecycle_stage === "retired" && <Badge tone="info">No longer offered as is</Badge>}
            </div>
            <h1 className="mt-3 text-h1 text-k-ink">{t.title}</h1>
            {t.subtitle && <p className="mt-2 text-lead text-k-muted">{t.subtitle}</p>}
            {t.summary && <p className="mt-4 max-w-[72ch] text-body text-k-body">{t.summary}</p>}
            {/* AA-819 (D4): the tour text below is the CIS writer model's output (automated checks). */}
            {(t.summary || t.subtitle) && (
              <AiLabel tone="kit" className="mt-2" label={AI_CATALOG_LABEL} shortLabel={AI_BRAND_SHORT} explanation={AI_CATALOG_EXPLANATION} />
            )}

            <KeyFacts facts={facts} />

            {t.activities.length > 0 && (
              <ul aria-label="Activities" className="mt-4 flex flex-wrap gap-2">
                {t.activities.slice(0, ACTIVITY_CHIPS).map((a) => (
                  <li key={a} className="rounded-pill bg-k-accent-tint px-3 py-1 text-caption text-k-accent-deep">{titleCase(a)}</li>
                ))}
                {t.activities.length > ACTIVITY_CHIPS && (
                  <li className="rounded-pill border border-k-line px-3 py-1 text-caption text-k-muted"
                      title={t.activities.slice(ACTIVITY_CHIPS).map(titleCase).join(", ")}>
                    <span className="sr-only">and {t.activities.length - ACTIVITY_CHIPS} more: {t.activities.slice(ACTIVITY_CHIPS).map(titleCase).join(", ")}</span>
                    <span aria-hidden>+{t.activities.length - ACTIVITY_CHIPS} more</span>
                  </li>
                )}
              </ul>
            )}

            {t.highlights.length > 0 && (
              <section className="mt-12" aria-labelledby="highlights">
                <SectionHeading id="highlights" ai>Highlights</SectionHeading>
                <ul className="mt-5 grid gap-3 md:grid-cols-2">
                  {t.highlights.map((h) => (
                    <li key={h} className="flex gap-3 text-body text-k-body">
                      <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-k-accent-tint">
                        <Icon name="check" className="h-3.5 w-3.5 text-k-accent-deep" />
                      </span>
                      {h}
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {/* Not content-visibility: its AI label's explanation popover could be clipped by paint containment. */}
            <section className="mt-12" aria-labelledby="itinerary">
              <div className="flex max-w-[72ch] flex-wrap items-end justify-between gap-x-4 gap-y-1">
                <SectionHeading id="itinerary" ai={t.days.length > 0 || Boolean(t.itinerary_text?.trim())}>Day by day</SectionHeading>
                {t.days.length >= DAY_TOGGLE_MIN_DAYS && <DayToggleAll listId="itinerary-days" />}
              </div>
              {t.days.length === 0 && t.itinerary_text?.trim() ? (
                // Legacy itinerary text that is not in "Day N — Title" blocks: show it as written.
                <div className="mt-5 max-w-[72ch] whitespace-pre-line rounded-card border border-k-line bg-k-card p-5 text-body text-k-body">
                  {t.itinerary_text.trim()}
                </div>
              ) : t.days.length === 0 ? (
                <p className="mt-5 rounded-card border border-dashed border-k-accent-border bg-k-card p-5 text-small text-k-muted">
                  The day-by-day itinerary for this tour is being prepared. An advisor can walk you through it.
                </p>
              ) : (
                // Timeline: a dotted gold line behind the day circles; each day is a <details>, day 1 open.
                <ol id="itinerary-days" className="tp-timeline relative mt-5 max-w-[72ch] space-y-3">
                  {t.days.map((d, i) => (
                    <li key={d.day_number} className="relative pl-12">
                      <details open={i === 0} className="tp-day group rounded-card border border-k-line bg-k-card">
                        <summary className="flex min-h-14 cursor-pointer list-none items-center gap-4 px-4 py-3 [&::-webkit-details-marker]:hidden">
                          {/* The day circle sits on the timeline, left of the card (positioned on the li). */}
                          <span className="absolute left-0 top-2.5 z-10 inline-flex h-9 min-w-9 items-center justify-center rounded-full border border-k-accent-border bg-k-accent-tint px-2 text-small font-semibold text-k-accent-deep ring-4 ring-k-bg">
                            <span className="sr-only">Day </span>{d.day_number}
                          </span>
                          <span className="min-w-0 flex-1">
                            <span className="block text-body font-semibold text-k-ink">{d.title}</span>
                            {d.places.length > 0 && (
                              <span className="block truncate text-caption font-normal text-k-muted">{d.places.join(" · ")}</span>
                            )}
                          </span>
                          <Icon name="chevron" className="h-5 w-5 shrink-0 text-k-muted transition-transform duration-base ease-std group-open:rotate-180" />
                        </summary>
                        <div className="whitespace-pre-line px-4 pb-5 text-body text-k-body">{d.body}</div>
                      </details>
                    </li>
                  ))}
                </ol>
              )}
            </section>

            {(points.length > 0 || lines.length > 0) && (
              <section className="tp-cv mt-12" style={cvSize(560)} aria-labelledby="route-map">
                <SectionHeading id="route-map">Where you go</SectionHeading>
                <div className="mt-5 grid gap-5 rounded-panel bg-k-sand p-4 md:grid-cols-[1fr_220px] md:p-6">
                  <StaticMap points={points} lines={lines} route title={`${t.title} route`} caption="Numbers follow the itinerary order." />
                  <ol className="space-y-2 text-small text-k-body">
                    {points.map((p, i) => (
                      <li key={`${p.name}-${i}`} className="flex items-center gap-2">
                        <span className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-k-accent text-caption font-semibold text-k-on-accent">
                          {i + 1}
                        </span>
                        {p.name}
                      </li>
                    ))}
                    {lines.map((l) => (
                      <li key={`line-${l.name}`} className="flex items-center gap-2">
                        <span role="img" aria-label="Route" className="inline-flex h-6 w-6 shrink-0 items-center justify-center text-k-accent-deep">
                          <Icon name="wave" className="h-5 w-5" />
                        </span>
                        {l.name}
                      </li>
                    ))}
                  </ol>
                </div>
              </section>
            )}

            {(inclusions.length > 0 || exclusions.length > 0) && (
              <section style={cvSize(360)} className="tp-cv mt-12 grid gap-4 md:grid-cols-2" aria-label="What's included">
                {inclusions.length > 0 && (
                  <div className="rounded-card border border-k-line bg-k-card p-5 shadow-e1">
                    <h2 className="text-h3 text-k-ink">Included</h2>
                    <ul className="mt-3 space-y-2">
                      {inclusions.map((x) => (
                        <li key={x} className="flex gap-2 text-small text-k-body">
                          <Icon name="check" className="mt-0.5 h-4 w-4 shrink-0 text-k-success" /> {x}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {exclusions.length > 0 && (
                  <div className="rounded-card border border-k-line bg-k-card p-5 shadow-e1">
                    <h2 className="text-h3 text-k-ink">Not included</h2>
                    <ul className="mt-3 space-y-2">
                      {exclusions.map((x) => (
                        <li key={x} className="flex gap-2 text-small text-k-body">
                          <Icon name="close" className="mt-0.5 h-4 w-4 shrink-0 text-k-muted" /> {x}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </section>
            )}
          </article>

          <aside className="hidden lg:col-span-4 lg:block">
            <div className="sticky top-[calc(var(--tp-header-h)+24px)]">
              <CtaCard t={t} />
            </div>
          </aside>
        </div>

        {t.days.length >= CTA_BAND_MIN_DAYS && (
          <CtaBand id="plan-this-trip" planHref={planHref(t)} askHref={planHref(t, true)} cvHeight={420} />
        )}

        {ribbon && (
          <PhotoRibbon id={ribbon.id} title="More from this trip" label={`More photos from ${t.title}`} items={ribbonItems} cvHeight={440} />
        )}
      </main>

      {/* Mobile sticky CTA bar (after main in the DOM; the page keeps bottom padding so the footer is not covered). */}
      <div className="fixed inset-x-0 bottom-0 z-sticky-cta border-t border-k-line bg-k-card px-gutter pb-[calc(12px+env(safe-area-inset-bottom))] pt-3 shadow-e3 lg:hidden">
        <div className="mx-auto flex max-w-page gap-2">
          <ButtonLink href={planHref(t)} variant="primary" size="md" className="min-h-12 flex-1 whitespace-nowrap !px-3 normal-case !tracking-normal">
            Customise this trip
          </ButtonLink>
          <ButtonLink href={planHref(t, true)} variant="secondary" size="md" className="min-h-12 flex-1 whitespace-nowrap !px-3 normal-case !tracking-normal">
            Ask an advisor
          </ButtonLink>
        </div>
      </div>

      <SiteFooter />
      <RevealObserver />
    </div>
  );
}
