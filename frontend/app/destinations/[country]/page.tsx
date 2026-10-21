// AA-812 — destination (country) page, SSR + ISR from the CIS catalog. The catalog has no editorial
// country text yet (contract catalog.tour v1), so the intro is written from facts only: tour count,
// trip lengths, the places and activities the tours really include.
import type { Metadata } from "next";
import Link from "next/link";
import { notFound, permanentRedirect } from "next/navigation";
import JsonLd from "@/components/site/JsonLd";
import SiteFooter from "@/components/site/SiteFooter";
import SiteHeader from "@/components/site/SiteHeader";
import StaticMap from "@/components/site/StaticMap";
import TourCard from "@/components/site/TourCard";
import { getDestination, siteUrl } from "@/lib/catalog";
import type { DestinationPage } from "@/lib/catalogTypes";
import { slugify, titleCase } from "@/lib/text";
import { ButtonLink, EmptyState, Icon, Photo } from "@/ui";

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
    d.min_days && d.max_days ? (d.min_days === d.max_days ? `${d.min_days} days` : `${d.min_days} to ${d.max_days} days`) : null;
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
  const points = listed.map((p) => ({
    name: p.name ?? "", lat: p.lat, lng: p.lng, located_by: p.located_by, precision: p.precision,
  }));

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
      <SiteHeader />

      <main>
        <section className="relative isolate overflow-hidden bg-k-hero-bg">
          {/* No cover: leave the solid hero-bg under the scrim. The Photo placeholder is a light tint,
              and white text over tint + scrim is only ~4.4:1 (below AA for the 12-15px lines). */}
          {d.cover_url && (
            <Photo src={d.cover_url} alt={d.country} eager className="absolute inset-0 -z-10 h-full w-full" />
          )}
          <div aria-hidden className="absolute inset-0 -z-10 bg-k-scrim" />
          <div className="mx-auto max-w-6xl px-4 pb-10 pt-14 sm:pb-14 sm:pt-24">
            <nav aria-label="Breadcrumb" className="text-[12px] text-white/80">
              <Link href="/" className="hover:text-white">Home</Link> / <Link href="/#destinations" className="hover:text-white">Destinations</Link>
            </nav>
            <h1 className="mt-2 text-[34px] font-semibold !text-white sm:text-[48px]">{d.country}</h1>
            <p className="mt-3 max-w-2xl text-[15px] leading-relaxed text-white/90">{intro(d)}</p>
            <div className="mt-5 flex flex-wrap gap-2">
              <ButtonLink href={planHref} variant="primary" size="lg">
                <Icon name="route" className="h-4 w-4" /> Plan a trip to {d.country}
              </ButtonLink>
            </div>
          </div>
        </section>

        <section className="mx-auto max-w-6xl px-4 pt-10" aria-labelledby="top-tours">
          <h2 id="top-tours" className="text-[24px] font-semibold">Tours in {d.country}</h2>
          {d.tours.length === 0 ? (
            <div className="mt-4"><EmptyState title={`No tours in ${d.country} yet`} /></div>
          ) : (
            <ul className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {d.tours.map((t, i) => (
                <li key={t.source_id}><TourCard tour={t} eager={i < 3} /></li>
              ))}
            </ul>
          )}
          {d.tour_count > d.tours.length && (
            <p className="mt-3 text-[13px] text-k-muted">
              Showing {d.tours.length} of {d.tour_count}.{" "}
              <Link href={planHref} className="font-semibold text-k-accent-deep hover:underline">See them all in the planner →</Link>
            </p>
          )}
        </section>

        <section className="mx-auto max-w-6xl px-4 pt-12" aria-labelledby="places">
          <h2 id="places" className="text-[24px] font-semibold">Places our tours visit</h2>
          {d.places.length === 0 ? (
            <div className="mt-4">
              <EmptyState title="Places are being mapped">The places on these tours are not on the map yet.</EmptyState>
            </div>
          ) : (
            <div className="mt-4 grid gap-5 lg:grid-cols-[1fr_1fr]">
              <StaticMap points={points} title={`Places in ${d.country}`} caption="Numbers match the list of places." />
              <ol className="grid content-start gap-2 sm:grid-cols-2">
                {listed.map((p, i) => (
                  <li key={p.destination_id} className="flex gap-3 rounded-xl border border-k-line bg-k-card p-2.5">
                    <Photo src={p.cover_url} alt={p.name ?? "Place"} className="h-14 w-14 shrink-0 rounded-lg" />
                    <div className="min-w-0">
                      <p className="truncate text-[14px] font-semibold text-k-ink">
                        <span className="mr-1 text-k-accent-deep">{i + 1}.</span>
                        {p.name}
                      </p>
                      <p className="text-[12px] text-k-muted">
                        {p.tour_count} tour{p.tour_count > 1 ? "s" : ""}
                        {p.activity_types.length ? ` · ${p.activity_types.slice(0, 2).map(titleCase).join(", ")}` : ""}
                      </p>
                    </div>
                  </li>
                ))}
              </ol>
            </div>
          )}
        </section>

        {d.photos.length > 0 && (
          <section className="mx-auto max-w-6xl px-4 pt-12" aria-labelledby="photos">
            <h2 id="photos" className="text-[24px] font-semibold">From our tours in {d.country}</h2>
            <ul className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
              {d.photos.map((p) => (
                <li key={p.photo_id}>
                  <Photo src={p.url_small ?? p.url} alt={p.place_label ? `${p.place_label}, ${d.country}` : d.country}
                         className="aspect-[4/3] w-full rounded-xl" />
                  {p.place_label && <p className="mt-1 truncate text-[11px] text-k-muted">{p.place_label}</p>}
                </li>
              ))}
            </ul>
          </section>
        )}
      </main>

      <SiteFooter />
    </div>
  );
}
