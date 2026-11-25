// Map engine core (AA-948, the root ADR 0016 pattern). Canonical: AA-TripPlanner-Web frontend/lib/map-engine. Edit only there; copies are checked byte for byte (map-engine.lock.json).
//
// layers.ts: the "stops + route" layer set of the shared map engine: one GeoJSON source holding a route line and one
// labelled point per stop (geo.ts routeCollection), drawn as a line, a numbered circle per stop and its label. The
// TripPlanner route preview and the CRM proposal builder both draw it; each passes its own colours (no app colour is
// baked in here). Only a type import of mapbox-gl: the caller owns the map.
import type mapboxgl from "mapbox-gl";

export interface RouteLayerStyle {
  /** Route line colour (a resolved colour: Mapbox paint cannot read CSS variables). */
  lineColor: string;
  /** Stop circle fill. */
  stopColor: string;
  /** Number on the stop. */
  labelColor: string;
  /** Ring around each stop (default white). */
  stopStrokeColor?: string;
  lineWidth?: number;
  lineOpacity?: number;
  /** Dashed (a proposal, an approximate route) or solid. Default dashed. */
  dashed?: boolean;
  stopRadius?: number;
  labelSize?: number;
}

/** The layer ids a route set uses, from its prefix. */
export function routeLayerIds(prefix: string): { line: string; stops: string; labels: string } {
  return { line: `${prefix}-line`, stops: `${prefix}-stops`, labels: `${prefix}-labels` };
}

/** Add the GeoJSON source `sourceId` (empty) and the three layers of a route set. Call once, after the style loaded. */
export function addRouteLayers(map: mapboxgl.Map, sourceId: string, prefix: string, style: RouteLayerStyle): void {
  if (!map.getSource(sourceId)) {
    map.addSource(sourceId, { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  }
  const ids = routeLayerIds(prefix);
  const dashed = style.dashed ?? true;
  map.addLayer({
    id: ids.line,
    type: "line",
    source: sourceId,
    filter: ["==", ["geometry-type"], "LineString"],
    layout: { "line-cap": "round", "line-join": "round" },
    paint: {
      "line-color": style.lineColor,
      "line-width": style.lineWidth ?? 3,
      ...(dashed ? { "line-dasharray": [1.5, 1.5] } : {}),
      "line-opacity": style.lineOpacity ?? 0.85,
    },
  });
  map.addLayer({
    id: ids.stops,
    type: "circle",
    source: sourceId,
    filter: ["==", ["geometry-type"], "Point"],
    paint: {
      "circle-radius": style.stopRadius ?? 9,
      "circle-color": style.stopColor,
      "circle-stroke-color": style.stopStrokeColor ?? "#ffffff",
      "circle-stroke-width": 2,
    },
  });
  map.addLayer({
    id: ids.labels,
    type: "symbol",
    source: sourceId,
    filter: ["==", ["geometry-type"], "Point"],
    layout: {
      "text-field": ["get", "label"],
      "text-size": style.labelSize ?? 10,
      "text-font": ["DIN Offc Pro Medium", "Arial Unicode MS Regular"],
      "text-allow-overlap": true,
    },
    paint: { "text-color": style.labelColor },
  });
}
