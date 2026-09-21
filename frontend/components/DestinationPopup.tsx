"use client";

import { useEffect, useState } from "react";
import { fetchDestination } from "@/lib/api";
import type { DestinationDetail } from "@/lib/types";
import { useTrip } from "@/lib/useTrip";
import ActivityIcon from "./ActivityIcon";

interface Props {
  destinationId: string;
  onClose: () => void;
}

function label(v: string): string {
  return v.replace(/_/g, " ");
}

export default function DestinationPopup({ destinationId, onClose }: Props) {
  const { add, remove, inTripComponentIds, itinerary } = useTrip();
  const [detail, setDetail] = useState<DestinationDetail | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    setLoading(true);
    fetchDestination(destinationId).then((d) => {
      if (active) {
        setDetail(d);
        setLoading(false);
      }
    });
    return () => {
      active = false;
    };
  }, [destinationId]);

  const dayOf = (componentId: string): number | null => {
    const found = itinerary.find((d) => d.component_id === componentId);
    return found ? found.day : null;
  };

  return (
    <div className="aa-animate-in absolute right-4 top-4 z-20 w-[22rem] overflow-hidden rounded-2xl border border-aa-line bg-white shadow-aa">
      {/* Cover image (from shared.destinations.cover_image_url). Only shown
          when content has supplied a URL; otherwise the header sits flush. */}
      {detail?.cover_image_url && (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={detail.cover_image_url}
          alt={detail.name}
          className="h-32 w-full object-cover"
          loading="lazy"
        />
      )}
      {/* Header */}
      <div className="flex items-start justify-between gap-3 border-b border-aa-line bg-aa-offwhite px-4 py-3">
        <div className="min-w-0">
          <h3 className="truncate text-base font-semibold text-aa-ink">
            {detail?.name ?? "Loading…"}
          </h3>
          {detail?.country && (
            <p className="mt-0.5 flex items-center gap-1 text-xs text-aa-muted">
              <span aria-hidden>📍</span>
              {detail.country}
            </p>
          )}
        </div>
        <button
          onClick={onClose}
          aria-label="Close"
          className="aa-focus -mr-1 rounded-lg p-1 text-aa-muted hover:bg-aa-line hover:text-aa-ink"
        >
          ✕
        </button>
      </div>

      <div className="px-4 py-3">
        {loading && (
          <p className="py-6 text-center text-sm text-aa-muted">Loading experiences…</p>
        )}

        {!loading && detail && detail.components.length === 0 && (
          <p className="py-6 text-center text-sm text-aa-muted">
            No experiences catalogued here yet.
          </p>
        )}

        {!loading && detail && detail.components.length > 0 && (
          <p className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-aa-muted">
            {detail.components.length} experience
            {detail.components.length > 1 ? "s" : ""} to pin
          </p>
        )}

        <ul className="max-h-[22rem] space-y-2.5 overflow-y-auto">
          {detail?.components.map((c) => {
            const inTrip = inTripComponentIds.has(c.id);
            const day = dayOf(c.id);
            return (
              <li
                key={c.id}
                className={`rounded-xl border p-3 transition ${
                  inTrip
                    ? "border-aa-gold/50 bg-aa-gold-soft"
                    : "border-aa-line bg-white hover:border-aa-gold/40"
                }`}
              >
                <div className="min-w-0">
                  <p className="text-sm font-semibold text-aa-ink">{c.name}</p>
                  <p className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[11px] capitalize text-aa-muted">
                    <span className="inline-flex items-center gap-1 rounded-full bg-aa-sand px-2 py-0.5">
                      <ActivityIcon activity={c.activity} size={13} className="text-aa-gold-dark" />
                      {label(c.activity)}
                    </span>
                    <span className="rounded-full bg-aa-sand px-2 py-0.5">
                      {label(c.intensity_level)}
                    </span>
                    <span className="rounded-full bg-aa-sand px-2 py-0.5">
                      {label(c.duration_hint)}
                    </span>
                  </p>
                </div>
                <p className="mt-1.5 text-xs leading-relaxed text-aa-ink-soft">
                  {c.text_extract}
                </p>
                {/* Add/Remove on its own full-width row so it's always
                    visible regardless of the component name length. */}
                <div className="mt-2 flex items-center justify-between gap-2">
                  {inTrip && day !== null ? (
                    <span className="flex items-center gap-1 text-[11px] font-semibold text-aa-gold-dark">
                      <span aria-hidden>✓</span> In your trip — Day {day}
                    </span>
                  ) : (
                    <span />
                  )}
                  {inTrip ? (
                    <button
                      onClick={() => remove(c.id)}
                      className="aa-focus rounded-lg border border-aa-line bg-white px-3 py-1.5 text-xs font-medium text-aa-muted hover:border-red-300 hover:text-red-600"
                    >
                      Remove
                    </button>
                  ) : (
                    <button
                      onClick={() => add(c.id)}
                      className="aa-focus rounded-lg bg-aa-gold px-4 py-1.5 text-xs font-semibold text-white shadow-aa-sm hover:bg-aa-gold-dark"
                    >
                      + Add to trip
                    </button>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}
