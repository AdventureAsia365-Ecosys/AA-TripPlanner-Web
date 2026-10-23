// AA-812 (S237): a fallback cover is a destination photo and must never carry the tour's title.
// Run: npm test (node --test with type stripping; no extra dependencies).
import { test } from "node:test";
import assert from "node:assert/strict";
import { coverAlt, daysText, formatDayRange, formatDays, MONTH_NAMES } from "./text.ts";

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

// AA-837 (D12): "1 DAYS" on one-day tours in the List of tours.
test("formatDays is singular for one day and plural otherwise", () => {
  assert.equal(formatDays(1), "1 day");
  assert.equal(formatDays(2), "2 days");
  assert.equal(formatDays(0), "0 days");
  assert.equal(formatDays(14), "14 days");
});

test("formatDayRange collapses an equal range to one length", () => {
  assert.equal(formatDayRange(1, 1), "1 day");
  assert.equal(formatDayRange(5, 5), "5 days");
  assert.equal(formatDayRange(3, 8), "3 to 8 days");
  assert.equal(formatDayRange(1, 3, "–"), "1–3 days");
});

test("daysText uses formatDays and falls back to the raw text", () => {
  assert.equal(daysText(1), "1 day");
  assert.equal(daysText(7, "7D6N"), "7 days");
  assert.equal(daysText(null, "Half day"), "Half day");
  assert.equal(daysText(0, ""), null);
});
