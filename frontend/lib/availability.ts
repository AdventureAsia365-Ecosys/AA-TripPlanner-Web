// AA-986 L6 (root ADR 0015 decision 5): the words a traveller sees for a saved stop AA no longer serves.
// ADR 0015: a retired tour is "no longer offered"; a paused one is "temporarily unavailable" (never "gone").
import type { UnavailableReason, UnavailableStop } from "./types";

const REASON_TEXT: Record<UnavailableReason, string> = {
  retired: "No longer offered",
  paused: "Temporarily unavailable",
  stop_removed: "No longer part of this tour",
  not_served: "Not available right now",
};

export function reasonText(reason: UnavailableReason | string | null | undefined): string {
  return REASON_TEXT[reason as UnavailableReason] ?? REASON_TEXT.not_served;
}

// The banner over a saved trip with stops AA no longer serves. "Gone" wording only when every stop is retired.
export function unavailableBanner(stops: Pick<UnavailableStop, "reason">[]): string | null {
  if (stops.length === 0) return null;
  const n = stops.length;
  const what = n === 1 ? "One stop in this trip is" : `${n} stops in this trip are`;
  const allRetired = stops.every((s) => s.reason === "retired");
  return allRetired
    ? `${what} no longer offered by Adventure Asia. Remove ${n === 1 ? "it" : "them"} or ask an advisor for an alternative.`
    : `${what} not available right now. You can keep ${n === 1 ? "it" : "them"} for later, remove ${
        n === 1 ? "it" : "them"
      }, or ask an advisor.`;
}

// The label of one greyed stop: its saved name, else its place, else a neutral word.
export function stopLabel(stop: Pick<UnavailableStop, "name" | "destination_name">): string {
  return stop.name || stop.destination_name || "A saved stop";
}
