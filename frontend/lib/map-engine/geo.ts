// Map engine core (AA-948, the root ADR 0016 pattern). Canonical: AA-TripPlanner-Web frontend/lib/map-engine. Edit only there; copies are checked byte for byte (map-engine.lock.json).
//
// geo.ts: the pure geometry of the shared map engine. No imports, no DOM, no Mapbox: coordinates are [lng, lat]
// pairs (Mapbox order), a bounding box is [west, south, east, north].

export type LngLat = [number, number];
export type BBox = [number, number, number, number];

/** A route stop: the number on its marker (a day) and where it is. */
export interface RouteStop {
  label: string;
  coord: LngLat;
}

/** A point to frame: a coordinate, or a whole bounding box (a river, a road). */
export type Frameable = LngLat | BBox;

/** True for a finite [lng, lat] inside the valid ranges (a missing or broken coordinate is never drawn). */
export function isLngLat(value: unknown): value is LngLat {
  if (!Array.isArray(value) || value.length !== 2) return false;
  const [lng, lat] = value;
  return (
    typeof lng === "number" &&
    typeof lat === "number" &&
    Number.isFinite(lng) &&
    Number.isFinite(lat) &&
    lng >= -180 &&
    lng <= 180 &&
    lat >= -90 &&
    lat <= 90
  );
}

/** [lng, lat] from separate values, or null when either is missing or out of range. */
export function lngLat(lng: unknown, lat: unknown): LngLat | null {
  const c = [lng, lat];
  return isLngLat(c) ? c : null;
}

/** Interpolate a great-circle arc between two [lng, lat] points (n segments). A slight arc (vs a dead-straight
 *  segment) reads as "travel between", and stays visually sensible over long spans. */
export function greatCircleSegment(a: LngLat, b: LngLat, n = 24): LngLat[] {
  const toRad = (d: number) => (d * Math.PI) / 180;
  const toDeg = (r: number) => (r * 180) / Math.PI;
  const lat1 = toRad(a[1]);
  const lon1 = toRad(a[0]);
  const lat2 = toRad(b[1]);
  const lon2 = toRad(b[0]);
  const d =
    2 *
    Math.asin(
      Math.min(
        1,
        Math.sqrt(
          Math.sin((lat2 - lat1) / 2) ** 2 +
            Math.cos(lat1) * Math.cos(lat2) * Math.sin((lon2 - lon1) / 2) ** 2,
        ),
      ),
    );
  if (d === 0) return [a, b];
  const out: LngLat[] = [];
  for (let i = 0; i <= n; i++) {
    const f = i / n;
    const A = Math.sin((1 - f) * d) / Math.sin(d);
    const B = Math.sin(f * d) / Math.sin(d);
    const x = A * Math.cos(lat1) * Math.cos(lon1) + B * Math.cos(lat2) * Math.cos(lon2);
    const y = A * Math.cos(lat1) * Math.sin(lon1) + B * Math.cos(lat2) * Math.sin(lon2);
    const z = A * Math.sin(lat1) + B * Math.sin(lat2);
    const lat = Math.atan2(z, Math.sqrt(x * x + y * y));
    const lon = Math.atan2(y, x);
    out.push([toDeg(lon), toDeg(lat)]);
  }
  return out;
}

function isBBox(value: Frameable): value is BBox {
  return value.length === 4;
}

/** The bounding box of points and boxes (a box counts by its two corners), or null when nothing valid is given. */
export function boundsOf(items: readonly Frameable[]): BBox | null {
  let w = Infinity;
  let s = Infinity;
  let e = -Infinity;
  let n = -Infinity;
  const add = (c: LngLat) => {
    if (!isLngLat(c)) return;
    w = Math.min(w, c[0]);
    s = Math.min(s, c[1]);
    e = Math.max(e, c[0]);
    n = Math.max(n, c[1]);
  };
  for (const it of items) {
    if (isBBox(it)) {
      add([it[0], it[1]]);
      add([it[2], it[3]]);
    } else {
      add(it);
    }
  }
  return Number.isFinite(w) ? [w, s, e, n] : null;
}

/** Route stops from days in order: one stop per place visit, so consecutive days at the same point share the first
 *  day's stop; a day without a valid point is skipped (it has no pin). */
export function routeStops(days: readonly { label: string; coord: LngLat | null | undefined }[]): RouteStop[] {
  const stops: RouteStop[] = [];
  for (const d of days) {
    if (!d.coord || !isLngLat(d.coord)) continue;
    const last = stops[stops.length - 1];
    if (last && last.coord[0] === d.coord[0] && last.coord[1] === d.coord[1]) continue;
    stops.push({ label: d.label, coord: d.coord });
  }
  return stops;
}

/** The line through the stops, as joined great-circle arcs of `n` segments each (empty below two stops). */
export function routeLine(stops: readonly RouteStop[], n = 8): LngLat[] {
  const line: LngLat[] = [];
  for (let i = 0; i < stops.length - 1; i++) {
    const seg = greatCircleSegment(stops[i].coord, stops[i + 1].coord, n);
    line.push(...(i === 0 ? seg : seg.slice(1)));
  }
  return line;
}

/** The GeoJSON of a "stops + route" layer set: the route line (when there is one) and one labelled point per stop. */
export function routeCollection(stops: readonly RouteStop[], n = 8): GeoJSON.FeatureCollection {
  const line = routeLine(stops, n);
  return {
    type: "FeatureCollection",
    features: [
      ...(line.length > 1
        ? [{ type: "Feature" as const, properties: {}, geometry: { type: "LineString" as const, coordinates: line } }]
        : []),
      ...stops.map((st) => ({
        type: "Feature" as const,
        properties: { label: st.label },
        geometry: { type: "Point" as const, coordinates: st.coord },
      })),
    ],
  };
}

/** An empty FeatureCollection (clears a source). */
export function emptyCollection(): GeoJSON.FeatureCollection {
  return { type: "FeatureCollection", features: [] };
}
