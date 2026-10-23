"use client";

import { useEffect, useState } from "react";
import { fetchDestination } from "@/lib/api";
import type { DestinationDetail } from "@/lib/types";
import { useTrip } from "@/lib/useTrip";
import ActivityIcon from "./ActivityIcon";
import GeocodeAttribution from "./GeocodeAttribution";
import AiLabel, { AI_BRAND_SHORT, AI_CATALOG_EXPLANATION, AI_CATALOG_LABEL } from "./AiLabel";

interface Props {
  destinationId: string;
  onClose: () => void;
}

function label(v: string): string {
  return v.replace(/_/g, " ");
}

export default function DestinationPopup({ destinationId, onClose }: Props) {
  const { add, remove, inTripComponentIds, itinerary } = useTrip();
  // AA-906 (React Compiler lint, no setState in an effect body): the fetched detail is tagged with
  // the destination it belongs to, so "loading" is derived (no answer yet for this id) instead of set
  // from the effect. As before, the previous detail stays on screen until the new one lands.
  const [fetched, setFetched] = useState<{ id: string; detail: DestinationDetail | null } | null>(null);
  const detail = fetched?.detail ?? null;
  const loading = fetched?.id !== destinationId;

  useEffect(() => {
    let active = true;
    fetchDestination(destinationId).then((d) => {
      if (active) setFetched({ id: destinationId, detail: d });
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
    <div className="aa-animate-in absolute right-4 top-4 z-20 w-[22rem] overflow-hidden rounded-2xl border border-k-line bg-k-card shadow-aa">
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
      <div className="flex items-start justify-between gap-3 border-b border-k-line bg-k-bg px-4 py-3">
        <div className="min-w-0">
          <h3 className="truncate text-base font-semibold text-k-ink">
            {detail?.name ?? "Loading…"}
          </h3>
          {detail?.country && (
            <p className="mt-0.5 flex items-center gap-1 text-xs text-k-muted">
              <span aria-hidden>📍</span>
              {detail.country}
              {/* AA-847 (ADR 0007): the model's point, not a geocoded one */}
              {detail.precision === "area" && <span className="ml-1">· approximate area</span>}
            </p>
          )}
        </div>
        <button
          onClick={onClose}
          aria-label="Close"
          className="aa-focus -mr-1 rounded-lg p-1 text-k-muted hover:bg-k-line hover:text-k-ink"
        >
          ✕
        </button>
      </div>

      <div className="px-4 py-3">
        {loading && (
          <p className="py-6 text-center text-sm text-k-muted">Loading experiences…</p>
        )}

        {!loading && detail && detail.components.length === 0 && (
          <p className="py-6 text-center text-sm text-k-muted">
            No experiences catalogued here yet.
          </p>
        )}

        {!loading && detail && detail.components.length > 0 && (
          <div className="mb-2">
            <p className="text-[11px] font-semibold uppercase tracking-wide text-k-muted">
              {detail.components.length} experience
              {detail.components.length > 1 ? "s" : ""} to pin
            </p>
            {/* AA-819 (D4): the experience descriptions are CIS writer-model text. The
                popup clips overflow, so the explanation opens in the flow. */}
            {detail.components.some((c) => c.text_extract) && (
              <AiLabel
                className="mt-1"
                placement="inline"
                label={AI_CATALOG_LABEL} shortLabel={AI_BRAND_SHORT}
                explanation={AI_CATALOG_EXPLANATION}
              />
            )}
          </div>
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
                    ? "border-k-accent-border bg-k-accent-tint"
                    : "border-k-line bg-k-card hover:border-k-accent-border"
                }`}
              >
                <div className="min-w-0">
                  <p className="text-sm font-semibold text-k-ink">{c.name}</p>
                  <p className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[11px] capitalize text-k-muted">
                    <span className="inline-flex items-center gap-1 rounded-full bg-k-neutral-bg px-2 py-0.5">
                      <ActivityIcon activity={c.activity} size={13} className="text-k-accent-deep" />
                      {label(c.activity)}
                    </span>
                    <span className="rounded-full bg-k-neutral-bg px-2 py-0.5">
                      {label(c.intensity_level)}
                    </span>
                    <span className="rounded-full bg-k-neutral-bg px-2 py-0.5">
                      {label(c.duration_hint)}
                    </span>
                  </p>
                </div>
                <p className="mt-1.5 text-xs leading-relaxed text-k-body">
                  {c.text_extract}
                </p>
                {/* Add/Remove on its own full-width row so it's always
                    visible regardless of the component name length. */}
                <div className="mt-2 flex items-center justify-between gap-2">
                  {inTrip && day !== null ? (
                    <span className="flex items-center gap-1 text-[11px] font-semibold text-k-accent-deep">
                      <span aria-hidden>✓</span> In your trip — Day {day}
                    </span>
                  ) : (
                    <span />
                  )}
                  {inTrip ? (
                    <button
                      onClick={() => remove(c.id)}
                      className="aa-focus rounded-lg border border-k-line bg-k-card px-3 py-1.5 text-xs font-medium text-k-muted hover:border-k-danger-border hover:text-k-danger"
                    >
                      Remove
                    </button>
                  ) : (
                    <button
                      onClick={() => add(c.id)}
                      className="aa-focus rounded-lg bg-k-accent px-4 py-1.5 text-xs font-semibold text-k-on-accent shadow-aa-sm hover:brightness-95"
                    >
                      + Add to trip
                    </button>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
        {detail?.located_by === "geoapify" && <GeocodeAttribution className="mt-2 px-0" />}
      </div>
    </div>
  );
}
