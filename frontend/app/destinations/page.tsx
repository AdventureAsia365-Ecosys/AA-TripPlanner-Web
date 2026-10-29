// AA-995 — /destinations: every country with Adventure Asia tours, as the home page's destination tiles (photo tiles
// first, then the designed no-photo tiles; lib/photos.gridWithPhotos). It was a 404 before, while the destination
// pages' breadcrumb said "Destinations". One catalog read, the same ISR-cached getCountries() the home page uses.
import type { Metadata } from "next";
import CtaBand from "@/components/site/CtaBand";
import DestinationTile from "@/components/site/DestinationTile";
import { noPhotoTone } from "@/components/site/NoPhotoTile";
import PageIntro from "@/components/site/PageIntro";
import RevealObserver from "@/components/site/RevealObserver";
import SiteFooter from "@/components/site/SiteFooter";
import SiteHeader from "@/components/site/SiteHeader";
import { getCountries } from "@/lib/catalog";
import { resolveTilePhoto } from "@/lib/photoResolve";
import { gridWithPhotos } from "@/lib/photos";
import { EmptyState, ErrorState } from "@/ui";

// Route segment config must be a literal; keep it equal to CATALOG_REVALIDATE_S (lib/segmentConfig.test.ts checks).
export const revalidate = 1800;

// S256 (mobile LCP): the first row at 390 px (2 tiles) is above the fold, and its first photo tile is the LCP. Their
// covers are resolved on the server to the same-origin CDN file and loaded eagerly; the rest stay lazy.
const ABOVE_FOLD_TILES = 2;

export const metadata: Metadata = {
  title: "Destinations",
  description: "Countries in Asia with Adventure Asia tours you can plan from today, tailor-made with an advisor.",
  alternates: { canonical: "/destinations" },
};

export default async function DestinationsPage() {
  const res = await getCountries();
  const countries = res.ok ? res.data.countries : [];
  const { items: grid, shown } = gridWithPhotos(countries, (c) => c.cover_url_small ?? c.cover_url);
  let n = 0;
  const tones = shown.map((on) => (on ? "band" : noPhotoTone(n++)));
  const firstPhoto = shown.indexOf(true);
  const abovePhotos = await Promise.all(
    grid.slice(0, ABOVE_FOLD_TILES).map((c, i) => (shown[i] ? resolveTilePhoto(c.cover_url_small ?? c.cover_url) : null)),
  );
  const tourTotal = countries.reduce((sum, c) => sum + c.tour_count, 0);

  return (
    <div className="aa-site">
      <SiteHeader />
      <main id="main">
        <PageIntro
          title="Destinations"
          kicker="Where to go"
          crumbs={[{ href: "/", label: "Home" }]}
          lead={
            countries.length > 0
              ? `${tourTotal} tours in ${countries.length} countries. Pick one to see its tours and the places they visit.`
              : "Countries with Adventure Asia tours you can plan from today."
          }
        />
        <section aria-label="Countries" className="bg-k-sand py-section">
          <div className="mx-auto max-w-page px-gutter">
            {!res.ok ? (
              <ErrorState title="We couldn't load destinations right now">Please try again in a moment.</ErrorState>
            ) : grid.length === 0 ? (
              <EmptyState title="No destinations yet">New destinations appear here as soon as their tours are published.</EmptyState>
            ) : (
              <ul className="grid grid-cols-2 gap-3 md:grid-cols-3 md:gap-5 lg:grid-cols-4" data-reveal="" data-reveal-group="">
                {grid.map((c, i) => (
                  <li key={c.slug}>
                    <DestinationTile
                      c={c}
                      showPhoto={shown[i]}
                      tone={tones[i]}
                      above={i < ABOVE_FOLD_TILES && shown[i] ? { photo: abovePhotos[i], lcp: i === firstPhoto } : undefined}
                    />
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>
        <CtaBand />
      </main>
      <SiteFooter />
      <RevealObserver />
    </div>
  );
}
