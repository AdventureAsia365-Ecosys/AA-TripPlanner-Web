// AA-1041 (R7 slice 1, items 1 and 3) — map / list sync and stop / day photos (lib/stopSync.ts).
import { test } from "node:test";
import assert from "node:assert/strict";
import { activeStop, dayPhoto, markerLabel, markerOf, markerStops, photoKey, stopPhoto, type StopInfo } from "./stopSync.ts";

const itin = [
  { component_id: "a", day: 1, name: "Dravin harbour", lat: 10, lng: 100 },
  { component_id: "b", day: 1, name: "Dravin market", lat: 10, lng: 100 },
  { component_id: "r", day: 2, name: "Zel River", lat: 11, lng: 101, precision: "line" },
  { component_id: "c", day: 3, name: "Pell hills", lat: 12, lng: 102 },
  { component_id: "x", day: 4, name: "No point yet" },
];

test("activeStop: hover wins, then the selection", () => {
  assert.equal(activeStop("a", { componentId: "c", from: "map", seq: 1 }), "a");
  assert.equal(activeStop(null, { componentId: "c", from: "map", seq: 1 }), "c");
  assert.equal(activeStop(null, null), null);
});

test("markerStops: one marker per place visit, no marker for a line or a stop without a point", () => {
  const ms = markerStops(itin);
  assert.deepEqual(ms.map((m) => [m.key, m.componentIds, m.day]), [["a", ["a", "b"], 1], ["c", ["c"], 3]]);
  assert.equal(markerOf(ms, "b")?.key, "a");
  assert.equal(markerOf(ms, "r"), null);
  assert.equal(markerOf(ms, null), null);
  assert.equal(markerLabel(ms[0]), "Day 1: Dravin harbour and 1 more");
  assert.equal(markerLabel(ms[1]), "Day 3: Pell hills");
});

test("stop and day photos fall back to null (the icon tile)", () => {
  const info = new Map<string, StopInfo>([
    ["a", { id: "a", photo_url: null, photo_url_small: null }],
    ["b", { id: "b", photo_url: "https://cis.example/p/b?size=large", photo_url_small: "https://cis.example/p/b?size=small" }],
  ]);
  assert.equal(stopPhoto(info, "a"), null);
  assert.equal(stopPhoto(info, "b"), "https://cis.example/p/b?size=small");
  assert.equal(stopPhoto(info, "zz"), null);
  assert.equal(dayPhoto(info, ["a", "b"]), "https://cis.example/p/b?size=small");
  assert.equal(dayPhoto(info, ["a"]), null);
});

test("photoKey is one sorted id set (a reorder does not refetch)", () => {
  assert.equal(photoKey([{ component_id: "c" }, { component_id: "a" }, { component_id: "c" }]), "a,c");
});
