// Mapbox helpers. The public token is exposed to the browser by design
// (GL JS is client-side); protect it with URL restrictions in production.

export const MAPBOX_TOKEN = process.env.NEXT_PUBLIC_MAPBOX_TOKEN ?? "";

export function hasMapboxToken(): boolean {
  return MAPBOX_TOKEN.length > 0;
}

// Mapbox Directions: max coordinates per request for the driving profile.
const MAX_DIRECTIONS_WAYPOINTS = 25;

// A single travel leg between two consecutive stops.
// - mode "road": a real drivable route exists (from Mapbox Directions). We
//   have a trustworthy distance/duration AND road-following geometry to draw.
// - mode "flight": no sensible road route (islands/cross-water, or the road
//   would detour absurdly / across a border, or the stops are simply too far
//   to drive). We do NOT invent a driving distance; the advisor arranges the
//   real flight/transfer.
// AA-847 slice 6d: mode "feature" = the day follows a river / road / trail between two stops (the line's stretch,
// from CIS geo.tour_segment): drawn in gold along the line, NO Directions request, and it replaces the road or flight
// leg between the same stops. `joins` link a stop to the stretch's end when that end is more than 2 km away.
export interface RouteLeg {
  mode: "road" | "flight" | "feature";
  // Present only for road legs (from Directions). null for flight legs — we
  // deliberately don't show a fabricated straight-line "distance".
  distanceKm: number | null;
  durationMin: number | null;
  // Road-following geometry ([lng,lat] coords) for road legs, for drawing the
  // real route on the map. Empty for flight legs (drawn as a dashed arc).
  geometry: [number, number][];
  // The two endpoints, for drawing.
  from: [number, number];
  to: [number, number];
  // feature legs only
  stretch?: Stretch;
  joins?: RouteLeg[];
}

import { COUNTRY_GATEWAY, type Gateway } from "./types";
import { formatStretch, type Stretch, type TripPlan } from "./lines";

// All known gateway airports, flattened once across countries. A stop finds
// its nearest gateway across this whole set, so we don't need a country field
// on the itinerary to place arrival/departure.
export const ALL_GATEWAYS: Gateway[] = Object.values(COUNTRY_GATEWAY).flat();

// Nearest gateway airport to a [lng,lat] stop, with the transfer distance.
// Returns null only when no gateways are configured at all.
export function nearestGateway(
  coord: [number, number],
): { gw: Gateway; km: number } | null {
  let best: { gw: Gateway; km: number } | null = null;
  for (const gw of ALL_GATEWAYS) {
    const km = haversineKm(coord, [gw.lng, gw.lat]);
    if (!best || km < best.km) best = { gw, km };
  }
  return best;
}

// Haversine great-circle distance in km between two [lng,lat] points.
export function haversineKm(a: [number, number], b: [number, number]): number {
  const R = 6371;
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dLat = toRad(b[1] - a[1]);
  const dLng = toRad(b[0] - a[0]);
  const lat1 = toRad(a[1]);
  const lat2 = toRad(b[1]);
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
}

// A driving leg is only trusted as "road" when the road distance is
// plausible vs the straight-line distance. If Directions has to detour a lot
// (road >> straight line) the two stops aren't sensibly road-connected (they
// route around water or across a border) — treat that as a flight leg rather
// than draw a snaking line and quote a meaningless distance.
const MAX_ROAD_DETOUR_RATIO = 1.8; // road km / straight km
const MAX_DRIVE_KM = 350; // beyond this, call it a flight regardless
// AA-674: mountain roads wind. Thimphu -> Haa over Chele La is ~2.5x the straight line and was
// shown as "Flight or transfer". Short hops may detour much more and still be a real drive.
const SHORT_HOP_KM = 150;
const SHORT_HOP_DETOUR_RATIO = 3.5;
function detourRatio(straightKm: number): number {
  return straightKm < SHORT_HOP_KM ? SHORT_HOP_DETOUR_RATIO : MAX_ROAD_DETOUR_RATIO;
}

/**
 * Resolve each consecutive pair of stops into a travel leg. For each pair we
 * ask Mapbox Directions (driving) for the real road route + geometry:
 *   - if a sensible road route exists -> a "road" leg (real km/time + geometry)
 *   - otherwise -> a "flight" leg (no fabricated distance; drawn as an arc)
 *
 * Per-pair requests (not one multi-leg request) so each leg gets its own
 * geometry and road/flight decision. Returns stops.length - 1 legs.
 */
function flightLeg(a: [number, number], b: [number, number]): RouteLeg {
  return { mode: "flight", distanceKm: null, durationMin: null, geometry: [], from: a, to: b };
}

// One consecutive pair: a road leg when Directions finds a sensible drive, else a flight leg.
async function resolvePair(a: [number, number], b: [number, number]): Promise<RouteLeg> {
  const straight = haversineKm(a, b);
  if (!hasMapboxToken() || straight > MAX_DRIVE_KM) return flightLeg(a, b);
  const url =
    `https://api.mapbox.com/directions/v5/mapbox/driving/${a[0]},${a[1]};${b[0]},${b[1]}` +
    `?geometries=geojson&overview=full&access_token=${MAPBOX_TOKEN}`;
  try {
    const res = await fetch(url);
    if (!res.ok) return flightLeg(a, b);
    const route = (await res.json())?.routes?.[0];
    const distM = route?.distance;
    const geo = route?.geometry?.coordinates;
    const roadKm = typeof distM === "number" ? distM / 1000 : null;
    const ok =
      roadKm !== null &&
      roadKm > 0 &&
      Array.isArray(geo) &&
      geo.length >= 2 &&
      roadKm <= Math.max(straight * detourRatio(straight), straight + 20);
    if (!ok) return flightLeg(a, b);
    return {
      mode: "road",
      distanceKm: roadKm,
      durationMin: (typeof route.duration === "number" ? route.duration : 0) / 60,
      geometry: geo as [number, number][],
      from: a,
      to: b,
    };
  } catch {
    return flightLeg(a, b);
  }
}

/**
 * Resolve each consecutive pair of stops into a travel leg. For each pair we
 * ask Mapbox Directions (driving) for the real road route + geometry:
 *   - if a sensible road route exists -> a "road" leg (real km/time + geometry)
 *   - otherwise -> a "flight" leg (no fabricated distance; drawn as an arc)
 *
 * Per-pair requests (not one multi-leg request) so each leg gets its own
 * geometry and road/flight decision. Returns stops.length - 1 legs.
 */
export async function fetchRouteLegs(
  waypoints: [number, number][],
): Promise<RouteLeg[] | null> {
  if (waypoints.length < 2) return null;
  const pts = waypoints.slice(0, MAX_DIRECTIONS_WAYPOINTS);
  const legs: RouteLeg[] = [];
  for (let i = 0; i < pts.length - 1; i++) legs.push(await resolvePair(pts[i], pts[i + 1]));
  return legs;
}

/**
 * AA-847 slice 6d: the legs of a planned trip (lib/lines.ts planTrip): one per plan leg, in order. A "pair" asks
 * Directions as before; a "feature" leg follows the line's stretch with no request, plus a Directions (or flight)
 * join only for a stretch end more than 2 km from its stop — so a trip sends the same number of Directions requests
 * or fewer than before.
 */
export async function fetchTripLegs(plan: TripPlan): Promise<RouteLeg[]> {
  const legs: RouteLeg[] = [];
  for (const p of plan.legs.slice(0, MAX_DIRECTIONS_WAYPOINTS - 1)) {
    if (p.kind === "pair") {
      legs.push(await resolvePair(p.from.coord, p.to.coord));
      continue;
    }
    const joins: RouteLeg[] = [];
    if (p.joinIn) joins.push(await resolvePair(p.joinIn[0], p.joinIn[1]));
    if (p.joinOut) joins.push(await resolvePair(p.joinOut[0], p.joinOut[1]));
    legs.push({
      mode: "feature",
      distanceKm: p.stretch.km,
      durationMin: null,
      geometry: p.stretch.geometry,
      from: p.from.coord,
      to: p.to.coord,
      stretch: p.stretch,
      joins,
    });
  }
  return legs;
}

// Human-friendly label for a leg.
//   road   -> "🚙 120 km · ~2h" (real driving distance/time)
//   flight -> "✈️ Flight or transfer — arranged by your advisor" (no km)
export function formatLeg(leg: RouteLeg): string {
  // AA-847 slice 6d: "〰 Along the Mekong River · ≈ 150 km" (or "· approximate route" while it has no geometry)
  if (leg.mode === "feature" && leg.stretch) return formatStretch(leg.stretch);
  if (leg.mode === "flight" || leg.distanceKm === null) {
    return "✈️ Flight or transfer — arranged by your advisor";
  }
  const km = Math.round(leg.distanceKm);
  const mins = Math.round(leg.durationMin ?? 0);
  let time: string;
  if (mins < 60) {
    time = `~${mins} min`;
  } else {
    const h = Math.floor(mins / 60);
    const m = mins % 60;
    time = m === 0 ? `~${h}h` : `~${h}h ${m}m`;
  }
  const icon = km < 60 ? "🚙" : "🚙";
  return `${icon} ${km} km · ${time} by road`;
}
