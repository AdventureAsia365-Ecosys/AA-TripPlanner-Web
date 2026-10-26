// AA-847 slice 6d — line places: class, pin rule, the trip plan (feature legs replace the road leg), polylines and
// the static map URL length.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  downsample, encodePolyline, formatStretch, isExactPin, isLine, lineClass, lineParts, planTrip, shapeLevel,
  staticPathOverlays, stretchSubtitle, type TripItem,
} from "./lines.ts";
import { staticMapUrl } from "./staticMap.ts";

const MEKONG = { type: "LineString", coordinates: [[100.41, 20.27], [100.75, 20.05], [101.13, 19.89]] };
const HX = "hx-id";
const PB = "pb-id";
const LP = "lp-id";

function item(day: number, id: string, name: string, extra: Partial<TripItem> = {}): TripItem {
  return { day, component_id: id, name, ...extra };
}

test("line class from the name", () => {
  assert.equal(lineClass("Mekong River"), "water");
  assert.equal(lineClass("Nam Ou"), "water");
  assert.equal(lineClass("Karakoram Highway"), "road");
  assert.equal(lineClass("Ho Chi Minh Trail Road"), "road");
  assert.equal(lineClass("Annapurna Circuit"), "trail");
  assert.equal(lineClass("Tea Horse"), "trail");
});

test("only a point (or a legacy row) is an exact pin", () => {
  assert.equal(isExactPin({ precision: "point" }), true);
  assert.equal(isExactPin({}), true);
  assert.equal(isExactPin({ precision: "area" }), false);
  assert.equal(isExactPin({ precision: "line" }), false);
  assert.equal(isExactPin({ precision: "polygon" }), false); // a value a newer CIS might add
  assert.equal(isLine({ precision: "line" }), true);
  assert.equal(shapeLevel(6.9), "lo");
  assert.equal(shapeLevel(7), "mid");
});

test("geometry parts", () => {
  assert.equal(lineParts(MEKONG).length, 1);
  assert.equal(lineParts({ type: "MultiLineString", coordinates: [[[1, 2], [3, 4]], [[5, 6]]] }).length, 1);
  assert.deepEqual(lineParts({ type: "Point", coordinates: [1, 2] }), []);
  assert.deepEqual(lineParts(null), []);
});

test("a river day between two stops replaces the road leg; no Directions pair is left for it", () => {
  const plan = planTrip([
    item(1, "c1", "Huay Xai", { lat: 20.27, lng: 100.41, destination_id: HX, precision: "point" }),
    item(2, "c2", "Mekong River", { lat: 20.0, lng: 100.8, destination_id: "mk", precision: "line", segment: MEKONG,
      segment_basis: "day_anchors", segment_from_destination_id: HX, segment_to_destination_id: PB, segment_km: 88 }),
    item(2, "c3", "Pak Beng", { lat: 19.89, lng: 101.13, destination_id: PB, precision: "point" }),
    item(3, "c4", "Luang Prabang", { lat: 19.89, lng: 102.13, destination_id: LP, precision: "point" }),
  ]);
  assert.deepEqual(plan.stops.map((s) => s.componentId), ["c1", "c3", "c4"]); // the river is no waypoint
  assert.equal(plan.legs.length, 2);
  const [boat, road] = plan.legs;
  assert.equal(boat.kind, "feature");
  assert.equal(road.kind, "pair");
  if (boat.kind === "feature") {
    assert.equal(boat.joinIn, null); // the stretch starts at the stop
    assert.equal(boat.joinOut, null);
    assert.equal(formatStretch(boat.stretch), "〰 Along the Mekong River · ≈ 88 km");
  }
  const directionsRequests = plan.legs.filter((l) => l.kind === "pair").length;
  assert.equal(directionsRequests, 1); // was 2 before (one per consecutive pair): same or fewer
});

test("a line with no geometry yet between two stops is a provisional dashed line, never nothing", () => {
  const plan = planTrip([
    item(1, "c1", "Huay Xai", { lat: 20.27, lng: 100.41, destination_id: HX }),
    item(2, "c2", "Mekong River", { lat: 20.0, lng: 100.8, precision: "line", segment: null, segment_basis: "feature",
      segment_from_destination_id: HX, segment_to_destination_id: PB }),
    item(2, "c3", "Pak Beng", { lat: 19.89, lng: 101.13, destination_id: PB }),
  ]);
  const leg = plan.legs[0];
  assert.equal(leg.kind, "feature");
  if (leg.kind === "feature") {
    assert.equal(leg.stretch.provisional, true);
    assert.deepEqual(leg.stretch.geometry, [[100.41, 20.27], [101.13, 19.89]]);
    assert.equal(formatStretch(leg.stretch), "〰 Mekong River · approximate route");
  }
});

test("joins are drawn only when the stretch ends are more than 2 km from the stops", () => {
  const far = { type: "LineString", coordinates: [[100.5, 20.27], [101.0, 19.95]] }; // ~9 km from both stops
  const plan = planTrip([
    item(1, "c1", "Huay Xai", { lat: 20.27, lng: 100.41, destination_id: HX }),
    item(2, "c2", "Mekong River", { precision: "line", segment: far, segment_basis: "day_anchors",
      segment_from_destination_id: HX, segment_to_destination_id: PB, segment_km: 60 }),
    item(2, "c3", "Pak Beng", { lat: 19.89, lng: 101.13, destination_id: PB }),
  ]);
  const leg = plan.legs[0];
  assert.ok(leg.kind === "feature" && leg.joinIn && leg.joinOut);
});

test("a window around one stop is drawn as an extra stretch and replaces no leg", () => {
  const plan = planTrip([
    item(1, "c1", "Nong Khiaw", { lat: 20.57, lng: 102.61, destination_id: "nk" }),
    item(1, "c2", "Nam Ou", { precision: "line", segment: MEKONG, segment_basis: "anchor_window",
      segment_from_destination_id: "nk", segment_to_destination_id: "nk", segment_km: 30 }),
    item(2, "c3", "Luang Prabang", { lat: 19.89, lng: 102.13, destination_id: LP }),
  ]);
  assert.equal(plan.legs[0].kind, "pair");
  assert.deepEqual(plan.extras.map((s) => s.componentId), ["c2"]);
});

test("list subtitles", () => {
  const names: Record<string, string> = { [HX]: "Huay Xai", [PB]: "Pak Beng" };
  const nameOf = (id: string) => names[id] ?? null;
  assert.equal(stretchSubtitle(item(2, "c", "Mekong River", { precision: "line", segment: MEKONG,
    segment_basis: "day_anchors", segment_from_destination_id: HX, segment_to_destination_id: PB, segment_km: 87.6 }), nameOf),
  "Huay Xai → Pak Beng · ≈ 88 km");
  assert.equal(stretchSubtitle(item(2, "c", "Mekong River", { precision: "line", segment: null,
    segment_basis: "feature" }), nameOf), "Mekong River · approximate route");
  assert.equal(stretchSubtitle(item(2, "c", "Hanoi", { precision: "point" }), nameOf), null);
});

test("polyline encoding (Google reference example)", () => {
  // https://developers.google.com/maps/documentation/utilities/polylinealgorithm
  assert.equal(encodePolyline([[-120.2, 38.5], [-120.95, 40.7], [-126.453, 43.252]]), "_p~iF~ps|U_ulLnnqC_mqNvxq`@");
  assert.equal(downsample([[0, 0], [1, 1], [2, 2], [3, 3], [4, 4]], 3).length, 3);
});

test("static map URL with three dense lines and 25 pins stays under 8,192 characters", () => {
  const dense = (lat0: number) => Array.from({ length: 300 }, (_, i) => [100 + i * 0.01, lat0 + Math.sin(i) * 0.05] as [number, number]);
  const lines = [{ name: "Mekong River", coords: dense(20) }, { name: "Nam Ou", coords: dense(21) },
    { name: "Ho Chi Minh Trail", coords: dense(16) }, { name: "a fourth", coords: dense(18) }];
  assert.equal(staticPathOverlays(lines).length, 3);
  const pts = Array.from({ length: 30 }, (_, i) => ({ name: `p${i}`, lat: 18 + i * 0.1, lng: 101 + i * 0.1, precision: "point" as const }));
  const url = staticMapUrl(pts, { route: true, lines, token: "pk.test-token-0123456789" });
  assert.ok(url && url.length < 8192, `length ${url?.length}`);
  assert.ok(url!.includes("path-4+3B82C4-0.85("));
});

test("a single-pin static map uses center,zoom without padding (Mapbox 422s padding without auto)", () => {
  const one = staticMapUrl([{ name: "Okinawa", lat: 26.6, lng: 127.98, precision: "point" as const }], { token: "pk.test-token-0123456789" });
  assert.ok(one && one.includes("/127.98,26.6,8/"), one ?? "null");
  assert.ok(!one!.includes("padding="));
  const two = staticMapUrl([{ name: "a", lat: 26.6, lng: 127.98, precision: "point" as const },
    { name: "b", lat: 26.2, lng: 127.7, precision: "point" as const }], { token: "pk.test-token-0123456789" });
  assert.ok(two && two.includes("/auto/") && two.includes("?padding=48&access_token="));
});
