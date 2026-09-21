// Mapbox helpers. The public token is exposed to the browser by design
// (GL JS is client-side); protect it with URL restrictions in production.

export const MAPBOX_TOKEN = process.env.NEXT_PUBLIC_MAPBOX_TOKEN ?? "";

export function hasMapboxToken(): boolean {
  return MAPBOX_TOKEN.length > 0;
}

// Fixed 1-degree grid tile id from lat/lng (matches backend TILE_DEGREES).
export function tileIdFor(lat: number, lng: number): string {
  const latCell = Math.floor(lat);
  const lngCell = Math.floor(lng);
  return `${latCell}_${lngCell}`;
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
export interface RouteLeg {
  mode: "road" | "flight";
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
}

import { COUNTRY_GATEWAY, type Gateway } from "./types";

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

  const flightLeg = (a: [number, number], b: [number, number]): RouteLeg => ({
    mode: "flight",
    distanceKm: null,
    durationMin: null,
    geometry: [],
    from: a,
    to: b,
  });

  const legs: RouteLeg[] = [];
  for (let i = 0; i < pts.length - 1; i++) {
    const a = pts[i];
    const b = pts[i + 1];
    const straight = haversineKm(a, b);

    if (!hasMapboxToken() || straight > MAX_DRIVE_KM) {
      legs.push(flightLeg(a, b));
      continue;
    }

    const url =
      `https://api.mapbox.com/directions/v5/mapbox/driving/${a[0]},${a[1]};${b[0]},${b[1]}` +
      `?geometries=geojson&overview=full&access_token=${MAPBOX_TOKEN}`;
    try {
      const res = await fetch(url);
      if (!res.ok) {
        legs.push(flightLeg(a, b));
        continue;
      }
      const route = (await res.json())?.routes?.[0];
      const distM = route?.distance;
      const geo = route?.geometry?.coordinates;
      const roadKm = typeof distM === "number" ? distM / 1000 : null;
      const ok =
        roadKm !== null &&
        roadKm > 0 &&
        Array.isArray(geo) &&
        geo.length >= 2 &&
        roadKm <= Math.max(straight * MAX_ROAD_DETOUR_RATIO, straight + 20);
      if (!ok) {
        legs.push(flightLeg(a, b));
        continue;
      }
      legs.push({
        mode: "road",
        distanceKm: roadKm,
        durationMin: (typeof route.duration === "number" ? route.duration : 0) / 60,
        geometry: geo as [number, number][],
        from: a,
        to: b,
      });
    } catch {
      legs.push(flightLeg(a, b));
    }
  }
  return legs;
}

// Human-friendly label for a leg.
//   road   -> "🚙 120 km · ~2h" (real driving distance/time)
//   flight -> "✈️ Flight or transfer — arranged by your advisor" (no km)
export function formatLeg(leg: RouteLeg): string {
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
