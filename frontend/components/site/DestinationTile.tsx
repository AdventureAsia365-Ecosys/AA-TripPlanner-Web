// AA-995 (spec §4.1 row 7 + immersive graft: UPPERCASE place names) — a country in the home grid: 4:5 photo,
// the label on a bottom scrim that is >= --tp-scrim-text under the text, M3 lift on hover. Without a photo
// (or with one already shown), the no-photo tile carries the name.
import Link from "next/link";
import type { CountrySummary } from "@/lib/catalogTypes";
import { formatDayRange } from "@/lib/text";
import LcpImage from "./LcpImage";
import NoPhotoTile, { type NoPhotoTone } from "./NoPhotoTile";

export function destinationMeta(c: Pick<CountrySummary, "tour_count" | "min_days" | "max_days">): string {
  const tours = `${c.tour_count} tour${c.tour_count === 1 ? "" : "s"}`;
  return c.min_days && c.max_days ? `${tours} · ${formatDayRange(c.min_days, c.max_days, "–")}` : tours;
}

// `photo`: the cover already resolved on the server (same-origin CDN file, lib/photoResolve.resolvePhotoMap; S257: every
// tile of /destinations), used instead of the catalog URL; null / absent keeps the catalog URL. `above`: the tile is in
// the first row of /destinations (S256, mobile LCP), loaded eagerly; `lcp` marks the one LCP tile (fetchpriority=high).
// Every other tile stays lazy.
export default function DestinationTile({
  c,
  showPhoto = true,
  tone = "band",
  photo,
  above,
}: {
  c: CountrySummary;
  showPhoto?: boolean;
  tone?: NoPhotoTone;
  photo?: string | null;
  above?: { lcp: boolean };
}) {
  const src = showPhoto ? (photo || (c.cover_url_small ?? c.cover_url)) : null;
  const meta = destinationMeta(c);
  // AA-833: no prefetch. Every tile in view would render its destination page at once after a deploy or
  // ISR expiry and throttle the browse Lambda.
  return (
    <Link
      href={`/destinations/${c.slug}`}
      prefetch={false}
      aria-label={`${c.country}, ${meta}`}
      className="tp-lift group relative block aspect-[4/5] overflow-hidden rounded-card border border-k-line bg-k-hero-bg shadow-e1"
    >
      {src ? (
        <>
          {above ? (
            <LcpImage src={src} alt="" width={480} height={600} loading="eager" decoding="async"
                      fetchPriority={above.lcp ? "high" : undefined} className="tp-lift-img h-full w-full object-cover" />
          ) : (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={src} alt="" width={480} height={600} loading="lazy" decoding="async"
                 className="tp-lift-img h-full w-full object-cover" />
          )}
          <div className="tp-tile-scrim absolute inset-x-0 bottom-0 p-4 text-white">
            <p className="text-h3 uppercase tracking-[0.08em]">{c.country}</p>
            <p className="mt-1 text-caption">{meta}</p>
          </div>
        </>
      ) : (
        <NoPhotoTile name={c.country} caption={meta} tone={tone} decorative className="h-full w-full" />
      )}
    </Link>
  );
}
