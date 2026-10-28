"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import mapboxgl from "mapbox-gl";
import "mapbox-gl/dist/mapbox-gl.css";
import { fetchByCountry, fetchShapes, fetchTile, tileScope } from "@/lib/api";
import {
  fetchTripLegs,
  hasMapboxToken,
  MAPBOX_TOKEN,
  nearestGateway,
} from "@/lib/mapbox";
import {
  APPROXIMATE_LABEL,
  LINE_COLOR,
  TRIP_FEATURE_COLOR,
  formatStretch,
  isDrawable,
  isExactPin,
  isLine,
  lineClass,
  lineParts,
  planTrip,
  shapeLevel,
} from "@/lib/lines";
import { TileLoader, planTiles } from "@/lib/tileLoader";
import type { BrowseFilters, DestinationPin } from "@/lib/types";
import { COUNTRY_BBOX, COUNTRY_ISO, needsGeocodeAttribution } from "@/lib/types";
import GeocodeAttribution from "./GeocodeAttribution";
import { useTrip } from "@/lib/useTrip";
import DestinationPopup from "./DestinationPopup";

const SOURCE_ID = "destinations";
// AA-847 slice 6d (owner decision S246, D1): rivers, roads and trails are drawn as lines (unclustered, deduped by id),
// never a pin, a circle or hidden; the trip's stretch of a line is a gold "feature" leg.
const LINES_SOURCE = "destination-lines";
const TRIP_FEATURE_SOURCE = "trip-feature-line";
// Kill switch: draw every line in the provisional (dashed ink) style. ⚠️ underscore env name — set by hand in Vercel.
const LINES_PROVISIONAL = ["1", "true", "on"].includes((process.env.NEXT_PUBLIC_MAP_LINES_PROVISIONAL ?? "").toLowerCase());

// Extend bounds by a destination: its bbox for a line, its point otherwise.
// AA-995: mapbox-gl skips camera animations for the OS reduced-motion setting, not for the site's own gate
// (<html data-motion="reduce">, set by the footer "Reduce motion" toggle too). Read the gate at call time.
function motionReduced(): boolean {
  return typeof document !== "undefined" && document.documentElement.getAttribute("data-motion") === "reduce";
}
function camMs(ms: number): number {
  return motionReduced() ? 0 : ms;
}
function camReduced(): { duration?: number } {
  return motionReduced() ? { duration: 0 } : {};
}

function extendBy(b: mapboxgl.LngLatBounds, p: DestinationPin) {
  if (isLine(p) && p.bbox) {
    b.extend([p.bbox[0], p.bbox[1]]);
    b.extend([p.bbox[2], p.bbox[3]]);
  } else {
    b.extend([p.lng, p.lat]);
  }
}
const TRIP_LINE_SOURCE = "trip-line";
const TRIP_FLIGHT_SOURCE = "trip-flight-line";
const TRIP_STOP_SOURCE = "trip-stops";
const TRIP_GATEWAY_SOURCE = "trip-gateways";
const COUNTRY_SOURCE = "country-boundaries";
// AA-674: preview of a proposed AA route (hovered in the route planner).
const ROUTE_PREVIEW_SOURCE = "route-preview";

// Interpolate a great-circle arc between two [lng,lat] points (n segments).
// A slight arc (vs a dead-straight segment) reads as "travel between", and
// stays visually sensible over long spans.
function greatCircleSegment(
  a: [number, number],
  b: [number, number],
  n = 24,
): [number, number][] {
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
  const out: [number, number][] = [];
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

// AA-812 hotfix (D1): tiles are planned by lib/tileLoader (bounded per view, coarse cells at low
// zoom) instead of one request per 1-degree cell. Wait this long after the last moveend.
const MOVE_DEBOUNCE_MS = 250;
// A partial view (a tile failed, or the request budget ran out) keeps the previous pins and is
// retried once after this delay (or after the budget window frees up, if later).
const PARTIAL_RETRY_MS = 3_000;

export default function MapView() {
  const {
    filters,
    searchResults,
    itinerary,
    focusDestinationId,
    focusDestination,
    previewCountry,
    routePreview,
  } = useTrip();
  const mapRef = useRef<mapboxgl.Map | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const pinsRef = useRef<Map<string, DestinationPin>>(new Map());
  // AA-847 slice 6d: the line places on screen (by id) and the shape level drawn
  const linesRef = useRef<DestinationPin[]>([]);
  const lineLevelRef = useRef<"lo" | "mid">("lo");
  const [selected, setSelected] = useState<string | null>(null);
  // AA-847: the Geoapify / OSM attribution is shown while a Geoapify-located point is drawn.
  const [attribution, setAttribution] = useState(false);
  const filtersRef = useRef(filters);
  // When search is active, the map shows the ranked result set instead of
  // tiles-by-bounds. Kept in a ref so the moveend handler can bail out.
  const searchResultsRef = useRef(searchResults);
  // AA-906 (React Compiler lint: no ref writes during render): the Mapbox handlers read the latest
  // filters / search results through these refs, synced after each commit. A layout effect runs
  // before every passive effect below (which re-query on a filter change), so they see the new values.
  useLayoutEffect(() => {
    filtersRef.current = filters;
    searchResultsRef.current = searchResults;
  }, [filters, searchResults]);

  // AA-847 slice 6d: draw the line places from their geometry (fetched by id, cached; light below zoom 7). A line
  // whose geometry is missing (pending / not found) is not drawn here: it is listed by name, flagged on CIS
  // /admin/places, and the trip map draws its provisional line between the day's stops.
  const drawLines = useCallback(async () => {
    const map = mapRef.current;
    if (!map) return;
    const lines = linesRef.current;
    const level = shapeLevel(map.getZoom());
    lineLevelRef.current = level;
    const src = map.getSource(LINES_SOURCE) as mapboxgl.GeoJSONSource | undefined;
    if (!src) return;
    const drawable = lines.filter((p) => isDrawable(p.shape_status));
    const shapes = drawable.length ? await fetchShapes(drawable.map((p) => p.id), level) : new Map();
    if (linesRef.current !== lines || !mapRef.current) return; // a newer view replaced these lines
    const features: GeoJSON.Feature[] = [];
    for (const p of drawable) {
      const sh = shapes.get(p.id);
      if (!sh || !isDrawable(sh.status)) continue;
      const parts = lineParts(sh.geometry);
      if (!parts.length) continue;
      const approx = sh.status === "approximate";
      const cls = lineClass(p.name);
      features.push({
        type: "Feature",
        geometry: parts.length === 1 ? { type: "LineString", coordinates: parts[0] }
          : { type: "MultiLineString", coordinates: parts },
        properties: {
          id: p.id, name: p.name, cls,
          dashed: LINES_PROVISIONAL || approx || cls === "trail",
          color: LINES_PROVISIONAL ? LINE_COLOR.road : LINE_COLOR[cls],
          label: approx || LINES_PROVISIONAL ? `${p.name} · ${APPROXIMATE_LABEL}` : p.name,
        },
      });
    }
    src.setData({ type: "FeatureCollection", features });
  }, []);

  const setSourceData = useCallback((pins: DestinationPin[]) => {
    const map = mapRef.current;
    if (!map) return;
    const src = map.getSource(SOURCE_ID) as mapboxgl.GeoJSONSource | undefined;
    if (!src) return;
    src.setData({
      type: "FeatureCollection",
      features: pins.filter((p) => !isLine(p)).map((p) => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: [p.lng, p.lat] },
        // AA-847: precision "area" (the model's point, not a geocoded one) is drawn as an area; slice 6d: any
        // precision other than point (or a legacy row) is never an exact pin
        properties: { id: p.id, name: p.name, count: p.component_count, area: !isExactPin(p) },
      })),
    });
    linesRef.current = pins.filter(isLine);
    void drawLines();
    setAttribution(needsGeocodeAttribution(pins) || linesRef.current.length > 0);
  }, [drawLines]);

  // One loader per map: in-memory tile cache (per filter set), at most 4 requests in flight, the
  // previous view's requests aborted on a new view, and a request budget as a loop guard.
  // The fetcher gets the filters load() was called with, so a tile is never cached under a
  // scope built from different filters than the request used.
  const loaderRef = useRef<TileLoader<DestinationPin, BrowseFilters> | null>(null);
  if (loaderRef.current === null) {
    loaderRef.current = new TileLoader<DestinationPin, BrowseFilters>(
      (id, signal, f) => fetchTile(id, f, signal),
      {
        concurrency: 4,
        onBudgetExceeded: () => {
          console.warn("[map] tile request budget reached; skipping tile loads for a few seconds");
        },
      },
    );
  }

  // Scope (filter query string) of the pins currently drawn, and the pending one-shot retry.
  const drawnScopeRef = useRef<string | null>(null);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // AA-906: the one-shot retry calls `run` (same closure), not `refreshVisibleTiles` itself — a
  // callback must not reference its own binding before it is declared (React Compiler lint).
  const refreshVisibleTiles = useCallback(
    (opts?: { retry?: boolean }) => {
      const run = async (o?: { retry?: boolean }): Promise<void> => {
        const map = mapRef.current;
        const loader = loaderRef.current;
        if (!map || !loader) return;
        if (retryTimerRef.current) {
          clearTimeout(retryTimerRef.current);
          retryTimerRef.current = null;
        }
        // In search mode the map shows the ranked results, not tiles — don't let
        // a pan/zoom overwrite them.
        if (searchResultsRef.current) return;
        const bounds = map.getBounds();
        if (!bounds) return;
        const plan = planTiles({
          west: bounds.getWest(),
          south: bounds.getSouth(),
          east: bounds.getEast(),
          north: bounds.getNorth(),
        });
        const f = filtersRef.current;
        const scope = tileScope(f);
        const res = await loader.loadView(plan.ids, scope, f);
        // null: a newer view superseded this one; keep the pins that view will draw.
        if (res === null || searchResultsRef.current) return;
        // Dedup by destination id across tiles. A partial result under the same filters is merged
        // into the pins already drawn instead of replacing them, so a failed tile or an exhausted
        // budget never blanks the map.
        const merged = pinsRef.current;
        if (res.complete || drawnScopeRef.current !== scope) merged.clear();
        for (const p of res.pins) merged.set(p.id, p);
        drawnScopeRef.current = scope;
        setSourceData(Array.from(merged.values()));
        if (!res.complete && o?.retry !== true) {
          retryTimerRef.current = setTimeout(
            () => {
              retryTimerRef.current = null;
              void run({ retry: true });
            },
            Math.max(PARTIAL_RETRY_MS, res.retryAfterMs),
          );
        }
      };
      return run(opts);
    },
    [setSourceData],
  );

  useEffect(() => {
    if (!hasMapboxToken() || !containerRef.current || mapRef.current) return;
    mapboxgl.accessToken = MAPBOX_TOKEN;
    const map = new mapboxgl.Map({
      container: containerRef.current,
      style: "mapbox://styles/mapbox/outdoors-v12",
      center: [105, 15],
      zoom: 3,
    });
    mapRef.current = map;

    // Mapbox reports token/style/worker problems via an 'error' event, not
    // a thrown exception — so a broken map leaves the JS console clean.
    map.on("error", (e) => {
      console.error("[mapbox error]", e?.error?.message ?? e);
    });

    // The map is created inside useEffect (after first paint), but Mapbox
    // still frequently measures the container before layout settles and
    // locks the canvas at its 400x300 default. Force a resize on the next
    // animation frames, and keep a ResizeObserver for later layout changes.
    const raf1 = requestAnimationFrame(() => {
      map.resize();
      requestAnimationFrame(() => map.resize());
    });
    const ro = new ResizeObserver(() => map.resize());
    ro.observe(containerRef.current);

    // Debounced: a fitBounds/flyTo or a drag ends with one refresh, not one per moveend.
    let moveTimer: ReturnType<typeof setTimeout> | null = null;
    const onMoveEnd = () => {
      if (moveTimer) clearTimeout(moveTimer);
      moveTimer = setTimeout(() => {
        moveTimer = null;
        void refreshVisibleTiles();
      }, MOVE_DEBOUNCE_MS);
    };

    map.on("load", () => {
      map.resize(); // ensure canvas matches container after first layout

      // --- Selected-country highlight. Uses Mapbox's free country-boundaries
      // tileset so the chosen country gets a soft gold fill + gold outline,
      // making "I picked Laos" visually obvious (not just a camera move).
      // Added first so it renders beneath the destination pins and trip path.
      map.addSource(COUNTRY_SOURCE, {
        type: "vector",
        url: "mapbox://mapbox.country-boundaries-v1",
      });
      map.addLayer({
        id: "country-highlight-fill",
        type: "fill",
        source: COUNTRY_SOURCE,
        "source-layer": "country_boundaries",
        // Start matching nothing; the country effect sets the real filter.
        filter: ["==", ["get", "iso_3166_1"], "__none__"],
        paint: { "fill-color": "#DB9628", "fill-opacity": 0.1 },
      });
      map.addLayer({
        id: "country-highlight-line",
        type: "line",
        source: COUNTRY_SOURCE,
        "source-layer": "country_boundaries",
        filter: ["==", ["get", "iso_3166_1"], "__none__"],
        paint: { "line-color": "#B87A1A", "line-width": 2, "line-opacity": 0.85 },
      });

      // AA-847 slice 6d: line places (rivers, roads, trails) under the pins: white casing, water blue, roads ink
      // solid, trails / approximate routes ink dashed, the name along the line, a wide invisible hit line.
      map.addSource(LINES_SOURCE, {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });
      const lineWidth: mapboxgl.ExpressionSpecification = ["interpolate", ["linear"], ["zoom"], 3, 1.5, 8, 3, 12, 5];
      map.addLayer({
        id: "destination-lines-casing",
        type: "line",
        source: LINES_SOURCE,
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": "#ffffff", "line-width": ["interpolate", ["linear"], ["zoom"], 3, 3.5, 8, 6, 12, 9],
                 "line-opacity": 0.9 },
      });
      map.addLayer({
        id: "destination-lines-solid",
        type: "line",
        source: LINES_SOURCE,
        filter: ["!=", ["get", "dashed"], true],
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": ["get", "color"], "line-width": lineWidth, "line-opacity": 0.9 },
      });
      map.addLayer({
        id: "destination-lines-dashed",
        type: "line",
        source: LINES_SOURCE,
        filter: ["==", ["get", "dashed"], true],
        layout: { "line-join": "round" },
        paint: { "line-color": ["get", "color"], "line-width": lineWidth, "line-opacity": 0.9,
                 "line-dasharray": [2, 1.5] },
      });
      map.addLayer({
        id: "destination-lines-label",
        type: "symbol",
        source: LINES_SOURCE,
        minzoom: 5,
        layout: {
          "symbol-placement": "line",
          "text-field": ["get", "label"],
          "text-size": 11,
          "text-font": ["DIN Offc Pro Medium", "Arial Unicode MS Regular"],
        },
        paint: { "text-color": "#1F2933", "text-halo-color": "#ffffff", "text-halo-width": 1.4 },
      });
      map.addLayer({
        id: "destination-lines-hit",
        type: "line",
        source: LINES_SOURCE,
        paint: { "line-color": "#000000", "line-width": 14, "line-opacity": 0 },
      });

      map.addSource(SOURCE_ID, {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
        cluster: true,
        clusterRadius: 50,
      });
      // Colour system (deliberate, so the three roles never blur together):
      //   ink  (#1F2933) = "browse / not yet picked"  -> clusters + single pins
      //   gold (#DB9628) = "in your trip"             -> numbered day stops + route
      map.addLayer({
        id: "clusters",
        type: "circle",
        source: SOURCE_ID,
        filter: ["has", "point_count"],
        paint: {
          "circle-color": "#1F2933",
          "circle-radius": ["step", ["get", "point_count"], 16, 10, 22, 30, 28],
          "circle-opacity": 0.88,
          "circle-stroke-width": 2,
          "circle-stroke-color": "#ffffff",
        },
      });
      map.addLayer({
        id: "cluster-count",
        type: "symbol",
        source: SOURCE_ID,
        filter: ["has", "point_count"],
        layout: {
          "text-field": ["get", "point_count_abbreviated"],
          "text-size": 12,
        },
        paint: { "text-color": "#ffffff" },
      });
      // AA-847 (ADR 0007): a place located only approximately (precision "area") is a translucent area that
      // grows with the zoom, with no pin in the middle: it never reads as an exact spot.
      map.addLayer({
        id: "unclustered-area",
        type: "circle",
        source: SOURCE_ID,
        filter: ["all", ["!", ["has", "point_count"]], ["==", ["get", "area"], true]],
        paint: {
          "circle-color": "#1F2933",
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 3, 10, 8, 26, 12, 60],
          "circle-opacity": 0.16,
          "circle-stroke-width": 1.5,
          "circle-stroke-color": "#1F2933",
          "circle-stroke-opacity": 0.55,
        },
      });
      map.addLayer({
        id: "unclustered",
        type: "circle",
        source: SOURCE_ID,
        filter: ["all", ["!", ["has", "point_count"]], ["!=", ["get", "area"], true]],
        paint: {
          "circle-color": "#1F2933",
          "circle-radius": 7,
          "circle-opacity": 0.85,
          "circle-stroke-width": 2,
          "circle-stroke-color": "#ffffff",
        },
      });

      // --- Trip path: a solid gold route (real roads via Directions, else a
      // straight fallback) connecting the pinned components in day order, plus
      // numbered day markers. Gold = "in your trip" (distinct from ink browse).
      map.addSource(TRIP_LINE_SOURCE, {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });
      map.addSource(TRIP_STOP_SOURCE, {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });
      // A soft white casing under a solid gold line reads cleanly over the
      // map (like a highlighted route).
      map.addLayer({
        id: "trip-line-casing",
        type: "line",
        source: TRIP_LINE_SOURCE,
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": "#ffffff", "line-width": 7, "line-opacity": 0.9 },
      });
      map.addLayer({
        id: "trip-line",
        type: "line",
        source: TRIP_LINE_SOURCE,
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          // SOLID gold: this source only holds REAL road-following geometry
          // (from Directions) for legs that are genuinely drivable.
          "line-color": "#B87A1A",
          "line-width": 4,
          "line-opacity": 0.95,
        },
      });
      // Flight/transfer legs: a faint dashed arc between stops that aren't
      // sensibly road-connected. Deliberately understated so it doesn't read
      // as a drawn road — it just links the day order.
      map.addSource(TRIP_FLIGHT_SOURCE, {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });
      map.addLayer({
        id: "trip-flight-line",
        type: "line",
        source: TRIP_FLIGHT_SOURCE,
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": "#9AA5B1",
          "line-width": 1.75,
          "line-opacity": 0.7,
          "line-dasharray": [1.5, 2],
        },
      });
      // AA-847 slice 6d: the day's stretch of a river / road / trail between two stops, gold like the trip, dashed
      // and labelled "approximate route" while the line has no geometry yet.
      map.addSource(TRIP_FEATURE_SOURCE, {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });
      map.addLayer({
        id: "trip-feature-casing",
        type: "line",
        source: TRIP_FEATURE_SOURCE,
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": "#ffffff", "line-width": 7, "line-opacity": 0.9 },
      });
      map.addLayer({
        id: "trip-feature-line",
        type: "line",
        source: TRIP_FEATURE_SOURCE,
        filter: ["!=", ["get", "approx"], true],
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": TRIP_FEATURE_COLOR, "line-width": 4, "line-opacity": 0.95 },
      });
      map.addLayer({
        id: "trip-feature-approx",
        type: "line",
        source: TRIP_FEATURE_SOURCE,
        filter: ["==", ["get", "approx"], true],
        layout: { "line-join": "round" },
        paint: { "line-color": TRIP_FEATURE_COLOR, "line-width": 3, "line-opacity": 0.9, "line-dasharray": [2, 1.5] },
      });
      map.addLayer({
        id: "trip-feature-label",
        type: "symbol",
        source: TRIP_FEATURE_SOURCE,
        layout: {
          "symbol-placement": "line",
          "text-field": ["get", "label"],
          "text-size": 11,
          "text-font": ["DIN Offc Pro Medium", "Arial Unicode MS Regular"],
        },
        paint: { "text-color": "#7A5210", "text-halo-color": "#ffffff", "text-halo-width": 1.4 },
      });
      map.addLayer({
        id: "trip-stop-dot",
        type: "circle",
        source: TRIP_STOP_SOURCE,
        paint: {
          "circle-color": "#DB9628",
          "circle-radius": 13,
          "circle-stroke-width": 3.5,
          "circle-stroke-color": "#ffffff",
        },
      });
      map.addLayer({
        id: "trip-stop-label",
        type: "symbol",
        source: TRIP_STOP_SOURCE,
        layout: {
          "text-field": ["get", "day"],
          "text-size": 12,
          "text-font": ["DIN Offc Pro Bold", "Arial Unicode MS Bold"],
          "text-allow-overlap": true,
        },
        paint: { "text-color": "#ffffff" },
      });

      // --- Gateway airports: arrival (fly in, near the first stop) and
      // departure (fly out, near the last stop). Drawn as a small ✈ marker so
      // the map shows the WHOLE journey — the flight in, the route, the flight
      // out — matching the "Getting there / Heading home" panel copy.
      map.addSource(TRIP_GATEWAY_SOURCE, {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });
      map.addLayer({
        id: "trip-gateway-dot",
        type: "circle",
        source: TRIP_GATEWAY_SOURCE,
        paint: {
          "circle-color": "#ffffff",
          "circle-radius": 11,
          "circle-stroke-width": 2,
          "circle-stroke-color": "#9AA5B1",
        },
      });
      map.addLayer({
        id: "trip-gateway-icon",
        type: "symbol",
        source: TRIP_GATEWAY_SOURCE,
        layout: {
          "text-field": "✈",
          "text-size": 13,
          "text-allow-overlap": true,
        },
        paint: { "text-color": "#5A6572" },
      });
      map.addLayer({
        id: "trip-gateway-label",
        type: "symbol",
        source: TRIP_GATEWAY_SOURCE,
        layout: {
          "text-field": ["get", "label"],
          "text-size": 10,
          "text-offset": [0, 1.4],
          "text-anchor": "top",
          "text-font": ["DIN Offc Pro Medium", "Arial Unicode MS Regular"],
          "text-optional": true,
        },
        paint: {
          "text-color": "#5A6572",
          "text-halo-color": "#ffffff",
          "text-halo-width": 1.2,
        },
      });

      // AA-674: a proposed route, drawn as a dashed ink line with numbered day dots.
      map.addSource(ROUTE_PREVIEW_SOURCE, {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });
      map.addLayer({
        id: "route-preview-line",
        type: "line",
        source: ROUTE_PREVIEW_SOURCE,
        filter: ["==", ["geometry-type"], "LineString"],
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": "#1F2A37", "line-width": 3, "line-dasharray": [1.5, 1.5], "line-opacity": 0.85 },
      });
      map.addLayer({
        id: "route-preview-stops",
        type: "circle",
        source: ROUTE_PREVIEW_SOURCE,
        filter: ["==", ["geometry-type"], "Point"],
        paint: {
          "circle-radius": 9,
          "circle-color": "#1F2A37",
          "circle-stroke-color": "#ffffff",
          "circle-stroke-width": 2,
        },
      });
      map.addLayer({
        id: "route-preview-labels",
        type: "symbol",
        source: ROUTE_PREVIEW_SOURCE,
        filter: ["==", ["geometry-type"], "Point"],
        layout: {
          "text-field": ["get", "label"],
          "text-size": 10,
          "text-font": ["DIN Offc Pro Medium", "Arial Unicode MS Regular"],
          "text-allow-overlap": true,
        },
        paint: { "text-color": "#ffffff" },
      });

      for (const layer of ["unclustered", "unclustered-area", "destination-lines-hit"]) {
        map.on("click", layer, (e) => {
          const f = e.features?.[0];
          const id = f?.properties?.id as string | undefined;
          if (id) setSelected(id);
        });
        map.on("mouseenter", layer, () => {
          map.getCanvas().style.cursor = "pointer";
        });
        map.on("mouseleave", layer, () => {
          map.getCanvas().style.cursor = "";
        });
      }
      map.on("click", "clusters", (e) => {
        const f = map.queryRenderedFeatures(e.point, { layers: ["clusters"] })[0];
        const clusterId = f.properties?.cluster_id;
        const src = map.getSource(SOURCE_ID) as mapboxgl.GeoJSONSource;
        src.getClusterExpansionZoom(clusterId, (err, zoom) => {
          if (err || zoom == null) return;
          map.easeTo({
            center: (f.geometry as GeoJSON.Point).coordinates as [number, number],
            zoom,
            ...camReduced(),
          });
        });
      });
      map.on("moveend", onMoveEnd);
      // a line's detail level follows the zoom (light below 7, detailed from 7)
      map.on("zoomend", () => {
        if (shapeLevel(map.getZoom()) !== lineLevelRef.current) void drawLines();
      });
      refreshVisibleTiles();
    });

    return () => {
      cancelAnimationFrame(raf1);
      if (moveTimer) clearTimeout(moveTimer);
      if (retryTimerRef.current) clearTimeout(retryTimerRef.current);
      retryTimerRef.current = null;
      loaderRef.current?.abort();
      ro.disconnect();
      map.remove();
      mapRef.current = null;
    };
  }, [refreshVisibleTiles, drawLines]);

  // Re-query when filters change (browse mode only).
  useEffect(() => {
    if (searchResults) return; // filters re-apply via a fresh search instead
    const map = mapRef.current;
    if (!map || !map.isStyleLoaded()) return;
    refreshVisibleTiles();
  }, [filters, refreshVisibleTiles, searchResults]);

  // Drive the country polygon highlight. A hover-preview (previewCountry) wins
  // over the committed selection (filters.country) so hovering a country in
  // the picker gives an instant "this is where you'd go" cue without changing
  // the trip. Clearing the preview falls back to the selected country.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.isStyleLoaded()) return;
    const active = previewCountry ?? filters.country ?? null;
    const iso = active ? COUNTRY_ISO[active] ?? "__none__" : "__none__";
    const f: mapboxgl.FilterSpecification = ["==", ["get", "iso_3166_1"], iso];
    if (map.getLayer("country-highlight-fill")) map.setFilter("country-highlight-fill", f);
    if (map.getLayer("country-highlight-line")) map.setFilter("country-highlight-line", f);
  }, [previewCountry, filters.country]);

  // When a COUNTRY is picked (country-first filter), fly the map to that
  // country and show its destinations — otherwise selecting a country only
  // filters within the current viewport, which looks like "nothing happened".
  useEffect(() => {
    if (searchResults) return; // search takes precedence
    const map = mapRef.current;
    if (!map || !map.isStyleLoaded()) return;

    const country = filters.country;
    if (!country) {
      return; // highlight handled by the dedicated effect below; refresh tiles elsewhere
    }

    let cancelled = false;
    fetchByCountry(country).then((pins) => {
      if (cancelled || !mapRef.current) return;
      setSourceData(pins);
      // Fit to the country. A known data issue: some places are mis-geocoded
      // onto the wrong continent, which would blow the camera out to a world
      // view. Clamp the fit to the country's approximate bbox so outliers
      // don't drag the camera; if none fall inside, fall back to all pins.
      const bbox = COUNTRY_BBOX[country];
      const inBox = bbox
        ? pins.filter(
            (p) =>
              p.lng >= bbox[0] &&
              p.lng <= bbox[2] &&
              p.lat >= bbox[1] &&
              p.lat <= bbox[3],
          )
        : pins;
      const fitPins = inBox.length > 0 ? inBox : pins;
      if (fitPins.length > 0) {
        const b = new mapboxgl.LngLatBounds();
        for (const p of fitPins) extendBy(b, p);
        map.fitBounds(b, { padding: 80, maxZoom: 8, duration: camMs(700) });
      }
    });
    return () => {
      cancelled = true;
    };
  }, [filters.country, searchResults, setSourceData]);

  // Render semantic-search results (or return to tiles when cleared).
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.isStyleLoaded()) return;
    if (searchResults === null) {
      // Back to browse mode: repopulate from the current viewport.
      refreshVisibleTiles();
      return;
    }
    setSourceData(searchResults);
    if (searchResults.length > 0) {
      const b = new mapboxgl.LngLatBounds();
      for (const p of searchResults) extendBy(b, p); // a river result frames its whole bbox
      map.fitBounds(b, { padding: 80, maxZoom: 9, duration: camMs(600) });
    }
  }, [searchResults, refreshVisibleTiles, setSourceData]);

  // Draw the trip path (day-ordered line + numbered stops) from the itinerary.
  // AA-847 slice 6d: a river / road / trail day is not a stop; its stretch between the two stops is a gold "feature"
  // leg that replaces the road / flight leg between them (lib/lines.ts planTrip).
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.isStyleLoaded()) return;
    const lineSrc = map.getSource(TRIP_LINE_SOURCE) as
      | mapboxgl.GeoJSONSource
      | undefined;
    const stopSrc = map.getSource(TRIP_STOP_SOURCE) as
      | mapboxgl.GeoJSONSource
      | undefined;
    const featureSrc = map.getSource(TRIP_FEATURE_SOURCE) as mapboxgl.GeoJSONSource | undefined;
    if (!lineSrc || !stopSrc) return;

    const plan = planTrip(itinerary);
    // Day number of each stop (the first day it is reached), for the numbered markers.
    const dayOf = new Map(itinerary.map((d) => [d.component_id, d.day]));
    stopSrc.setData({
      type: "FeatureCollection",
      features: plan.stops.map((p) => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: p.coord },
        properties: { day: String(dayOf.get(p.componentId) ?? "") },
      })),
    });

    const flightSrc = map.getSource(TRIP_FLIGHT_SOURCE) as
      | mapboxgl.GeoJSONSource
      | undefined;
    const gatewaySrc = map.getSource(TRIP_GATEWAY_SOURCE) as
      | mapboxgl.GeoJSONSource
      | undefined;

    const stops = plan.stops.map((p) => p.coord);

    // Arrival/departure gateways: fly into the gateway nearest the first stop,
    // fly out from the gateway nearest the last stop. Draw a ✈ marker at each
    // and a dashed arc linking it to the adjacent stop, so the map tells the
    // full story (flight in -> route -> flight out). A one-stop trip still
    // gets both a fly-in and fly-out cue. When the same gateway serves both
    // ends we show it once.
    const first = stops[0];
    const last = stops[stops.length - 1];
    const arrivalGw = first ? nearestGateway(first) : null;
    const departureGw = last ? nearestGateway(last) : null;
    const gatewayFeatures: GeoJSON.Feature[] = [];
    const gatewayArcs: [number, number][][] = [];
    const seenGateway = new Set<string>();
    const addGateway = (
      gw: { gw: { iata: string; city: string; lng: number; lat: number }; km: number } | null,
      stop: [number, number] | undefined,
      role: string,
    ) => {
      if (!gw || !stop) return;
      const coord: [number, number] = [gw.gw.lng, gw.gw.lat];
      // Link the airport to the stop unless they're effectively the same point.
      if (gw.km > 1) gatewayArcs.push(greatCircleSegment(coord, stop));
      if (!seenGateway.has(gw.gw.iata)) {
        seenGateway.add(gw.gw.iata);
        gatewayFeatures.push({
          type: "Feature",
          geometry: { type: "Point", coordinates: coord },
          properties: { label: `${gw.gw.iata} · ${role}`, iata: gw.gw.iata },
        });
      }
    };
    addGateway(arrivalGw, first, "arrive");
    addGateway(departureGw, last, "depart");
    gatewaySrc?.setData({
      type: "FeatureCollection",
      features: gatewayFeatures,
    });

    // Stretches that replace no leg (a window around one stop) are drawn right away.
    const stretchFeature = (geometry: [number, number][], label: string, approx: boolean): GeoJSON.Feature => ({
      type: "Feature",
      geometry: { type: "LineString", coordinates: geometry },
      properties: { label, approx },
    });
    const extraFeatures = plan.extras.map((x) => stretchFeature(x.geometry, formatStretch(x).replace(/^〰 /, ""), false));
    featureSrc?.setData({ type: "FeatureCollection", features: extraFeatures });

    if (stops.length < 2) {
      lineSrc.setData({ type: "FeatureCollection", features: [] });
      // Even a single-stop trip shows its fly-in/out arcs.
      flightSrc?.setData({
        type: "FeatureCollection",
        features: gatewayArcs.map((coords) => ({
          type: "Feature" as const,
          geometry: { type: "LineString" as const, coordinates: coords },
          properties: {},
        })),
      });
      return;
    }

    // Resolve each planned leg: a road leg (real road geometry), a flight leg (dashed arc) or a feature leg along a
    // line (no Directions request). We never draw a straight line pretending to be a road.
    let cancelled = false;
    fetchTripLegs(plan).then((legs) => {
      if (cancelled) return;
      const road = map.getSource(TRIP_LINE_SOURCE) as mapboxgl.GeoJSONSource | undefined;
      const flight = map.getSource(TRIP_FLIGHT_SOURCE) as mapboxgl.GeoJSONSource | undefined;
      const feature = map.getSource(TRIP_FEATURE_SOURCE) as mapboxgl.GeoJSONSource | undefined;
      if (!road || !flight) return;
      const all = legs.flatMap((l) => [l, ...(l.joins ?? [])]);

      const roadFeatures = all
        .filter((l) => l.mode === "road" && l.geometry.length >= 2)
        .map((l) => ({
          type: "Feature" as const,
          geometry: { type: "LineString" as const, coordinates: l.geometry },
          properties: {},
        }));
      const flightFeatures = all
        .filter((l) => l.mode === "flight")
        .map((l) => ({
          type: "Feature" as const,
          geometry: {
            type: "LineString" as const,
            coordinates: greatCircleSegment(l.from, l.to),
          },
          properties: {},
        }));
      const featureLegs = legs
        .filter((l) => l.mode === "feature" && l.stretch && l.geometry.length >= 2)
        .map((l) => stretchFeature(l.geometry, formatStretch(l.stretch!).replace(/^〰 /, ""), l.stretch!.provisional));

      // Prepend the gateway fly-in/out arcs so the whole journey is dashed
      // consistently (airport -> first stop, last stop -> airport).
      const gatewayArcFeatures = gatewayArcs.map((coords) => ({
        type: "Feature" as const,
        geometry: { type: "LineString" as const, coordinates: coords },
        properties: {},
      }));

      road.setData({ type: "FeatureCollection", features: roadFeatures });
      flight.setData({
        type: "FeatureCollection",
        features: [...gatewayArcFeatures, ...flightFeatures],
      });
      feature?.setData({ type: "FeatureCollection", features: [...extraFeatures, ...featureLegs] });
    });
    return () => {
      cancelled = true;
    };
  }, [itinerary]);

  // AA-674: draw (or clear) the previewed AA route and frame it.
  useEffect(() => {
    const map = mapRef.current;
    const src = map?.getSource(ROUTE_PREVIEW_SOURCE) as mapboxgl.GeoJSONSource | undefined;
    if (!map || !src) return;
    const pts = (routePreview?.days ?? []).filter(
      (d) => typeof d.lat === "number" && typeof d.lng === "number",
    );
    if (pts.length === 0) {
      src.setData({ type: "FeatureCollection", features: [] });
      return;
    }
    // One dot per stop (first day it is reached), labelled with that day number.
    const stops: { day: number; coord: [number, number] }[] = [];
    for (const d of pts) {
      const coord: [number, number] = [d.lng as number, d.lat as number];
      const last = stops[stops.length - 1];
      if (!last || last.coord[0] !== coord[0] || last.coord[1] !== coord[1]) stops.push({ day: d.day, coord });
    }
    const line: [number, number][] = [];
    for (let i = 0; i < stops.length - 1; i++) {
      const seg = greatCircleSegment(stops[i].coord, stops[i + 1].coord, 8);
      line.push(...(i === 0 ? seg : seg.slice(1)));
    }
    src.setData({
      type: "FeatureCollection",
      features: [
        ...(line.length > 1
          ? [{ type: "Feature" as const, properties: {}, geometry: { type: "LineString" as const, coordinates: line } }]
          : []),
        ...stops.map((st) => ({
          type: "Feature" as const,
          properties: { label: String(st.day) },
          geometry: { type: "Point" as const, coordinates: st.coord },
        })),
      ],
    });
    const b = new mapboxgl.LngLatBounds(stops[0].coord, stops[0].coord);
    for (const st of stops) b.extend(st.coord);
    map.fitBounds(b, { padding: 80, maxZoom: 9, duration: camMs(600) });
  }, [routePreview]);

  // Open a destination's popup when something (e.g. a suggestion click) asks
  // to focus it, then clear the request. The user still picks a specific
  // component from the popup (never a whole-destination add).
  // AA-906 (React Compiler lint, no setState in an effect body): opening the popup is the "adjust
  // state when a prop changes" pattern (during render, once per new request); the effect keeps the
  // external part only (camera move) and hands the request back.
  const [focusSeen, setFocusSeen] = useState<string | null>(null);
  if (focusDestinationId !== focusSeen) {
    setFocusSeen(focusDestinationId);
    if (focusDestinationId) setSelected(focusDestinationId);
  }
  useEffect(() => {
    if (!focusDestinationId) return;
    const map = mapRef.current;
    const pin = pinsRef.current.get(focusDestinationId);
    if (map && pin) {
      map.easeTo({ center: [pin.lng, pin.lat], zoom: Math.max(map.getZoom(), 7), ...camReduced() });
    }
    focusDestination(null);
  }, [focusDestinationId, focusDestination]);

  if (!hasMapboxToken()) {
    return (
      <div className="absolute inset-0 flex items-center justify-center bg-k-neutral-bg text-center text-k-muted">
        <div className="max-w-sm rounded-2xl border border-k-line bg-k-card p-6 shadow-aa">
          <p className="font-semibold text-k-ink">Map token not set</p>
          <p className="mt-1 text-sm">
            Add NEXT_PUBLIC_MAPBOX_TOKEN to frontend/.env.local to load the map.
          </p>
        </div>
      </div>
    );
  }

  return (
    <>
      {/* Inline style (not Tailwind) so the map container always has a
          real, positioned box — independent of any Tailwind
          purge/config. Mapbox measures this element to size its canvas. */}
      <div
        ref={containerRef}
        style={{ position: "absolute", inset: 0, width: "100%", height: "100%" }}
      />
      {(attribution || needsGeocodeAttribution(itinerary ?? [])) && (
        <GeocodeAttribution className="absolute bottom-8 left-2 z-10" />
      )}
      {selected && (
        <DestinationPopup
          destinationId={selected}
          onClose={() => setSelected(null)}
        />
      )}
    </>
  );
}
