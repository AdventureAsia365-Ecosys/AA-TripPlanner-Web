// AA-812 — destination (country) page, SSR + ISR from the CIS catalog. The catalog has no editorial
// country text yet (contract catalog.tour v1), so the intro is written from facts only: tour count,
// trip lengths, the places and activities the tours really include.
// AA-995 "Golden Hour" (spec §4.2): photo hero under the transparent header (cover resolved to its CDN URL on the
// server, it is the LCP; no cover = the navy no-photo hero) → tours (white; a carousel above 3 tours, else a grid) →
// places our tours visit (sand; map + numbered list) → navy CTA band → photo ribbon "Around {country}" → footer.
// Same single catalog read as before (getDestination); the hero adds one server-side redirect lookup per size.
import type { Metadata } from "next";
import Link from "next/link";
import { notFound, permanentRedirect } from "next/navigation";
import CtaBand from "@/components/site/CtaBand";
import { destinationMeta } from "@/components/site/DestinationTile";
import JsonLd from "@/components/site/JsonLd";
import { MountainMotif, noPhotoTone } from "@/components/site/NoPhotoTile";
import PageHero from "@/components/site/PageHero";
import PhotoRibbon from "@/components/site/PhotoRibbon";
import RevealObserver from "@/components/site/RevealObserver";
import SectionTitle from "@/components/site/SectionTitle";
import SiteFooter from "@/components/site/SiteFooter";
import SiteHeader from "@/components/site/SiteHeader";
import StaticMap, { staticMapUrl } from "@/components/site/StaticMap";
import { placeLines } from "@/lib/tourMap";
import TourCard from "@/components/site/TourCard";
import TourCarousel from "@/components/site/TourCarousel";
import { getDestination, siteUrl } from "@/lib/catalog";
import type { CountryPlace, DestinationPage } from "@/lib/catalogTypes";
import { cvSize } from "@/lib/contentVisibility";
import { firstUseOfCover } from "@/lib/photos";
import { resolveHeroPhoto } from "@/lib/photoResolve";
import { formatDayRange, slugify, titleCase } from "@/lib/text";
import { ButtonLink, EmptyState, Icon } from "@/ui";

// Route segment config must be a literal (Next 15 reads it statically and fails the build on an
// imported constant). Keep it equal to CATALOG_REVALIDATE_S (lib/segmentConfig.test.ts checks).
export const revalidate = 1800;

export function generateStaticParams() {
  return [];
}

// Next 15: route params arrive as a Promise (async request APIs).
type Props = { params: Promise<{ country: string }> };

function intro(d: DestinationPage): string {
  const range =
    d.min_days && d.max_days ? formatDayRange(d.min_days, d.max_days) : null;
  const places = d.places.slice(0, 4).map((p) => p.name).filter(Boolean);
  const parts = [
    `Adventure Asia has ${d.tour_count} tour${d.tour_count > 1 ? "s" : ""} in ${d.country}${range ? `, from ${range}` : ""}.`,
  ];
  if (places.length) parts.push(`They take in places such as ${places.join(", ")}.`);
  if (d.top_activities.length)
    parts.push(`Typical days include ${d.top_activities.slice(0, 3).map((a) => titleCase(a).toLowerCase()).join(", ")}.`);
  return parts.join(" ");
}

// How many places the page lists; the map pins exactly these, with the same numbers.
const LISTED_PLACES = 12;
// Below lg the list shows this many places; the rest open from a "Show all" disclosure (spec §4.2 row 4).
const PLACES_FIRST = 8;
// More tours than this (or more than fit one row at 1440) become the featured carousel; fewer stay a grid.
const CAROUSEL_FROM = 4;

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { country } = await params;
  const res = await getDestination(slugify(country));
  if (!res.ok) return { title: "Destination", robots: { index: false } };
  const d = res.data;
  return {
    title: `${d.country} tours and trip ideas`,
    description: intro(d).slice(0, 160),
    alternates: { canonical: `/destinations/${d.slug}` },
    openGraph: { title: `${d.country} — Adventure Asia`, images: d.cover_url ? [{ url: d.cover_url }] : undefined },
  };
}

function PlaceRow({ p, label }: { p: CountryPlace; label: string }) {
  return (
    <li className="flex items-center gap-3 rounded-tile border border-k-line bg-k-card p-2.5">
      {p.cover_url ? (
        // The place name is right next to it: the thumbnail is decorative.
        // eslint-disable-next-line @next/next/no-img-element
        <img src={p.cover_url} alt="" width={56} height={56} loading="lazy" decoding="async"
             className="h-14 w-14 shrink-0 rounded-chip object-cover" />
      ) : (
        <span aria-hidden className="relative h-14 w-14 shrink-0 overflow-hidden rounded-chip bg-k-band">
          <MountainMotif className="-bottom-0.5 right-0 w-full text-k-band-accent" />
        </span>
      )}
      <div className="min-w-0">
        <p className="truncate text-small font-semibold text-k-ink">
          {p.precision === "line" ? (
            <span role="img" aria-label="Route" className="mr-1.5 inline-flex align-middle text-k-accent-deep">
              <Icon name="wave" className="h-4 w-4" />
            </span>
          ) : (
            <span className="mr-1.5 text-k-accent-deep">{label}</span>
          )}
          {p.name}
        </p>
        <p className="truncate text-caption font-normal text-k-muted">
          {p.tour_count} tour{p.tour_count > 1 ? "s" : ""}
          {p.activity_types.length ? ` · ${p.activity_types.slice(0, 2).map(titleCase).join(", ")}` : ""}
        </p>
      </div>
    </li>
  );
}

export default async function DestinationRoute({ params }: Props) {
  const { country } = await params;
  // /destinations/Vietnam or /destinations/Sri_Lanka move to the canonical lowercase slug.
  const canonical = slugify(country);
  if (canonical && canonical !== country) permanentRedirect(`/destinations/${canonical}`);
  const res = await getDestination(country);
  if (!res.ok) {
    if (res.status === 404) notFound();
    throw new Error(`destination read failed (${res.status})`);
  }
  const d = res.data;
  // A week by default, or the shortest tour here when that is longer.
  const days = Math.min(30, Math.max(7, d.min_days ?? 7));
  const planHref = `/plan?${new URLSearchParams({ country: d.country, days: String(days) })}`;
  const listed = d.places.slice(0, LISTED_PLACES);
  // AA-847 slice 6d: a river / road / trail is a line on the map (its shape_lo), never a numbered pin.
  const pinPlaces = listed.filter((p) => p.precision !== "line");
  const points = pinPlaces.map((p) => ({
    name: p.name ?? "", lat: p.lat, lng: p.lng, located_by: p.located_by, precision: p.precision,
  }));
  const lines = placeLines(listed);
  const pinNumber = new Map(pinPlaces.map((p, i) => [p.destination_id, i + 1]));
  const placeLabel = (p: CountryPlace, i: number) => `${pinNumber.get(p.destination_id) ?? i + 1}.`;
  // Without a map (no Mapbox token, or nothing to pin) the list takes the full width in two columns.
  const hasMap = Boolean(staticMapUrl(points, { lines }));
  const firstPlaces = listed.slice(0, PLACES_FIRST);
  const morePlaces = listed.slice(PLACES_FIRST);

  const hero = await resolveHeroPhoto(d.cover_url);
  // Duplicate-cover rule (spec §3.4): a cover already shown on an earlier card becomes the no-photo tile.
  const tourPhoto = firstUseOfCover(d.tours, (t) => t.cover_url_small ?? t.cover_url);
  let noPhoto = 0;
  const tourTone = tourPhoto.map((on) => (on ? "band" : noPhotoTone(noPhoto++)));
  const tourCards = d.tours.map((t, i) => (
    <li key={t.source_id} className="min-w-0">
      <TourCard tour={t} eager={false} showPhoto={tourPhoto[i]} noPhotoTone={tourTone[i]} />
    </li>
  ));
  const seeAll =
    d.tour_count > d.tours.length ? (
      <Link href={planHref} className="text-small font-semibold text-k-accent-deep">
        <span className="tp-link">See them all in the planner</span> <span aria-hidden className="tp-link-arrow">→</span>
      </Link>
    ) : undefined;
  const toursLead =
    d.tour_count > d.tours.length
      ? `Showing ${d.tours.length} of ${d.tour_count}. Open one and change it day by day.`
      : "Real Adventure Asia routes. Open one and change it day by day.";

  return (
    <div className="aa-site">
      <JsonLd
        data={{
          "@context": "https://schema.org",
          "@type": "TouristDestination",
          name: d.country,
          url: `${siteUrl()}/destinations/${d.slug}`,
          description: intro(d),
          image: d.cover_url ?? undefined,
          includesAttraction: d.places.slice(0, 15).map((p) => ({
            "@type": "TouristAttraction",
            name: p.name,
            geo: { "@type": "GeoCoordinates", latitude: p.lat, longitude: p.lng },
          })),
        }}
      />
      <SiteHeader overHero />

      <main id="main">
        {/* 2 · Hero (LCP). No entrance animation in the first screen; the photo settles once. */}
        <PageHero photo={hero} alt={d.country}>
          <div className="mx-auto max-w-page px-gutter pb-10 md:pb-14">
            <div className="max-w-[720px] text-white">
              <nav aria-label="Breadcrumb" className="text-caption font-normal">
                <Link href="/" className="underline-offset-2 hover:underline">Home</Link>
                <span aria-hidden> / </span>
                <Link href="/destinations" className="underline-offset-2 hover:underline">Destinations</Link>
              </nav>
              <h1 className="mt-3 text-h1 uppercase tracking-[0.08em] !text-white">{d.country}</h1>
              <p className="mt-2 text-small font-semibold">{destinationMeta(d)}</p>
              <p className="mt-3 max-w-[60ch] text-small md:text-body">{intro(d)}</p>
              {d.top_activities.length > 0 && (
                <ul aria-label="Typical activities" className="mt-4 flex flex-wrap gap-2">
                  {d.top_activities.slice(0, 3).map((a) => (
                    <li key={a} className="rounded-pill bg-k-scrim px-3 py-1 text-caption text-white">{titleCase(a)}</li>
                  ))}
                </ul>
              )}
              <div className="mt-6 flex flex-col gap-3 sm:flex-row">
                <ButtonLink href={planHref} variant="primary" size="lg" className="min-h-12">
                  <Icon name="route" className="h-4 w-4" /> Plan a trip to {d.country}
                </ButtonLink>
                {d.tours.length > 0 && (
                  <ButtonLink href="#tours" variant="on-dark" size="lg" className="hidden min-h-12 md:inline-flex" prefetch={false}>
                    See the tours
                  </ButtonLink>
                )}
              </div>
            </div>
          </div>
        </PageHero>

        {/* 3 · Tours in {country} (white) */}
        <section id="tours" aria-labelledby="top-tours" className="scroll-mt-header bg-k-card py-section">
          <div className="mx-auto max-w-page px-gutter">
            {d.tours.length === 0 ? (
              <>
                <SectionTitle id="top-tours" title={`Tours in ${d.country}`} />
                <div className="mt-8">
                  <EmptyState title={`No tours in ${d.country} yet`}>
                    New Adventure Asia tours appear here as soon as they are published. You can still plan on the map.
                  </EmptyState>
                </div>
              </>
            ) : d.tours.length >= CAROUSEL_FROM ? (
              <TourCarousel id="country-tours" label={`Tours in ${d.country}`} titleId="top-tours" title={`Tours in ${d.country}`}
                            lead={toursLead} action={seeAll}>
                {tourCards}
              </TourCarousel>
            ) : (
              <>
                <div data-reveal="">
                  <SectionTitle id="top-tours" title={`Tours in ${d.country}`} lead={toursLead} action={seeAll} />
                </div>
                <ul className="mt-8 grid gap-4 md:grid-cols-2 md:gap-6 lg:grid-cols-3" aria-label={`Tours in ${d.country}`}>
                  {tourCards}
                </ul>
              </>
            )}
          </div>
        </section>

        {/* 4 · Places our tours visit (sand): map + the same numbers in the list. Sections 4–6 are below the fold:
            content-visibility (globals.css .tp-cv, spec R14); the hero and the tours (the #tours anchor) are not. */}
        <section aria-labelledby="places" className="tp-cv bg-k-sand py-section" style={cvSize(d.places.length === 0 ? 420 : 960)}>
          <div className="mx-auto max-w-page px-gutter">
            <div data-reveal="">
              <SectionTitle id="places" title="Places our tours visit" />
            </div>
            {d.places.length === 0 ? (
              <div className="mt-8">
                <EmptyState title="Places are being mapped">The places on these tours are not on the map yet.</EmptyState>
              </div>
            ) : (
              <div className="mt-10 grid grid-cols-[minmax(0,1fr)] gap-6 lg:grid-cols-12 lg:items-start lg:gap-8">
                {hasMap && (
                  <div className="lg:sticky lg:top-[calc(var(--tp-header-h)+24px)] lg:col-span-7">
                    <StaticMap points={points} lines={lines} title={`Places in ${d.country}`} caption="Numbers match the list of places." />
                  </div>
                )}
                {/* Desktop: the whole list in its own scroll area next to the sticky map (focusable, so the keyboard
                    can scroll it). Below lg: the first places, then "Show all". */}
                <div
                  className={hasMap ? "lg:col-span-5 lg:max-h-[560px] lg:overflow-y-auto lg:pr-1" : "lg:col-span-12"}
                  {...(hasMap ? { tabIndex: 0, role: "region", "aria-label": `Places in ${d.country}, list` } : {})}
                >
                  <ol className={`grid grid-cols-[minmax(0,1fr)] gap-2 ${hasMap ? "" : "md:grid-cols-2"}`}>
                    {firstPlaces.map((p, i) => <PlaceRow key={p.destination_id} p={p} label={placeLabel(p, i)} />)}
                  </ol>
                  {morePlaces.length > 0 && (
                    <>
                      <ol start={PLACES_FIRST + 1} className={`mt-2 hidden gap-2 lg:grid ${hasMap ? "" : "md:grid-cols-2"}`}>
                        {morePlaces.map((p, i) => (
                          <PlaceRow key={p.destination_id} p={p} label={placeLabel(p, i + PLACES_FIRST)} />
                        ))}
                      </ol>
                      <details className="group mt-2 lg:hidden">
                        <summary className="flex min-h-11 cursor-pointer list-none items-center justify-center gap-2 rounded-pill border border-k-accent-border text-small font-semibold text-k-accent-deep [&::-webkit-details-marker]:hidden">
                          Show all {listed.length} places
                          <Icon name="chevron" className="h-4 w-4 transition-transform duration-base ease-std group-open:rotate-180" />
                        </summary>
                        <ol start={PLACES_FIRST + 1} className="mt-2 grid grid-cols-[minmax(0,1fr)] gap-2">
                          {morePlaces.map((p, i) => (
                            <PlaceRow key={p.destination_id} p={p} label={placeLabel(p, i + PLACES_FIRST)} />
                          ))}
                        </ol>
                      </details>
                    </>
                  )}
                </div>
              </div>
            )}
          </div>
        </section>

        {/* 5 · Navy CTA band */}
        <CtaBand id="make-it-yours" title={`Make ${d.country} yours`} planHref={planHref} cvHeight={420} />

        {/* 6 · Photo ribbon from the tours' photos here (< 4 photos: nothing; the old static grid is gone). */}
        <PhotoRibbon
          id="around"
          cvHeight={440}
          title={`Around ${d.country}`}
          label={`Photos from our tours in ${d.country}`}
          items={d.photos.map((p) => ({
            href: null,
            src: p.url_small ?? p.url,
            alt: p.place_label ? `${p.place_label}, ${d.country}` : d.country,
            caption: p.place_label || d.country,
          }))}
        />
      </main>

      <SiteFooter />
      <RevealObserver />
    </div>
  );
}
