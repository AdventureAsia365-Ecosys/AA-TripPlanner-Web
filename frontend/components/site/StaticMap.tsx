// AA-812 — a static Mapbox map of numbered places (tour and destination pages). Server-rendered
// <img>, no Mapbox GL bundle on these pages. Without NEXT_PUBLIC_MAPBOX_TOKEN the map is left
// out and the place list next to it carries the information.

import type { MapPoint } from "@/lib/tourMap";
import { GEOCODE_ATTRIBUTION, needsGeocodeAttribution } from "@/lib/types";
import { isExactPin, isLine } from "@/lib/lines";
import { MAX_PINS, staticMapUrl, type StaticLine } from "@/lib/staticMap";

// AA-847 (ADR 0007): an approximate point (precision "area": the model's point, not a geocoded one) gets a
// muted pin, so it never reads as an exact spot; the caption says so. Slice 6d: a river / road / trail is drawn as a
// path (lib/staticMap.ts), never a pin; with no geometry yet it is left off the map (listed by name next to it).
export { staticMapUrl };

export default function StaticMap({
  points,
  route = false,
  lines = [],
  title,
  caption,
}: {
  points: MapPoint[];
  route?: boolean;
  lines?: StaticLine[];
  title: string;
  // What the pin numbers mean on this page (itinerary order, list order, ...).
  caption?: string;
}) {
  const url = staticMapUrl(points, { route, lines });
  if (!url) return null;
  const shown = points.filter((p) => !isLine(p)).slice(0, MAX_PINS);
  const approximate = shown.some((p) => !isExactPin(p));
  const geoapify = needsGeocodeAttribution(shown);
  return (
    <figure className="overflow-hidden rounded-2xl border border-k-line bg-k-card">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={url} alt={`Map: ${title}`} loading="lazy" className="aspect-[5/3] w-full object-cover" />
      <figcaption className="px-3 py-2 text-[11px] text-k-muted">
        {caption ? `${caption} ` : ""}
        {approximate ? "Grey pins mark an approximate area. " : ""}
        {lines.length > 0 ? "Lines show rivers, roads and trails. " : ""}© Mapbox © OpenStreetMap
        {geoapify && (
          <>
            {" · "}
            <a href={GEOCODE_ATTRIBUTION.geoapify.href} target="_blank" rel="noopener noreferrer" className="underline">
              {GEOCODE_ATTRIBUTION.geoapify.label}
            </a>
            {" · "}
            <a href={GEOCODE_ATTRIBUTION.osm.href} target="_blank" rel="noopener noreferrer" className="underline">
              {GEOCODE_ATTRIBUTION.osm.label}
            </a>
          </>
        )}
      </figcaption>
    </figure>
  );
}
