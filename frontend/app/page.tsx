// AA-812 — search-first home (Nghiệp, S236: trip.com-style search leading into the map planner).
// Server Component: featured tours and destinations come from the CIS catalog through the browse
// Lambda (ISR, same TTL as the Lambda's CDN header). Each section has its own empty/error state, so
// a failed read never blanks the page.
import type { Metadata } from "next";
import Link from "next/link";
import HomeSearch from "@/components/site/HomeSearch";
import SiteFooter from "@/components/site/SiteFooter";
import SiteHeader from "@/components/site/SiteHeader";
import TourCard from "@/components/site/TourCard";
import JsonLd from "@/components/site/JsonLd";
import { getCountries, getTours, siteUrl } from "@/lib/catalog";
import { EmptyState, ErrorState, Icon, Photo } from "@/ui";
import { formatDayRange } from "@/lib/text";

// Route segment config must be a literal (Next 15 reads it statically and fails the build on an
// imported constant). Keep it equal to CATALOG_REVALIDATE_S (lib/segmentConfig.test.ts checks).
export const revalidate = 1800;

// The layout's title/description apply; the home only adds its canonical URL.
export const metadata: Metadata = { alternates: { canonical: "/" } };

const STEPS = [
  {
    icon: "compass",
    title: "Start from a real tour",
    body: "Pick a destination and length — we propose routes built only from Adventure Asia's own tours.",
  },
  {
    icon: "map",
    title: "Make it yours on the map",
    body: "Add a night, a free day or an activity at each stop, and see the trip take shape day by day.",
  },
  {
    icon: "chat",
    title: "An advisor tailors it",
    body: "Send the draft to an Adventure Asia advisor, who checks every detail and builds your tailor-made trip.",
  },
];

export default async function HomePage() {
  const [countriesRes, toursRes] = await Promise.all([getCountries(), getTours({ sort: "featured", limit: 6 })]);
  const countries = countriesRes.ok ? countriesRes.data.countries : [];

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
      <SiteHeader />

      <main>
        {/* Hero + search */}
        <section className="relative overflow-hidden border-b border-k-line">
          <div aria-hidden className="absolute inset-0 bg-gradient-to-b from-k-accent-tint to-k-bg" />
          <div className="relative mx-auto max-w-6xl px-4 pb-10 pt-10 sm:pb-14 sm:pt-16">
            <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-k-accent-deep">
              Adventure Asia Trip Planner
            </p>
            <h1 className="mt-2 max-w-2xl text-[30px] font-semibold leading-tight sm:text-[44px]">
              Plan your Asia adventure, tailor-made with an advisor
            </h1>
            <p className="mt-3 max-w-xl text-[15px] leading-relaxed text-k-muted">
              Tell us where and for how long. We start you from real Adventure Asia routes — then you shape the
              trip on the map and an advisor makes it yours.
            </p>
            <div className="mt-6">
              <HomeSearch countries={countries.map((c) => ({ country: c.country, tour_count: c.tour_count }))} />
            </div>
            {!countriesRes.ok && (
              <p className="mt-2 text-[12px] text-k-muted">
                Destinations could not be loaded just now — you can still open the planner.
              </p>
            )}
          </div>
        </section>

        {/* Featured tours */}
        <section id="tours" className="mx-auto max-w-6xl scroll-mt-20 px-4 pt-12">
          <div className="flex items-end justify-between gap-3">
            <div>
              <h2 className="text-[24px] font-semibold sm:text-[28px]">Featured tours</h2>
              <p className="mt-1 text-[13px] text-k-muted">Start from one of these and customise it day by day.</p>
            </div>
            <Link href="/plan" className="hidden shrink-0 text-[13px] font-semibold text-k-accent-deep hover:underline sm:inline">
              Browse all on the map →
            </Link>
          </div>
          <div className="mt-5">
            {!toursRes.ok ? (
              <ErrorState title="We couldn't load tours right now">Please try again in a moment.</ErrorState>
            ) : toursRes.data.tours.length === 0 ? (
              <EmptyState title="No tours to show yet">
                New Adventure Asia tours appear here as soon as they are published.
              </EmptyState>
            ) : (
              <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {toursRes.data.tours.map((t, i) => (
                  <li key={t.source_id}>
                    <TourCard tour={t} eager={i < 3} />
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>

        {/* Destinations */}
        <section id="destinations" className="mx-auto max-w-6xl scroll-mt-20 px-4 pt-14">
          <h2 className="text-[24px] font-semibold sm:text-[28px]">Destinations</h2>
          <p className="mt-1 text-[13px] text-k-muted">Countries with Adventure Asia tours you can plan from today.</p>
          <div className="mt-5">
            {!countriesRes.ok ? (
              <ErrorState title="We couldn't load destinations right now">Please try again in a moment.</ErrorState>
            ) : countries.length === 0 ? (
              <EmptyState title="No destinations yet" />
            ) : (
              <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
                {countries.map((c) => (
                  <li key={c.slug}>
                    {/* AA-833: no prefetch. Every card in view would render its destination page at
                        once after a deploy or ISR expiry and throttle the browse Lambda. */}
                    <Link
                      href={`/destinations/${c.slug}`}
                      prefetch={false}
                      className="group relative block aspect-[4/5] overflow-hidden rounded-2xl border border-k-line"
                    >
                      <Photo src={c.cover_url_small ?? c.cover_url} alt={c.country}
                             className="h-full w-full transition duration-300 group-hover:scale-[1.04]" />
                      <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/70 to-transparent p-3 pt-10">
                        <p className="font-display text-[18px] font-semibold text-white">{c.country}</p>
                        <p className="text-[12px] text-white/85">
                          {c.tour_count} tour{c.tour_count > 1 ? "s" : ""}
                          {c.min_days && c.max_days ? ` · ${formatDayRange(c.min_days, c.max_days, "–")}` : ""}
                        </p>
                      </div>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>

        {/* How it works */}
        <section className="mx-auto max-w-6xl px-4 pt-14" aria-labelledby="how">
          <h2 id="how" className="text-[24px] font-semibold sm:text-[28px]">How it works</h2>
          <ol className="mt-5 grid gap-3 sm:grid-cols-3">
            {STEPS.map((s, i) => (
              <li key={s.title} className="rounded-2xl border border-k-line bg-k-card p-5">
                <div className="flex items-center gap-2">
                  <span className="inline-flex h-7 w-7 items-center justify-center rounded-full bg-k-accent-tint text-[13px] font-bold text-k-accent-deep">
                    {i + 1}
                  </span>
                  <Icon name={s.icon} className="h-5 w-5 text-k-accent" />
                </div>
                <p className="mt-3 text-[15px] font-semibold text-k-ink">{s.title}</p>
                <p className="mt-1 text-[13px] leading-relaxed text-k-muted">{s.body}</p>
              </li>
            ))}
          </ol>
        </section>
      </main>

      <SiteFooter />
    </div>
  );
}
