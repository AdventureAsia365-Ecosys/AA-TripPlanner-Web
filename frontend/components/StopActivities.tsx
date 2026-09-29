"use client";

// AA-674 / Jira PR-11 point 2: at each stop the traveller selects or deselects activities, taken
// from what real Adventure Asia tour days do there. Selecting pins that activity's component;
// deselecting removes it. Both are trip events, so they survive a reload and reach the advisor.
// Anything no AA tour offers at this stop is simply not listed.

import { useEffect, useState } from "react";
import { useTrip } from "@/lib/useTrip";
import { fetchStopActivities } from "@/lib/api";
import type { StopActivity } from "@/lib/types";

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

  if (items === null) return <p className="mt-2 text-[11px] text-aa-muted">Loading activities…</p>;
  if (items.length === 0) {
    return <p className="mt-2 text-[11px] text-aa-muted">No other AA activities here.</p>;
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
    <ul className="mt-2 space-y-1" aria-label="Activities at this stop">
      {items.map((a) => (
        <li key={`${a.name}-${a.activity}`}>
          <label className="flex cursor-pointer items-start gap-2 text-[11px] text-aa-ink-soft">
            <input
              type="checkbox"
              checked={a.selected}
              disabled={busy !== null}
              onChange={() => toggle(a)}
              className="mt-0.5 accent-[#B87A1A]"
            />
            <span>
              <span className="font-medium text-aa-ink">{LABEL[a.activity] ?? a.activity}</span>
              {a.text_extract && <span> — {a.text_extract.slice(0, 90)}</span>}
              <span className="text-aa-muted"> · on {a.tour_count} AA tour{a.tour_count > 1 ? "s" : ""}</span>
            </span>
          </label>
        </li>
      ))}
    </ul>
  );
}
