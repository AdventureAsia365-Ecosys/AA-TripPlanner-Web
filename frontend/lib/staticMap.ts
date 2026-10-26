// AA-812 — the Mapbox Static Images URL of a numbered places map (tour and destination pages). Pure (testable with
// node --test); components/site/StaticMap.tsx renders it.
// AA-847 (ADR 0007): an approximate point (precision "area") gets a muted pin; slice 6d: a LINE place (river, road,
// trail) is never a pin — it is a `path` overlay of its geometry (≤ 3 lines, ≤ 60 vertices each, encoded polyline).

import type { MapPoint } from "./tourMap";
import { isExactPin, isLine, staticPathOverlays, type Coord } from "./lines.ts";

export const PIN_EXACT = "db9628";
export const PIN_AREA = "9aa0a6";
export const MAX_PINS = 25; // keeps the URL well under Mapbox's 8192-char limit
export const MAX_URL = 8192;

export interface StaticLine {
  name: string | null;
  coords: Coord[];
}

export function staticMapUrl(
  points: MapPoint[],
  opts: { route?: boolean; width?: number; height?: number; lines?: StaticLine[]; token?: string } = {},
): string | null {
  const token = opts.token ?? process.env.NEXT_PUBLIC_MAPBOX_TOKEN;
  const pins = points.filter((p) => !isLine(p)).slice(0, MAX_PINS);
  const paths = staticPathOverlays(opts.lines ?? []);
  if (!token || (pins.length === 0 && paths.length === 0)) return null;
  const r6 = (n: number) => Math.round(n * 1e4) / 1e4;
  const overlays: string[] = [...paths];
  if (opts.route && pins.length > 1) {
    const line = {
      type: "Feature",
      properties: { stroke: "#db9628", "stroke-width": 3, "stroke-opacity": 0.8 },
      geometry: { type: "LineString", coordinates: pins.map((p) => [r6(p.lng), r6(p.lat)]) },
    };
    overlays.push(`geojson(${encodeURIComponent(JSON.stringify(line))})`);
  }
  pins.forEach((p, i) => {
    const label = i < 99 ? String(i + 1) : "";
    const color = isExactPin(p) ? PIN_EXACT : PIN_AREA;
    overlays.push(`pin-s${label ? `-${label}` : ""}+${color}(${r6(p.lng)},${r6(p.lat)})`);
  });
  const w = opts.width ?? 800;
  const h = opts.height ?? 480;
  const auto = pins.length === 1 && paths.length === 0 ? `${r6(pins[0].lng)},${r6(pins[0].lat)},8` : "auto";
  // Mapbox answers 422 to `padding` with a fixed center,zoom (it needs `auto` or a bounding box): S252 parity,
  // every single-pin map was a broken image.
  const padding = auto === "auto" ? "padding=48&" : "";
  const build = (ov: string[]) =>
    `https://api.mapbox.com/styles/v1/mapbox/light-v11/static/${ov.join(",")}/${auto}/${w}x${h}@2x?${padding}access_token=${encodeURIComponent(token)}`;
  let url = build(overlays);
  // Never exceed Mapbox's limit: drop the last path overlays first (the pins and the route stay)
  let drop = paths.length;
  while (url.length >= MAX_URL && drop > 0) {
    drop -= 1;
    url = build([...paths.slice(0, drop), ...overlays.slice(paths.length)]);
  }
  return url;
}
