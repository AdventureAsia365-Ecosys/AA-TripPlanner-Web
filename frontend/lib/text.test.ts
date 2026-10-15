// AA-812 (S237): a fallback cover is a destination photo and must never carry the tour's title.
// Run: npm test (node --test with type stripping; no extra dependencies).
import { test } from "node:test";
import assert from "node:assert/strict";
import { coverAlt, MONTH_NAMES } from "./text.ts";

test("own cover keeps the tour title as alt text", () => {
  assert.equal(coverAlt({ title: "Hanoi and Ha Long Bay", cover_source: "tour", cover_place: null }), "Hanoi and Ha Long Bay");
});

test("destination and country fallbacks are named as a destination photo", () => {
  for (const cover_source of ["destination", "country"] as const) {
    assert.equal(
      coverAlt({ title: "Mekong Delta by Boat", cover_source, cover_place: "Ha Long Bay" }),
      "Ha Long Bay — destination photo",
    );
  }
});

test("no photo, or a fallback without a place name, falls back to the title", () => {
  assert.equal(coverAlt({ title: "Tour", cover_source: null, cover_place: null }), "Tour");
  assert.equal(coverAlt({ title: "Tour", cover_source: "country", cover_place: null }), "Tour");
});

test("month names map 1-12 to the browse API's month values", () => {
  assert.equal(MONTH_NAMES.length, 12);
  assert.equal(MONTH_NAMES[0], "January");
  assert.equal(MONTH_NAMES[11], "December");
});
