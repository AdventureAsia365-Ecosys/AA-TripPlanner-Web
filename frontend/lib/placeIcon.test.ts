// AA-1041 (R7 slice 1, item 2) — the icon of a stop (lib/placeIcon.ts). Invented names only.
import { test } from "node:test";
import assert from "node:assert/strict";
import { ACTIVITY_KIND, PLACE_GLYPHS, PLACE_KINDS, kindLabel, kindOfName, legKind, placeKind } from "./placeIcon.ts";

test("about 25 classes, each with at least one glyph path", () => {
  assert.ok(PLACE_KINDS.length >= 25, `${PLACE_KINDS.length} kinds`);
  for (const k of PLACE_KINDS) {
    assert.ok(PLACE_GLYPHS[k]?.length > 0, `glyph of ${k}`);
    for (const d of PLACE_GLYPHS[k]) assert.match(d, /^M[-0-9. ,a-zA-Z]+$/, `${k}: a path d string`);
  }
  assert.deepEqual(Object.keys(PLACE_GLYPHS).sort(), [...PLACE_KINDS].sort());
});

test("every catalog activity enum has a kind", () => {
  for (const a of ["trekking", "cultural_heritage", "wildlife_nature", "water_activities", "culinary",
    "wellness_relaxation", "adventure_sport", "local_immersion"]) {
    assert.ok(PLACE_KINDS.includes(ACTIVITY_KIND[a]), a);
  }
});

test("name words pick the most specific kind", () => {
  assert.equal(kindOfName("Sunrise boat trip to the Floating Market of Zelmora"), "boat");
  assert.equal(kindOfName("Morning market walk in Dravin"), "market");
  assert.equal(kindOfName("Visit the Golden Wat of Pell"), "temple");
  assert.equal(kindOfName("Cooking class with a family"), "culinary");
  assert.equal(kindOfName("Tea estate tour"), "tea");
  assert.equal(kindOfName("Night train to Orvane"), "train");
  assert.equal(kindOfName("Kayaking on Lake Miren"), "boat");
  assert.equal(kindOfName("Lake Miren"), "lake");
  assert.equal(kindOfName("Homestay in Ban Tolu"), "homestay");
  assert.equal(kindOfName("Waterfalls of Kessa"), "waterfall");
  assert.equal(kindOfName(""), null);
  assert.equal(kindOfName("Strolling around"), null);
  // whole words only: "pass" in "passage" is not a mountain pass
  assert.equal(kindOfName("Passage of time"), null);
});

test("placeKind: own name, then place name, then activity, then a pin", () => {
  assert.equal(placeKind({ name: "Old quarter stroll", activity: "trekking" }), "city");
  assert.equal(placeKind({ name: "Free afternoon", destination_name: "Kessa Island", activity: "trekking" }), "island");
  assert.equal(placeKind({ name: "Free afternoon", destination_name: "Kessa", activity: "wellness_relaxation" }), "wellness");
  assert.equal(placeKind({ name: "Free afternoon", activity: "unknown_enum" }), "pin");
  assert.equal(placeKind({}), "pin");
});

test("legKind follows the leg mode and the line class", () => {
  assert.equal(legKind("flight"), "flight");
  assert.equal(legKind("road"), "car");
  assert.equal(legKind("feature", "water"), "boat");
  assert.equal(legKind("feature", "trail"), "trekking");
});

test("kindLabel is readable", () => {
  assert.equal(kindLabel("temple"), "Temple");
  assert.equal(kindLabel("pin"), "Place");
});
