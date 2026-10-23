// AA-986 L6: wording of the saved-trip banner and the greyed stops (ADR 0015: retired = "no longer offered",
// paused = temporary, never "gone"). Run: npm test.
import { test } from "node:test";
import assert from "node:assert/strict";
import { reasonText, stopLabel, unavailableBanner } from "./availability.ts";

test("each reason has its own words; an unknown one reads as not available right now", () => {
  assert.equal(reasonText("retired"), "No longer offered");
  assert.equal(reasonText("paused"), "Temporarily unavailable");
  assert.equal(reasonText("stop_removed"), "No longer part of this tour");
  assert.equal(reasonText("not_served"), "Not available right now");
  assert.equal(reasonText("something_new"), "Not available right now");
  assert.equal(reasonText(null), "Not available right now");
});

test("no banner without unavailable stops", () => {
  assert.equal(unavailableBanner([]), null);
});

test("the banner says 'no longer offered' only when every stop is retired", () => {
  assert.match(unavailableBanner([{ reason: "retired" }]) ?? "", /^One stop in this trip is no longer offered/);
  assert.match(unavailableBanner([{ reason: "retired" }, { reason: "retired" }]) ?? "", /^2 stops .* no longer offered/);
  const mixed = unavailableBanner([{ reason: "retired" }, { reason: "paused" }]) ?? "";
  assert.match(mixed, /not available right now/);
  assert.doesNotMatch(mixed, /no longer offered/);
});

test("a stop is named by its saved name, then its place", () => {
  assert.equal(stopLabel({ name: "Old Quarter walk", destination_name: "Hanoi" }), "Old Quarter walk");
  assert.equal(stopLabel({ name: null, destination_name: "Hanoi" }), "Hanoi");
  assert.equal(stopLabel({ name: null, destination_name: null }), "A saved stop");
});
