// AA-812 — map points for tour and destination pages.
import type { TourDetail } from "./catalogTypes";

export interface MapPoint {
  name: string;
  lat: number;
  lng: number;
  // AA-847 (catalog.tour v1.1): "area" points are drawn as approximate; "geoapify" needs the attribution
  located_by?: "geoapify" | "mapbox" | "llm" | "manual" | null;
  precision?: "point" | "area" | null;
}

// Places in itinerary order with coordinates, consecutive repeats collapsed (one pin per stop).
export function routePoints(t: TourDetail): MapPoint[] {
  const out: MapPoint[] = [];
  const ordered = [...t.places]
    .filter((p) => p.day_number !== null && p.lat !== null && p.lng !== null)
    .sort((a, b) => (a.day_number ?? 0) - (b.day_number ?? 0) || a.position - b.position);
  for (const p of ordered) {
    const name = p.destination_name || p.place_display_name || p.place_name;
    if (out.length && out[out.length - 1].name === name) continue;
    out.push({ name, lat: p.lat as number, lng: p.lng as number, located_by: p.located_by, precision: p.precision });
  }
  return out;
}
