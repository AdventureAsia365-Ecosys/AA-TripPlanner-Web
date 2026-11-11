// AA-1041 (R7 slice 1, item 4) — reorder of days and stops (lib/itineraryOrder.ts).
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  assignDays,
  dropIndex,
  groupByDay,
  keyTarget,
  moveAnnouncement,
  moveDay,
  moveItem,
  moveStop,
  reorderLocally,
} from "./itineraryOrder.ts";

// Day 1: a (tour T day 1); day 2: b + c (tour T day 2, shared day); day 3: d (no tour day).
const trip = [
  { component_id: "a", day: 1, source_tour_id: "T", source_day_index: 1 },
  { component_id: "b", day: 2, source_tour_id: "T", source_day_index: 2 },
  { component_id: "c", day: 2, source_tour_id: "T", source_day_index: 2 },
  { component_id: "d", day: 3, source_tour_id: null, source_day_index: null },
];

test("groupByDay keeps consecutive stops of one day together", () => {
  assert.deepEqual(groupByDay(trip).map((g) => [g.day, g.stops.map((s) => s.component_id)]), [
    [1, ["a"]], [2, ["b", "c"]], [3, ["d"]],
  ]);
  assert.deepEqual(groupByDay([]), []);
});

test("moveItem clamps and copies", () => {
  const list = ["a", "b", "c"];
  assert.deepEqual(moveItem(list, 0, 2), ["b", "c", "a"]);
  assert.deepEqual(moveItem(list, 2, -5), ["c", "a", "b"]);
  assert.deepEqual(moveItem(list, 9, 0), ["a", "b", "c"]);
  assert.deepEqual(list, ["a", "b", "c"]);
});

test("moveStop moves one component; no change is null", () => {
  assert.deepEqual(moveStop(trip, "d", 0), ["d", "a", "b", "c"]);
  assert.deepEqual(moveStop(trip, "b", 3), ["a", "c", "d", "b"]);
  assert.equal(moveStop(trip, "a", 0), null);
  assert.equal(moveStop(trip, "zz", 1), null);
});

test("moveDay moves every stop of a day together", () => {
  assert.deepEqual(moveDay(trip, 1, 0), ["b", "c", "a", "d"]);
  assert.deepEqual(moveDay(trip, 0, 2), ["b", "c", "d", "a"]);
  assert.equal(moveDay(trip, 1, 1), null);
  assert.equal(moveDay(trip, 7, 0), null);
});

test("assignDays mirrors the assembly rule: a shared tour day only while consecutive", () => {
  const days = assignDays([trip[1], trip[0], trip[2], trip[3]]).map((s) => [s.component_id, s.day]);
  assert.deepEqual(days, [["b", 1], ["a", 2], ["c", 3], ["d", 4]]);   // b and c split: not consecutive any more
  assert.deepEqual(assignDays(trip).map((s) => s.day), [1, 2, 2, 3]);
  // two stops without a tour day never share a day
  assert.deepEqual(assignDays([{ component_id: "x" }, { component_id: "y" }]).map((s) => s.day), [1, 2]);
});

test("reorderLocally redraws the new order with renumbered days and keeps unnamed stops at the end", () => {
  const next = reorderLocally(trip, ["d", "b", "c"]);
  assert.deepEqual(next.map((s) => [s.component_id, s.day]), [["d", 1], ["b", 2], ["c", 2], ["a", 3]]);
});

test("keyTarget: arrows move by one, Home / End to the ends, other keys and no-op moves are null", () => {
  assert.equal(keyTarget("ArrowUp", 2, 4), 1);
  assert.equal(keyTarget("ArrowDown", 2, 4), 3);
  assert.equal(keyTarget("Home", 2, 4), 0);
  assert.equal(keyTarget("End", 2, 4), 3);
  assert.equal(keyTarget("ArrowUp", 0, 4), null);
  assert.equal(keyTarget("ArrowDown", 3, 4), null);
  assert.equal(keyTarget("End", 3, 4), null);
  assert.equal(keyTarget("Enter", 1, 4), null);
});

test("dropIndex: the slot past the last centre the pointer crossed", () => {
  const centres = [10, 30, 50, 70];
  assert.equal(dropIndex(centres, 55, 0), 2);
  assert.equal(dropIndex(centres, 75, 0), 3);
  assert.equal(dropIndex(centres, 25, 3), 1);
  assert.equal(dropIndex(centres, 5, 3), 0);
  assert.equal(dropIndex(centres, 12, 0), 0);   // not past the next centre: stays
  assert.equal(dropIndex(centres, 45, 2), 2);   // over itself: stays
});

test("moveAnnouncement reads the new position", () => {
  assert.equal(moveAnnouncement("Day 3", 0, 4), "Moved Day 3 to position 1 of 4.");
});
