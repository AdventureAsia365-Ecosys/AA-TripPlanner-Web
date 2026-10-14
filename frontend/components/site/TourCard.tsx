import Link from "next/link";
import { Badge, Photo } from "@/ui";
import type { TourCard as TourCardData } from "@/lib/catalogTypes";
import { daysText } from "@/lib/text";

export default function TourCard({ tour, eager = false }: { tour: TourCardData; eager?: boolean }) {
  const days = daysText(tour.duration_days, tour.duration_raw);
  return (
    <Link
      href={`/tours/${tour.slug}`}
      className="group flex h-full flex-col overflow-hidden rounded-2xl border border-k-line bg-k-card transition hover:border-k-accent-border hover:shadow-aa-sm"
    >
      <div className="relative aspect-[4/3] overflow-hidden">
        <Photo src={tour.cover_url_small ?? tour.cover_url} alt={tour.title} eager={eager}
               className="h-full w-full transition duration-300 group-hover:scale-[1.03]" />
        {days && (
          <span className="absolute left-3 top-3 rounded-full bg-k-card px-2.5 py-1 text-[11px] font-semibold text-k-ink">
            {days}
          </span>
        )}
      </div>
      <div className="flex flex-1 flex-col p-4">
        {tour.country && <Badge tone="accent" className="self-start">{tour.country}</Badge>}
        <h3 className="mt-2 font-display text-[17px] font-semibold leading-snug text-k-ink">{tour.title}</h3>
        {(tour.subtitle || tour.summary) && (
          <p className="mt-1 line-clamp-2 text-[13px] leading-relaxed text-k-muted">{tour.subtitle || tour.summary}</p>
        )}
        <span className="mt-auto pt-3 text-[12px] font-semibold uppercase tracking-[0.06em] text-k-accent-deep">
          View itinerary →
        </span>
      </div>
    </Link>
  );
}
