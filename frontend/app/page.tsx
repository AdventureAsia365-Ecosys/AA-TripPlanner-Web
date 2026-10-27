// AA-812 — search-first home (Nghiệp, S236: trip.com-style search leading into the map planner).
// Server Component: featured tours and destinations come from the CIS catalog through the browse
// Lambda (ISR, same TTL as the Lambda's CDN header). Each section has its own empty/error state, so
// a failed read never blanks the page.
// AA-995 "Golden Hour" (spec §4.1): photo hero under a transparent header → search card → ways in (cream)
// → featured tours (white) → proof line (cream) → destinations (sand) → how it works (white) → navy CTA
// band → photo ribbon (cream) → navy footer. No two adjacent bands share a surface. Same two catalog reads
// as before (featured limit 6 → 9). Reveals only on sections after the first screen.
import type { Metadata } from "next";
import Link from "next/link";
import CtaBand from "@/components/site/CtaBand";
import DestinationTile from "@/components/site/DestinationTile";
import HomeHero from "@/components/site/HomeHero";
import HomeSearch from "@/components/site/HomeSearch";
import JsonLd from "@/components/site/JsonLd";
import { noPhotoTone } from "@/components/site/NoPhotoTile";
import PhotoRibbon from "@/components/site/PhotoRibbon";
import RevealObserver from "@/components/site/RevealObserver";
import SectionTitle from "@/components/site/SectionTitle";
import SiteFooter from "@/components/site/SiteFooter";
import SiteHeader from "@/components/site/SiteHeader";
import TourCard from "@/components/site/TourCard";
import TourCarousel from "@/components/site/TourCarousel";
import { getCountries, getTours, siteUrl } from "@/lib/catalog";
import type { CountrySummary, TourCard as TourCardData } from "@/lib/catalogTypes";
import { firstUseOfCover, gridWithPhotos, type RibbonItem } from "@/lib/photos";
import { coverAlt } from "@/lib/text";
import { EmptyState, ErrorState } from "@/ui";

// Route segment config must be a literal (Next 15 reads it statically and fails the build on an
// imported constant). Keep it equal to CATALOG_REVALIDATE_S (lib/segmentConfig.test.ts checks).
export const revalidate = 1800;

// The layout's title/description apply; the home only adds its canonical URL.
export const metadata: Metadata = { alternates: { canonical: "/" } };

const WAYS_IN = [
  { icon: "/brand/icons/direction-color.svg", title: "Find by destination", body: "Pick a country and see its tours.", href: "#destinations" },
  { icon: "/brand/icons/map-color.svg", title: "Explore on the map", body: "Pin places and build the days yourself.", href: "/plan" },
  { icon: "/brand/icons/plan-color.svg", title: "Start from a tour", body: "Take a real route and make it yours.", href: "#tours" },
];

const STEPS = [
  {
    icon: "/brand/icons/plan-color.svg",
    title: "Start from a real tour",
    body: "Pick a destination and length. We propose routes built only from Adventure Asia's own tours.",
  },
  {
    icon: "/brand/icons/map-color.svg",
    title: "Make it yours on the map",
    body: "Add a night, a free day or an activity at each stop, and watch the trip take shape day by day.",
  },
  {
    icon: "/brand/icons/direction-color.svg",
    title: "An advisor tailors it",
    body: "Send the draft to an Adventure Asia advisor, who checks every detail and builds your trip.",
  },
];

// Ribbon content: featured tour covers, then country covers; uniqueRibbonItems dedupes by photo.
function ribbonItems(tours: TourCardData[], countries: CountrySummary[]): RibbonItem[] {
  return [
    ...tours.map((t) => ({
      href: `/tours/${t.slug}`,
      src: t.cover_url_small ?? t.cover_url,
      alt: coverAlt(t),
      caption: [t.cover_source && t.cover_source !== "tour" ? t.cover_place : null, t.country].filter(Boolean).join(" · ") || t.title,
    })),
    ...countries.map((c) => ({
      href: `/destinations/${c.slug}`,
      src: c.cover_url_small ?? c.cover_url,
      alt: c.country,
      caption: c.country,
    })),
  ];
}

export default async function HomePage() {
  const [countriesRes, toursRes] = await Promise.all([getCountries(), getTours({ sort: "featured", limit: 9 })]);
  const countries = countriesRes.ok ? countriesRes.data.countries : [];
  const tours = toursRes.ok ? toursRes.data.tours : [];
  const tourPhoto = firstUseOfCover(tours, (t) => t.cover_url_small ?? t.cover_url);
  const { items: grid, shown: gridPhoto } = gridWithPhotos(countries, (c) => c.cover_url_small ?? c.cover_url);
  const tourTotal = countries.reduce((n, c) => n + c.tour_count, 0);

  // No-photo tiles alternate navy / gold tint within each list, counted among the no-photo tiles only.
  const tones = (shown: boolean[]) => {
    let n = 0;
    return shown.map((on) => (on ? "band" : noPhotoTone(n++)));
  };
  const tourTone = tones(tourPhoto);
  const gridTone = tones(gridPhoto);

  return (
    <div className="aa-site">
      <JsonLd
        data={{
          "@context": "https://schema.org",
          "@type": "WebSite",
          name: "Adventure Asia Trip Planner",
          url: siteUrl(),
          publisher: { "@type": "Organization", name: "Adventure Asia", url: "https://adventure.asia/" },
        }}
      />
      <SiteHeader overHero />

      <main id="main">
        {/* 2 · Hero (LCP: the static photo). No entrance animation on anything in the first screen. */}
        <HomeHero>
          {/* Bottom padding = the search card overlap (48 / 56 px) + 32 px of air. */}
          <div className="mx-auto max-w-page px-gutter pb-20 md:pb-[88px]">
            <div className="max-w-[640px]">
              <p className="font-script text-script-sm text-k-band-accent">Asia, the way few see it</p>
              <h1 className="mt-2 text-display text-white">Plan your Asia adventure, tailor-made with an advisor</h1>
              <p className="mt-4 hidden text-lead text-white md:block">
                Start from a real Adventure Asia route, shape it on the map, and an advisor makes it yours.
              </p>
            </div>
          </div>
        </HomeHero>

        {/* 3 · Search card overlapping the hero, 4 · ways in (cream) */}
        <section aria-label="Plan a trip" className="relative z-10 bg-k-bg pb-section">
          <div className="mx-auto -mt-12 max-w-[1104px] px-gutter md:-mt-14">
            <HomeSearch countries={countries.map((c) => ({ country: c.country, tour_count: c.tour_count }))} />
            {!countriesRes.ok && (
              <p className="mt-2 text-caption font-normal text-k-muted">
                Destinations could not be loaded just now. You can still open the planner.
              </p>
            )}
          </div>
          <ul className="mx-auto mt-10 grid max-w-page grid-cols-3 gap-3 px-gutter md:mt-14 md:gap-6" aria-label="Ways to start">
            {WAYS_IN.map((w) => (
              <li key={w.title}>
                <Link
                  href={w.href}
                  prefetch={false}
                  className="group flex h-full flex-col items-center gap-2 rounded-card p-2 text-center md:flex-row md:items-start md:gap-4 md:p-4 md:text-left"
                >
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={w.icon} alt="" width={48} height={48} className="h-8 w-8 shrink-0 md:h-12 md:w-12" />
                  <span>
                    <span className="block text-caption font-semibold text-k-ink md:text-body md:font-semibold">
                      <span className="tp-link">{w.title}</span>
                    </span>
                    <span className="mt-1 hidden text-small text-k-muted md:block">{w.body}</span>
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </section>

        {/* 5 · Featured tours (white) */}
        <section id="tours" aria-labelledby="tours-title" className="scroll-mt-header bg-k-card py-section">
          <div className="mx-auto max-w-page px-gutter">
            {!toursRes.ok ? (
              <>
                <SectionTitle id="tours-title" title="Trips to start from" />
                <div className="mt-8">
                  <ErrorState title="We couldn't load tours right now">Please try again in a moment.</ErrorState>
                </div>
              </>
            ) : tours.length === 0 ? (
              <>
                <SectionTitle id="tours-title" title="Trips to start from" />
                <div className="mt-8">
                  <EmptyState title="No tours to show yet">
                    New Adventure Asia tours appear here as soon as they are published.
                  </EmptyState>
                </div>
              </>
            ) : (
              <TourCarousel
                id="featured-tours"
                label="Featured tours"
                titleId="tours-title"
                title="Trips to start from"
                lead="Real Adventure Asia routes. Open one and change it day by day."
                action={
                  <Link href="/plan" className="text-small font-semibold text-k-accent-deep">
                    <span className="tp-link">See all on the map</span> <span aria-hidden className="tp-link-arrow">→</span>
                  </Link>
                }
              >
                {tours.map((t, i) => (
                  <li key={t.source_id} className="min-w-0">
                    <TourCard
                      tour={t}
                      eager={i < 3}
                      showPhoto={tourPhoto[i]}
                      noPhotoTone={tourTone[i]}
                    />
                  </li>
                ))}
              </TourCarousel>
            )}
          </div>
        </section>

        {/* 6 · Proof line (cream, compact). Counts from getCountries(); no count-up animation. */}
        {countries.length > 0 && (
          <section aria-label="Adventure Asia in numbers" className="bg-k-bg py-8">
            <p className="mx-auto max-w-page px-gutter text-center text-lead font-semibold text-k-accent-deep">
              {tourTotal} tours · {countries.length} countries · every trip checked by an advisor before you book
            </p>
          </section>
        )}

        {/* 7 · Destinations (sand) */}
        <section id="destinations" aria-labelledby="destinations-title" className="scroll-mt-header bg-k-sand py-section">
          <div className="mx-auto max-w-page px-gutter">
            <div data-reveal="">
              <SectionTitle id="destinations-title" title="Where to go" lead="Countries with Adventure Asia tours you can plan from today." />
            </div>
            <div className="mt-10">
              {!countriesRes.ok ? (
                <ErrorState title="We couldn't load destinations right now">Please try again in a moment.</ErrorState>
              ) : countries.length === 0 ? (
                <EmptyState title="No destinations yet" />
              ) : (
                <ul className="grid grid-cols-2 gap-3 md:grid-cols-3 md:gap-5 lg:grid-cols-4" data-reveal="" data-reveal-group="">
                  {grid.map((c, i) => (
                    <li key={c.slug}>
                      <DestinationTile c={c} showPhoto={gridPhoto[i]} tone={gridTone[i]} />
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        </section>

        {/* 8 · How it works (white) */}
        <section id="how-it-works" aria-labelledby="how-title" className="scroll-mt-header bg-k-card py-section">
          <div className="mx-auto max-w-page px-gutter">
            <div data-reveal="">
              <SectionTitle id="how-title" title="How it works" />
            </div>
            <div className="relative mt-10">
            {/* A static dotted gold line behind the three steps (desktop). */}
            <span aria-hidden className="absolute left-[16%] right-[16%] top-6 hidden border-t-2 border-dotted border-k-accent-border md:block" />
            <ol className="relative grid gap-10 md:grid-cols-3 md:gap-8" data-reveal="" data-reveal-group="">
              {STEPS.map((s, i) => (
                <li key={s.title} className="relative flex flex-col items-center text-center">
                  <span className="flex h-12 items-center gap-3 bg-k-card px-3">
                    <span aria-hidden className="font-script text-script leading-none text-k-accent-deep">{i + 1}</span>
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={s.icon} alt="" width={48} height={48} className="h-12 w-12" />
                  </span>
                  <h3 className="mt-4 text-h3 text-k-ink">
                    <span className="sr-only">Step {i + 1}: </span>
                    {s.title}
                  </h3>
                  <p className="mt-2 max-w-[34ch] text-small text-k-muted">{s.body}</p>
                </li>
              ))}
            </ol>
            </div>
          </div>
        </section>

        {/* 9 · Navy CTA band */}
        <CtaBand />

        {/* 10 · Photo ribbon (cream, full-bleed): featured tour covers + country covers, deduped */}
        <PhotoRibbon id="from-our-trips" title="From our trips" label="Photos from our trips" items={ribbonItems(tours, countries)} />
      </main>

      <SiteFooter />
      <RevealObserver />
    </div>
  );
}
