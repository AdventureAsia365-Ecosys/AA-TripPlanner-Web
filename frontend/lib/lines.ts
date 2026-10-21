// AA-847 slice 6d (owner decision S246, D1): rivers, roads and trekking stretches are DRAWN as lines on every map —
// never a pin, never a circle, never hidden. Pure helpers (no Mapbox, no React) shared by the planner map, the trip
// panel and the static maps; unit-tested in lines.test.ts.

export type LineClass = "water" | "road" | "trail";
export type Coord = [number, number]; // [lng, lat]

// Style (TripPlanner map): water blue, roads ink solid, trails / paths / circuits ink dashed; a white casing under
// every line; the trip's stretch of a line is gold. Approximate / provisional lines are dashed and labelled.
export const LINE_COLOR: Record<LineClass, string> = { water: "#3B82C4", road: "#1F2933", trail: "#1F2933" };
export const TRIP_FEATURE_COLOR = "#B87A1A";
export const APPROXIMATE_LABEL = "approximate route";
// Below this zoom the map asks for the light geometry (≤ 60 vertices), from it the detailed one (≤ 300).
export const MID_ZOOM = 7;
// A join between a stop and the end of a stretch is drawn only when the end is farther than this from the stop.
export const JOIN_KM = 2;

const WATER = new Set(["river", "stream", "creek", "canal", "waterway", "song", "nam", "mae", "sungai", "jiang"]);
const ROAD = new Set(["road", "highway", "expressway", "railway", "railroad", "street", "avenue", "boulevard"]);

/** water / road / trail from the name's linear word (the last one wins: "Ho Chi Minh Trail Road" is a road). */
export function lineClass(name: string | null | undefined): LineClass {
  let out: LineClass = "trail";
  for (const w of (name ?? "").toLowerCase().normalize("NFKD").replace(/[̀-ͯ]/g, "").match(/[a-z]+/g) ?? []) {
    if (WATER.has(w)) out = "water";
    else if (ROAD.has(w)) out = "road";
    else if (["trail", "path", "circuit", "loop", "route", "trek"].includes(w)) out = "trail";
  }
  return out;
}

type HasPrecision = { precision?: string | null };

/** A line place (river, road, trail): drawn from its geometry, never as a pin. */
export function isLine(p: HasPrecision): boolean {
  return p.precision === "line";
}

/** Only these are drawn as an exact pin: a point, or a legacy row without precision. Any other value (area, line, a
 * value a newer CIS adds) is never an exact pin (6c hardening). */
export function isExactPin(p: HasPrecision): boolean {
  return p.precision === "point" || p.precision == null;
}

/** A shape status that can be drawn (ok / manual / approximate); pending / not_found / failed have no geometry. */
export function isDrawable(status: string | null | undefined): boolean {
  return status === "ok" || status === "manual" || status === "approximate";
}

export function shapeLevel(zoom: number): "lo" | "mid" {
  return zoom >= MID_ZOOM ? "mid" : "lo";
}

export function haversineKm(a: Coord, b: Coord): number {
  const R = 6371;
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dLat = toRad(b[1] - a[1]);
  const dLng = toRad(b[0] - a[0]);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(a[1])) * Math.cos(toRad(b[1])) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
}

type Geo = { type: string; coordinates: unknown } | null | undefined;

/** (Multi)LineString → its parts ([] for anything else). */
export function lineParts(g: Geo): Coord[][] {
  if (!g || !Array.isArray(g.coordinates)) return [];
  const raw = g.type === "LineString" ? [g.coordinates] : g.type === "MultiLineString" ? (g.coordinates as unknown[]) : [];
  return (raw as unknown[])
    .map((part) => (Array.isArray(part) ? part : []).filter((c): c is Coord => Array.isArray(c) && c.length >= 2)
      .map((c) => [Number(c[0]), Number(c[1])] as Coord))
    .filter((p) => p.length >= 2);
}

// ── the trip: stops, stretches of lines and the legs between them ─────────────────────────────────────────────

export interface TripItem {
  day: number;
  component_id: string;
  name: string | null;
  lat?: number;
  lng?: number;
  destination_id?: string | null;
  precision?: string | null;
  segment?: Geo;
  segment_basis?: string | null;
  segment_from_destination_id?: string | null;
  segment_to_destination_id?: string | null;
  segment_km?: number | null;
}

export interface TripStop {
  componentId: string;
  destinationId: string | null;
  name: string | null;
  coord: Coord;
}

export interface Stretch {
  componentId: string;
  name: string;
  cls: LineClass;
  geometry: Coord[]; // the day's stretch; [from, to] for a provisional line
  km: number | null;
  provisional: boolean; // no geometry yet: a dashed line between the day's two stops, "approximate route"
  fromId: string | null;
  toId: string | null;
}

export type LegPlan =
  | { kind: "pair"; from: TripStop; to: TripStop }
  | { kind: "feature"; from: TripStop; to: TripStop; stretch: Stretch; joinIn: [Coord, Coord] | null;
      joinOut: [Coord, Coord] | null };

export interface TripPlan {
  stops: TripStop[];
  legs: LegPlan[]; // stops.length - 1 entries: leg i goes from stops[i] to stops[i + 1]
  extras: Stretch[]; // stretches drawn on the map that replace no leg (a window around one stop)
}

function coordOf(d: TripItem): Coord | null {
  return typeof d.lat === "number" && typeof d.lng === "number" ? [d.lng, d.lat] : null;
}

/**
 * The trip as drawn: line places leave the waypoint list; a line day whose stretch runs between two consecutive
 * stops REPLACES the road / flight leg between them (a "feature" leg: no Directions request), joined to the stops
 * only when its ends are more than JOIN_KM away; a line with no geometry yet between two stops becomes a dashed
 * provisional line (never nothing); a window around one stop is drawn without replacing a leg.
 */
export function planTrip(items: TripItem[]): TripPlan {
  const stops: TripStop[] = [];
  for (const d of items) {
    if (isLine(d)) continue;
    const c = coordOf(d);
    if (!c) continue;
    const last = stops[stops.length - 1];
    if (last && last.coord[0] === c[0] && last.coord[1] === c[1]) continue;
    stops.push({ componentId: d.component_id, destinationId: d.destination_id ?? null, name: d.name, coord: c });
  }
  const stretches: Stretch[] = items.filter(isLine).map((d) => {
    const geom = lineParts(d.segment)[0] ?? [];
    return {
      componentId: d.component_id,
      name: d.name ?? "",
      cls: lineClass(d.name),
      geometry: geom,
      km: typeof d.segment_km === "number" ? d.segment_km : null,
      provisional: geom.length < 2,
      fromId: d.segment_from_destination_id ?? null,
      toId: d.segment_to_destination_id ?? null,
    };
  });
  const used = new Set<string>();
  const legs: LegPlan[] = [];
  for (let i = 0; i < stops.length - 1; i++) {
    const a = stops[i];
    const b = stops[i + 1];
    const s = stretches.find((x) => !used.has(x.componentId) && x.fromId && x.toId && x.fromId !== x.toId
      && x.fromId === a.destinationId && x.toId === b.destinationId);
    if (!s) {
      legs.push({ kind: "pair", from: a, to: b });
      continue;
    }
    used.add(s.componentId);
    if (s.provisional) {
      legs.push({ kind: "feature", from: a, to: b, stretch: { ...s, geometry: [a.coord, b.coord] }, joinIn: null,
        joinOut: null });
      continue;
    }
    const start = s.geometry[0];
    const end = s.geometry[s.geometry.length - 1];
    legs.push({
      kind: "feature", from: a, to: b, stretch: s,
      joinIn: haversineKm(a.coord, start) > JOIN_KM ? [a.coord, start] : null,
      joinOut: haversineKm(end, b.coord) > JOIN_KM ? [end, b.coord] : null,
    });
  }
  const extras = stretches.filter((s) => !used.has(s.componentId) && !s.provisional);
  return { stops, legs, extras };
}

/** "Along the Mekong River · ≈ 150 km" (approximate: "Mekong River · approximate route"). */
export function formatStretch(s: Stretch): string {
  if (s.provisional) return `〰 ${s.name} · ${APPROXIMATE_LABEL}`;
  const km = s.km != null ? ` · ≈ ${Math.round(s.km)} km` : "";
  return `〰 Along the ${s.name}${km}`;
}

/** The list subtitle of a line item: "Huay Xai → Pak Beng · ≈ 88 km", else "Mekong River · ≈ 88 km". */
export function stretchSubtitle(d: TripItem, nameOf: (destinationId: string) => string | null): string | null {
  if (!isLine(d)) return null;
  const km = typeof d.segment_km === "number" ? ` · ≈ ${Math.round(d.segment_km)} km` : "";
  const from = d.segment_from_destination_id ? nameOf(d.segment_from_destination_id) : null;
  const to = d.segment_to_destination_id ? nameOf(d.segment_to_destination_id) : null;
  if (d.segment_basis !== "feature" && from && to && from !== to) return `${from} → ${to}${km}`;
  if (d.segment_basis === "anchor_window" && from) return `around ${from}${km}`;
  if (!d.segment) return `${d.name ?? "Route"} · ${APPROXIMATE_LABEL}`;
  return `${d.name ?? "Route"}${km}`;
}

// ── static maps (Mapbox Static Images: path overlay with an encoded polyline) ───────────────────────────────────

/** Google polyline encoding, precision 5, of [lng, lat] coordinates. */
export function encodePolyline(coords: Coord[]): string {
  let out = "";
  let pLat = 0;
  let pLng = 0;
  const enc = (v: number) => {
    let n = v < 0 ? ~(v << 1) : v << 1;
    let s = "";
    while (n >= 0x20) {
      s += String.fromCharCode((0x20 | (n & 0x1f)) + 63);
      n >>= 5;
    }
    return s + String.fromCharCode(n + 63);
  };
  for (const [lng, lat] of coords) {
    const la = Math.round(lat * 1e5);
    const ln = Math.round(lng * 1e5);
    out += enc(la - pLat) + enc(ln - pLng);
    pLat = la;
    pLng = ln;
  }
  return out;
}

/** At most `max` evenly spaced vertices (first and last kept). */
export function downsample(coords: Coord[], max: number): Coord[] {
  if (coords.length <= max) return coords;
  const out: Coord[] = [];
  for (let i = 0; i < max; i++) out.push(coords[Math.round((i * (coords.length - 1)) / (max - 1))]);
  return out;
}

export const STATIC_MAX_LINES = 3;
export const STATIC_LINE_VERTICES = 60;

/** Mapbox Static `path` overlays for up to STATIC_MAX_LINES lines (percent-encoded polylines). */
export function staticPathOverlays(lines: { name: string | null; coords: Coord[] }[]): string[] {
  return lines
    .filter((l) => l.coords.length >= 2)
    .slice(0, STATIC_MAX_LINES)
    .map((l) => {
      const color = LINE_COLOR[lineClass(l.name)].slice(1);
      return `path-4+${color}-0.85(${encodeURIComponent(encodePolyline(downsample(l.coords, STATIC_LINE_VERTICES)))})`;
    });
}
