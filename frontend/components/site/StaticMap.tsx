// AA-812 — a static Mapbox map of numbered places (tour and destination pages). Server-rendered
// <img>, no Mapbox GL bundle on these pages. Without NEXT_PUBLIC_MAPBOX_TOKEN the map is left
// out and the place list next to it carries the information.

import type { MapPoint } from "@/lib/tourMap";

const MAX_PINS = 25; // keeps the URL well under Mapbox's 8192-char limit

export function staticMapUrl(points: MapPoint[], opts: { route?: boolean; width?: number; height?: number } = {}): string | null {
  const token = process.env.NEXT_PUBLIC_MAPBOX_TOKEN;
  if (!token || points.length === 0) return null;
  const pts = points.slice(0, MAX_PINS);
  const r6 = (n: number) => Math.round(n * 1e4) / 1e4;
  const overlays: string[] = [];
  if (opts.route && pts.length > 1) {
    const line = {
      type: "Feature",
      properties: { stroke: "#db9628", "stroke-width": 3, "stroke-opacity": 0.8 },
      geometry: { type: "LineString", coordinates: pts.map((p) => [r6(p.lng), r6(p.lat)]) },
    };
    overlays.push(`geojson(${encodeURIComponent(JSON.stringify(line))})`);
  }
  pts.forEach((p, i) => {
    const label = i < 99 ? String(i + 1) : "";
    overlays.push(`pin-s${label ? `-${label}` : ""}+db9628(${r6(p.lng)},${r6(p.lat)})`);
  });
  const w = opts.width ?? 800;
  const h = opts.height ?? 480;
  const auto = pts.length === 1 ? `${r6(pts[0].lng)},${r6(pts[0].lat)},8` : "auto";
  return `https://api.mapbox.com/styles/v1/mapbox/light-v11/static/${overlays.join(",")}/${auto}/${w}x${h}@2x?padding=48&access_token=${encodeURIComponent(token)}`;
}

export default function StaticMap({
  points,
  route = false,
  title,
  caption,
}: {
  points: MapPoint[];
  route?: boolean;
  title: string;
  // What the pin numbers mean on this page (itinerary order, list order, ...).
  caption?: string;
}) {
  const url = staticMapUrl(points, { route });
  if (!url) return null;
  return (
    <figure className="overflow-hidden rounded-2xl border border-k-line bg-k-card">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={url} alt={`Map: ${title}`} loading="lazy" className="aspect-[5/3] w-full object-cover" />
      <figcaption className="px-3 py-2 text-[11px] text-k-muted">
        {caption ? `${caption} ` : ""}© Mapbox © OpenStreetMap
      </figcaption>
    </figure>
  );
}
