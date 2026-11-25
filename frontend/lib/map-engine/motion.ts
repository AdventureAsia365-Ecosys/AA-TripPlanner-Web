// Map engine core (AA-948, the root ADR 0016 pattern). Canonical: AA-TripPlanner-Web frontend/lib/map-engine. Edit only there; copies are checked byte for byte (map-engine.lock.json).
//
// motion.ts: camera motion follows the site's own "reduce motion" gate. mapbox-gl skips camera animations for the OS
// reduced-motion setting, not for a site toggle (<html data-motion="reduce">, AA-995), so every camera move of the
// engine reads the gate at call time. No imports; safe on the server (no document = motion allowed).

/** True when the page asked for reduced motion (<html data-motion="reduce">). */
export function motionReduced(): boolean {
  return typeof document !== "undefined" && document.documentElement.getAttribute("data-motion") === "reduce";
}

/** A camera duration: 0 when motion is reduced, else `ms`. */
export function camMs(ms: number): number {
  return motionReduced() ? 0 : ms;
}

/** Extra camera options: `{ duration: 0 }` when motion is reduced (keeps Mapbox's default duration otherwise). */
export function camReduced(): { duration?: number } {
  return motionReduced() ? { duration: 0 } : {};
}
