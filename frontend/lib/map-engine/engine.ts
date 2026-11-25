// Map engine core (AA-948, the root ADR 0016 pattern). Canonical: AA-TripPlanner-Web frontend/lib/map-engine. Edit only there; copies are checked byte for byte (map-engine.lock.json).
//
// engine.ts: the framework-free map engine shared by the TripPlanner planner and the CRM proposal builder. It owns one
// Mapbox GL map: creation (token, style, camera), the sizing fixes, error reporting, GeoJSON source updates, camera
// framing that follows the site's reduce-motion gate, DOM markers, one popup, layer clicks, and teardown. App data
// (what a pin means, which layers exist, colours) stays in the app. No React, no Next: the mapbox-gl module is passed in
// (the app imports it, or calls loadMapboxGl), so this file holds a type import only and runs in tests with a fake.
import type mapboxgl from "mapbox-gl";
import { boundsOf, isLngLat, type Frameable, type LngLat } from "./geo";
import { camMs, camReduced } from "./motion";

/** The mapbox-gl module's default export (what `import mapboxgl from "mapbox-gl"` gives). */
export type MapboxModule = typeof mapboxgl;

export interface MapEngineOptions {
  container: HTMLElement;
  /** The public (pk.) token, URL-restricted to the app's host. Never a code constant: the app reads it from config. */
  accessToken: string;
  style?: string;
  center?: LngLat;
  zoom?: number;
  /** Called with a Mapbox error message (token, style or worker problems come as an "error" event, not an exception).
   *  Default: console.error with the "[mapbox error]" prefix. */
  onError?: (message: string) => void;
}

export interface FitOptions {
  padding?: number;
  maxZoom?: number;
  /** Animation length; 0 when the page asked for reduced motion. */
  durationMs?: number;
}

export interface MarkerSpec {
  /** Stable key: a marker whose key stays is moved, not re-created. */
  key: string;
  coord: LngLat;
  element: HTMLElement;
}

export interface MapEngine {
  readonly map: mapboxgl.Map;
  /** Runs `cb` once the style has loaded (at once when it already has). */
  onReady(cb: () => void): void;
  /** Replace the data of a GeoJSON source; false when the source does not exist (yet). */
  setData(sourceId: string, data: GeoJSON.FeatureCollection): boolean;
  /** Frame points and boxes; false (no camera move) when none is valid. */
  fitTo(items: readonly Frameable[], opts?: FitOptions): boolean;
  /** Ease the camera to a point (instant when motion is reduced). */
  easeTo(center: LngLat, zoom?: number): void;
  /** Show exactly these DOM markers (by key); others are removed. */
  setMarkers(markers: readonly MarkerSpec[]): void;
  /** Open the one popup at a point with this content (replaces an open one). */
  showPopup(coord: LngLat, content: HTMLElement, opts?: { offset?: number }): void;
  closePopup(): void;
  /** Click handler on features of these layers (pointer cursor on hover); gets the first feature's properties. */
  onLayerClick(layerIds: readonly string[], handler: (props: Record<string, unknown>, coord: LngLat | null) => void): void;
  /** Remove markers, popup, observers and the map. Safe to call twice. */
  destroy(): void;
}

export const DEFAULT_STYLE = "mapbox://styles/mapbox/outdoors-v12";
export const DEFAULT_CENTER: LngLat = [105, 15];
export const DEFAULT_ZOOM = 3;

/** True when a token is configured (the map is not created without one; the app shows its empty state). */
export function hasToken(token: string | null | undefined): token is string {
  return typeof token === "string" && token.trim().length > 0;
}

/** Load the mapbox-gl module on demand (keeps it out of the first bundle of a page that may not show a map). */
export async function loadMapboxGl(): Promise<MapboxModule> {
  const mod = await import("mapbox-gl");
  return (mod as unknown as { default?: MapboxModule }).default ?? (mod as unknown as MapboxModule);
}

export function createMapEngine(gl: MapboxModule, opts: MapEngineOptions): MapEngine {
  if (!hasToken(opts.accessToken)) throw new Error("map engine: no access token (show the empty state instead)");
  gl.accessToken = opts.accessToken;
  const map = new gl.Map({
    container: opts.container,
    style: opts.style ?? DEFAULT_STYLE,
    center: opts.center ?? DEFAULT_CENTER,
    zoom: opts.zoom ?? DEFAULT_ZOOM,
  });
  const report = opts.onError ?? ((message: string) => console.error("[mapbox error]", message));
  map.on("error", (e: { error?: { message?: string } }) => {
    report(e?.error?.message ?? String(e));
  });

  // The map is created after first paint, but Mapbox still often measures the container before layout settles and
  // locks the canvas at its 400x300 default: resize on the next two animation frames, and on every later layout
  // change of the container.
  const raf =
    typeof requestAnimationFrame === "function"
      ? requestAnimationFrame(() => {
          map.resize();
          requestAnimationFrame(() => map.resize());
        })
      : null;
  const ro = typeof ResizeObserver === "function" ? new ResizeObserver(() => map.resize()) : null;
  ro?.observe(opts.container);

  let loaded = false;
  const waiting: (() => void)[] = [];
  map.on("load", () => {
    map.resize(); // the canvas matches the container after the first layout
    loaded = true;
    for (const cb of waiting.splice(0)) cb();
  });

  const markers = new Map<string, { marker: mapboxgl.Marker; element: HTMLElement }>();
  let popup: mapboxgl.Popup | null = null;
  let destroyed = false;

  const engine: MapEngine = {
    map,
    onReady(cb) {
      if (loaded) cb();
      else waiting.push(cb);
    },
    setData(sourceId, data) {
      const src = map.getSource(sourceId) as mapboxgl.GeoJSONSource | undefined;
      if (!src) return false;
      src.setData(data);
      return true;
    },
    fitTo(items, fit = {}) {
      const b = boundsOf(items);
      if (!b) return false;
      map.fitBounds(
        [
          [b[0], b[1]],
          [b[2], b[3]],
        ],
        { padding: fit.padding ?? 80, maxZoom: fit.maxZoom ?? 9, duration: camMs(fit.durationMs ?? 600) },
      );
      return true;
    },
    easeTo(center, zoom) {
      map.easeTo({ center, ...(zoom === undefined ? {} : { zoom }), ...camReduced() });
    },
    setMarkers(specs) {
      const keep = new Set(specs.map((s) => s.key));
      for (const [key, m] of markers) {
        if (!keep.has(key)) {
          m.marker.remove();
          markers.delete(key);
        }
      }
      for (const s of specs) {
        if (!isLngLat(s.coord)) continue;
        const have = markers.get(s.key);
        if (have && have.element === s.element) {
          have.marker.setLngLat(s.coord);
          continue;
        }
        have?.marker.remove();
        const marker = new gl.Marker({ element: s.element }).setLngLat(s.coord).addTo(map);
        markers.set(s.key, { marker, element: s.element });
      }
    },
    showPopup(coord, content, popupOpts = {}) {
      popup?.remove();
      popup = new gl.Popup({ offset: popupOpts.offset ?? 16, closeButton: true, maxWidth: "280px" })
        .setLngLat(coord)
        .setDOMContent(content)
        .addTo(map);
    },
    closePopup() {
      popup?.remove();
      popup = null;
    },
    onLayerClick(layerIds, handler) {
      for (const layer of layerIds) {
        map.on("click", layer, (e: mapboxgl.MapLayerMouseEvent) => {
          const f = e.features?.[0];
          const geom = f?.geometry;
          const coord = geom && geom.type === "Point" && isLngLat(geom.coordinates) ? (geom.coordinates as LngLat) : null;
          handler((f?.properties ?? {}) as Record<string, unknown>, coord);
        });
        map.on("mouseenter", layer, () => {
          map.getCanvas().style.cursor = "pointer";
        });
        map.on("mouseleave", layer, () => {
          map.getCanvas().style.cursor = "";
        });
      }
    },
    destroy() {
      if (destroyed) return;
      destroyed = true;
      if (raf !== null && typeof cancelAnimationFrame === "function") cancelAnimationFrame(raf);
      ro?.disconnect();
      for (const m of markers.values()) m.marker.remove();
      markers.clear();
      popup?.remove();
      popup = null;
      waiting.length = 0;
      map.remove();
    },
  };
  return engine;
}
