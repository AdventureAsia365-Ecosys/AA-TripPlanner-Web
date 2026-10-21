// AA-812 — map points for tour and destination pages.
// AA-847 slice 6d: a line place (river, road, trail) is never a pin: routePoints leaves it out and routeLines gives its
// geometry (the tour-day's stretch, else the line's shape_lo) for the static map's path overlays.
import type { CountryPlace, TourDetail } from "./catalogTypes";
import { lineParts, type Coord } from "./lines.ts";

export interface MapPoint {
  name: string;
  lat: number;
  lng: number;
  // AA-847 (catalog.tour v1.1): "area" points are drawn as approximate; "geoapify" needs the attribution
  located_by?: "geoapify" | "mapbox" | "llm" | "manual" | null;
  precision?: "point" | "area" | "line" | null;
}

export interface MapLine {
  name: string | null;
  coords: Coord[];
}

// Places in itinerary order with coordinates, consecutive repeats collapsed (one pin per stop).
export function routePoints(t: TourDetail): MapPoint[] {
  const out: MapPoint[] = [];
  const ordered = [...t.places]
    .filter((p) => p.day_number !== null && p.lat !== null && p.lng !== null && p.precision !== "line")
    .sort((a, b) => (a.day_number ?? 0) - (b.day_number ?? 0) || a.position - b.position);
  for (const p of ordered) {
    const name = p.destination_name || p.place_display_name || p.place_name;
    if (out.length && out[out.length - 1].name === name) continue;
    out.push({ name, lat: p.lat as number, lng: p.lng as number, located_by: p.located_by, precision: p.precision });
  }
  return out;
}

// Lines of a tour in itinerary order, one per line place: the stretch its day follows, else the line's shape.
export function routeLines(t: TourDetail): MapLine[] {
  const out: MapLine[] = [];
  const seen = new Set<string>();
  for (const p of t.places) {
    if (p.precision !== "line") continue;
    const coords = lineParts(p.segment as never)[0] ?? lineParts(p.shape_lo as never)[0];
    const key = p.destination_id ?? p.place_name;
    if (!coords || seen.has(key)) continue;
    seen.add(key);
    out.push({ name: p.destination_name || p.place_name, coords });
  }
  return out;
}

// Lines of a country page's places (their shape_lo).
export function placeLines(places: CountryPlace[]): MapLine[] {
  return places
    .filter((p) => p.precision === "line")
    .map((p) => ({ name: p.name, coords: lineParts(p.shape_lo as never)[0] ?? [] }))
    .filter((l) => l.coords.length >= 2);
}
