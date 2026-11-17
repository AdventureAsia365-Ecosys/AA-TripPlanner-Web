"use client";

// AA-674 / Jira PR-11 point 2: at each stop the traveller selects or deselects activities, taken
// from what real Adventure Asia tour days do there. Selecting pins that activity's component;
// deselecting removes it. Both are trip events, so they survive a reload and reach the advisor.
// Anything no AA tour offers at this stop is simply not listed.

import { useEffect, useState } from "react";
import { useTrip } from "@/lib/useTrip";
import { fetchStopActivities } from "@/lib/api";
import type { StopActivity } from "@/lib/types";
import AiLabel from "@/components/AiLabel";
import { useBrand } from "@/components/BrandContext";
import { AiNotice } from "@/ui/AiNotice";

const LABEL: Record<string, string> = {
  trekking: "Trekking",
  cultural_heritage: "Culture & heritage",
  wildlife_nature: "Wildlife & nature",
  water_activities: "On the water",
  culinary: "Food",
  wellness_relaxation: "Wellness",
  adventure_sport: "Adventure sport",
  local_immersion: "Local life",
};

export default function StopActivities({ destinationId }: { destinationId: string }) {
  const { tripId, itinerary, add, remove } = useTrip();
  const brand = useBrand(); // AA-1115: an agency's planner shows its own label and terms
  const [items, setItems] = useState<StopActivity[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const pinnedKey = itinerary.map((d) => d.component_id).sort().join(",");

  useEffect(() => {
    let active = true;
    fetchStopActivities(tripId, destinationId).then((a) => {
      if (active) setItems(a);
    });
    return () => {
      active = false;
    };
  }, [tripId, destinationId, pinnedKey]);

  if (items === null) return <p className="mt-2 text-[11px] text-k-muted">Loading activities…</p>;
  if (items.length === 0) {
    return <p className="mt-2 text-[11px] text-k-muted">{brand.copy.activities.none}</p>;
  }

  const toggle = async (a: StopActivity) => {
    setBusy(a.component_id);
    try {
      await (a.selected ? remove(a.component_id) : add(a.component_id));
    } finally {
      setBusy(null);
    }
  };

  return (
    <>
      <ul className="mt-2 space-y-1" aria-label="Activities at this stop">
        {items.map((a) => (
          <li key={`${a.name}-${a.activity}`}>
            <label className="flex cursor-pointer items-start gap-2 text-[11px] text-k-body">
              <input
                type="checkbox"
                checked={a.selected}
                disabled={busy !== null}
                onChange={() => toggle(a)}
                className="mt-0.5 accent-k-accent-deep"
              />
              <span>
                <span className="font-medium text-k-ink">{LABEL[a.activity] ?? a.activity}</span>
                {a.text_extract && <span> — {a.text_extract.slice(0, 90)}</span>}
                <span className="text-k-muted"> · {brand.copy.activities.onTours(a.tour_count)}</span>
              </span>
            </label>
          </li>
        ))}
      </ul>
      {/* AA-819: the activity snippets are the same CIS writer-model text as the map popup. */}
      {/* AA-1033: and the traveller notice (mistakes possible, the official quote confirms), as the planner has no
          site footer. Only one stop's activity list is open at a time, so it shows once. */}
      {items.some((a) => a.text_extract) && (
        <>
          <AiLabel
            className="mt-1.5"
            placement="inline"
            label={brand.copy.catalogAi.label} shortLabel={brand.copy.catalogAi.shortLabel}
            explanation={brand.copy.catalogAi.explanation}
          />
          <AiNotice size="sm" termsHref={brand.legal.termsAi} className="mt-1" testId="stop-activities-ai-notice" />
        </>
      )}
    </>
  );
}
