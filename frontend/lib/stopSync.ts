// AA-1041 (R7 slice 1, items 1 and 3) — map / itinerary sync and the photo of a stop and of a day. DOM-free.
//
// Sync: one "active stop" for the planner. Hovering or focusing a stop in the trip panel, or its marker on the map,
// highlights it on both sides; a click / Enter "selects" it: the map eases to it (selected from the list) or the panel
// scrolls to it (selected on the map). The hover wins while it lasts, then the selection shows again.
//
// Markers: the map draws one marker per place visit, as before (lib/lines.ts planTrip: consecutive stops at the same
// point are one marker, a river / road / trail day is a line, never a marker). A marker is active when any of its
// stops is.

import { isLine } from "./lines.ts";

export type SyncSource = "map" | "list";

export interface StopSelection {
  componentId: string;
  from: SyncSource;
  // Bumped on every select, so selecting the same stop again re-runs the camera / scroll.
  seq: number;
}

/** The stop to highlight: the hovered one, else the selected one. */
export function activeStop(hovered: string | null, selected: StopSelection | null): string | null {
  return hovered ?? selected?.componentId ?? null;
}

export interface MarkerStop {
  key: string;
  componentIds: string[];
  day: number;
  name: string | null;
  coord: [number, number];
}

interface MarkerItem {
  component_id: string;
  day: number;
  name: string | null;
  lat?: number;
  lng?: number;
  precision?: string | null;
}

/** One marker per place visit (same grouping as planTrip's stops), each with every stop it stands for. */
export function markerStops(itinerary: readonly MarkerItem[]): MarkerStop[] {
  const out: MarkerStop[] = [];
  for (const d of itinerary) {
    if (isLine(d) || typeof d.lat !== "number" || typeof d.lng !== "number") continue;
    const coord: [number, number] = [d.lng, d.lat];
    const last = out[out.length - 1];
    if (last && last.coord[0] === coord[0] && last.coord[1] === coord[1]) {
      last.componentIds.push(d.component_id);
      continue;
    }
    out.push({ key: d.component_id, componentIds: [d.component_id], day: d.day, name: d.name, coord });
  }
  return out;
}

/** The marker that stands for a stop (a line day has none). */
export function markerOf(markers: readonly MarkerStop[], componentId: string | null): MarkerStop | null {
  if (!componentId) return null;
  return markers.find((m) => m.componentIds.includes(componentId)) ?? null;
}

/** The marker's accessible name: "Day 2: Old town walk" (+ "and 1 more" when it stands for several stops). */
export function markerLabel(m: MarkerStop): string {
  const more = m.componentIds.length > 1 ? ` and ${m.componentIds.length - 1} more` : "";
  return `Day ${m.day}: ${m.name ?? "Stop"}${more}`;
}

// --- photos (item 1) -------------------------------------------------------------------------------------------

export interface StopInfo {
  id: string;
  photo_url: string | null;
  photo_url_small: string | null;
  activity?: string | null;
  destination_name?: string | null;
}

/** The photo of a stop (the place's CIS cover photo, small size for the panel), or null. */
export function stopPhoto(info: ReadonlyMap<string, StopInfo>, componentId: string): string | null {
  return info.get(componentId)?.photo_url_small ?? null;
}

/** The photo of a day: the first of its stops that has one, else null (the day shows its icon tile). */
export function dayPhoto(info: ReadonlyMap<string, StopInfo>, componentIds: readonly string[]): string | null {
  for (const id of componentIds) {
    const p = stopPhoto(info, id);
    if (p) return p;
  }
  return null;
}

/** The id set to ask /browse/stops for: every stop of the trip, sorted (one cache key per set). */
export function photoKey(itinerary: readonly { component_id: string }[]): string {
  return [...new Set(itinerary.map((d) => d.component_id))].sort().join(",");
}
