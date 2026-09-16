"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import mapboxgl from "mapbox-gl";
import "mapbox-gl/dist/mapbox-gl.css";
import { fetchByCountry, fetchTile } from "@/lib/api";
import {
  fetchRouteLegs,
  hasMapboxToken,
  MAPBOX_TOKEN,
  nearestGateway,
  tileIdFor,
} from "@/lib/mapbox";
import type { DestinationPin } from "@/lib/types";
import { COUNTRY_BBOX, COUNTRY_ISO } from "@/lib/types";
import { useTrip } from "@/lib/useTrip";
import DestinationPopup from "./DestinationPopup";

const SOURCE_ID = "destinations";
const TRIP_LINE_SOURCE = "trip-line";
const TRIP_FLIGHT_SOURCE = "trip-flight-line";
const TRIP_STOP_SOURCE = "trip-stops";
const TRIP_GATEWAY_SOURCE = "trip-gateways";
const COUNTRY_SOURCE = "country-boundaries";

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

// Enumerate the integer 1° tiles covering the current map bounds.
function tilesForBounds(b: mapboxgl.LngLatBounds): string[] {
  const out: string[] = [];
  const latMin = Math.floor(b.getSouth());
  const latMax = Math.floor(b.getNorth());
  const lngMin = Math.floor(b.getWest());
  const lngMax = Math.floor(b.getEast());
  for (let lat = latMin; lat <= latMax; lat++) {
    for (let lng = lngMin; lng <= lngMax; lng++) {
      out.push(`${lat}_${lng}`);
    }
  }
  return out;
}

export default function MapView() {
  const {
    filters,
    searchResults,
    itinerary,
    focusDestinationId,
    focusDestination,
    previewCountry,
  } = useTrip();
  const mapRef = useRef<mapboxgl.Map | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const pinsRef = useRef<Map<string, DestinationPin>>(new Map());
  const [selected, setSelected] = useState<string | null>(null);
  const filtersRef = useRef(filters);
  filtersRef.current = filters;
  // When search is active, the map shows the ranked result set instead of
  // tiles-by-bounds. Kept in a ref so the moveend handler can bail out.
  const searchResultsRef = useRef(searchResults);
  searchResultsRef.current = searchResults;

  const setSourceData = useCallback((pins: DestinationPin[]) => {
    const map = mapRef.current;
    if (!map) return;
    const src = map.getSource(SOURCE_ID) as mapboxgl.GeoJSONSource | undefined;
    if (!src) return;
    src.setData({
      type: "FeatureCollection",
      features: pins.map((p) => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: [p.lng, p.lat] },
        properties: { id: p.id, name: p.name, count: p.component_count },
      })),
    });
  }, []);

  const refreshVisibleTiles = useCallback(async () => {
    const map = mapRef.current;
    if (!map) return;
    // In search mode the map shows the ranked results, not tiles — don't let
    // a pan/zoom overwrite them.
    if (searchResultsRef.current) return;
    const bounds = map.getBounds();
    if (!bounds) return;
    const tiles = tilesForBounds(bounds);
    const results = await Promise.all(
      tiles.map((t) => fetchTile(t, filtersRef.current)),
    );
    // Dedup by destination id across tiles.
    const merged = pinsRef.current;
    merged.clear();
    for (const pins of results) {
      for (const p of pins) merged.set(p.id, p);
    }
    setSourceData(Array.from(merged.values()));
  }, [setSourceData]);

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
      // eslint-disable-next-line no-console
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
      map.addLayer({
        id: "unclustered",
        type: "circle",
        source: SOURCE_ID,
        filter: ["!", ["has", "point_count"]],
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

      map.on("click", "unclustered", (e) => {
        const f = e.features?.[0];
        const id = f?.properties?.id as string | undefined;
        if (id) setSelected(id);
      });
      map.on("click", "clusters", (e) => {
        const f = map.queryRenderedFeatures(e.point, { layers: ["clusters"] })[0];
        const clusterId = f.properties?.cluster_id;
        const src = map.getSource(SOURCE_ID) as mapboxgl.GeoJSONSource;
        src.getClusterExpansionZoom(clusterId, (err, zoom) => {
          if (err || zoom == null) return;
          map.easeTo({
            center: (f.geometry as GeoJSON.Point).coordinates as [number, number],
            zoom,
          });
        });
      });
      map.on("mouseenter", "unclustered", () => {
        map.getCanvas().style.cursor = "pointer";
      });
      map.on("mouseleave", "unclustered", () => {
        map.getCanvas().style.cursor = "";
      });

      map.on("moveend", refreshVisibleTiles);
      refreshVisibleTiles();
    });

    return () => {
      cancelAnimationFrame(raf1);
      ro.disconnect();
      map.remove();
      mapRef.current = null;
    };
  }, [refreshVisibleTiles]);

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
        for (const p of fitPins) b.extend([p.lng, p.lat]);
        map.fitBounds(b, { padding: 80, maxZoom: 8, duration: 700 });
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
      for (const p of searchResults) b.extend([p.lng, p.lat]);
      map.fitBounds(b, { padding: 80, maxZoom: 9, duration: 600 });
    }
  }, [searchResults, refreshVisibleTiles, setSourceData]);

  // Draw the trip path (day-ordered line + numbered stops) from the itinerary.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.isStyleLoaded()) return;
    const lineSrc = map.getSource(TRIP_LINE_SOURCE) as
      | mapboxgl.GeoJSONSource
      | undefined;
    const stopSrc = map.getSource(TRIP_STOP_SOURCE) as
      | mapboxgl.GeoJSONSource
      | undefined;
    if (!lineSrc || !stopSrc) return;

    // Only days that carry coordinates (the assembly API includes lat/lng).
    const pts = itinerary
      .filter((d) => typeof d.lat === "number" && typeof d.lng === "number")
      .map((d) => ({ day: d.day, coord: [d.lng as number, d.lat as number] as [number, number] }));

    stopSrc.setData({
      type: "FeatureCollection",
      features: pts.map((p) => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: p.coord },
        properties: { day: String(p.day) },
      })),
    });

    const flightSrc = map.getSource(TRIP_FLIGHT_SOURCE) as
      | mapboxgl.GeoJSONSource
      | undefined;
    const gatewaySrc = map.getSource(TRIP_GATEWAY_SOURCE) as
      | mapboxgl.GeoJSONSource
      | undefined;

    // A line needs at least 2 distinct points; dedupe consecutive identical
    // coords (several components can share one destination's coordinate).
    const stops = pts.map((p) => p.coord).filter(
      (c, i, arr) => i === 0 || c[0] !== arr[i - 1][0] || c[1] !== arr[i - 1][1],
    );

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

    // Resolve each consecutive pair into a road leg (real road geometry) or a
    // flight leg (dashed arc). Road legs draw the ACTUAL road; flight legs
    // draw a faint arc — we never draw a straight line pretending to be a road.
    let cancelled = false;
    fetchRouteLegs(stops).then((legs) => {
      if (cancelled || !legs) return;
      const road = map.getSource(TRIP_LINE_SOURCE) as mapboxgl.GeoJSONSource | undefined;
      const flight = map.getSource(TRIP_FLIGHT_SOURCE) as mapboxgl.GeoJSONSource | undefined;
      if (!road || !flight) return;

      const roadFeatures = legs
        .filter((l) => l.mode === "road" && l.geometry.length >= 2)
        .map((l) => ({
          type: "Feature" as const,
          geometry: { type: "LineString" as const, coordinates: l.geometry },
          properties: {},
        }));
      const flightFeatures = legs
        .filter((l) => l.mode === "flight")
        .map((l) => ({
          type: "Feature" as const,
          geometry: {
            type: "LineString" as const,
            coordinates: greatCircleSegment(l.from, l.to),
          },
          properties: {},
        }));

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
    });
    return () => {
      cancelled = true;
    };
  }, [itinerary]);

  // Open a destination's popup when something (e.g. a suggestion click) asks
  // to focus it, then clear the request. The user still picks a specific
  // component from the popup (never a whole-destination add).
  useEffect(() => {
    if (!focusDestinationId) return;
    setSelected(focusDestinationId);
    const map = mapRef.current;
    const pin = pinsRef.current.get(focusDestinationId);
    if (map && pin) {
      map.easeTo({ center: [pin.lng, pin.lat], zoom: Math.max(map.getZoom(), 7) });
    }
    focusDestination(null);
  }, [focusDestinationId, focusDestination]);

  if (!hasMapboxToken()) {
    return (
      <div className="absolute inset-0 flex items-center justify-center bg-aa-sand text-center text-aa-muted">
        <div className="max-w-sm rounded-2xl border border-aa-line bg-white p-6 shadow-aa">
          <p className="font-semibold text-aa-ink">Map token not set</p>
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
      {selected && (
        <DestinationPopup
          destinationId={selected}
          onClose={() => setSelected(null)}
        />
      )}
    </>
  );
}
