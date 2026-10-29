// AA-812 — a tour in a grid or row (home, destination pages).
// AA-995 (spec §3.4) — Golden Hour card: 4:3 photo with the duration pill, the "Photo: {place}" caption when
// the cover is a destination photo (AA-812 D4/D5), country kicker, 2-line title and summary, a meta row,
// and "Explore trip →". The whole card is one link. `showPhoto={false}` (duplicate-cover rule,
// lib/photos.ts) or a missing cover puts the no-photo tile in the photo slot. `photo` (S257): the cover already resolved
// on the server (same-origin CDN file, lib/photoResolve.resolvePhotoMap), used instead of the catalog URL.
import Link from "next/link";
import { Icon } from "@/ui";
import type { TourCard as TourCardData } from "@/lib/catalogTypes";
import { coverAlt, daysText } from "@/lib/text";
import NoPhotoTile, { type NoPhotoTone } from "./NoPhotoTile";

export default function TourCard({
  tour,
  eager = false,
  showPhoto = true,
  noPhotoTone = "band",
  photo,
}: {
  tour: TourCardData;
  eager?: boolean;
  showPhoto?: boolean;
  noPhotoTone?: NoPhotoTone;
  photo?: string | null;
}) {
  const days = daysText(tour.duration_days, tour.duration_raw);
  const src = showPhoto ? (photo || (tour.cover_url_small ?? tour.cover_url)) : null;
  const fallbackCover = tour.cover_source && tour.cover_source !== "tour" && tour.cover_place;
  const highlights = tour.highlights?.length ?? 0;
  // AA-833: no prefetch. Cards come in grids (home, destination pages), and prefetching every card
  // in view renders all those tour pages at once after a deploy or ISR expiry, throttling the
  // browse Lambda. The page is fetched on click instead.
  return (
    <Link
      href={`/tours/${tour.slug}`}
      prefetch={false}
      className="tp-lift group flex h-full flex-col overflow-hidden rounded-card border border-k-line bg-k-card shadow-e1 hover:border-k-accent-border"
    >
      <div className="relative aspect-[4/3] overflow-hidden bg-k-hero-bg">
        {src ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={src}
            alt={coverAlt(tour)}
            width={600}
            height={450}
            loading={eager ? "eager" : "lazy"}
            decoding="async"
            className="tp-lift-img h-full w-full object-cover"
          />
        ) : (
          <NoPhotoTile name={tour.country ?? "Adventure Asia"} tone={noPhotoTone} decorative className="h-full w-full" />
        )}
        {days && (
          <span className="absolute left-3 top-3 rounded-pill bg-k-scrim px-2.5 py-1 text-caption text-white">{days}</span>
        )}
        {src && fallbackCover && (
          <span className="absolute bottom-3 left-3 max-w-[calc(100%-24px)] truncate rounded-pill bg-k-scrim px-2.5 py-1 text-caption text-white">
            Photo: {tour.cover_place}
          </span>
        )}
      </div>
      <div className="flex flex-1 flex-col p-5">
        {tour.country && <p className="text-kicker uppercase text-k-accent-deep">{tour.country}</p>}
        <h3 className="mt-2 line-clamp-2 text-h3 text-k-ink">{tour.title}</h3>
        {(tour.subtitle || tour.summary) && (
          <p className="mt-2 line-clamp-2 text-small text-k-muted">{tour.subtitle || tour.summary}</p>
        )}
        {(tour.best_time_to_go || highlights > 0) && (
          <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-caption text-k-muted">
            {tour.best_time_to_go && (
              <li className="inline-flex min-w-0 items-center gap-1.5">
                <Icon name="calendar" className="h-3.5 w-3.5 shrink-0 text-k-accent-deep" />
                <span className="truncate">Best {tour.best_time_to_go}</span>
              </li>
            )}
            {highlights > 0 && (
              <li className="inline-flex items-center gap-1.5">
                <Icon name="sparkle" className="h-3.5 w-3.5 shrink-0 text-k-accent-deep" />
                {highlights} highlight{highlights > 1 ? "s" : ""}
              </li>
            )}
          </ul>
        )}
        <span className="mt-auto flex items-center gap-1 border-t border-k-line pt-4 text-small font-semibold text-k-accent-deep">
          <span className="tp-link">Explore trip</span> <span aria-hidden className="tp-link-arrow">→</span>
        </span>
      </div>
    </Link>
  );
}
