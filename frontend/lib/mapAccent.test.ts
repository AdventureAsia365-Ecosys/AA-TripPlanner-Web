// AA-1160 (scope 3, review fix) — the planner map's trip colours follow an agency's theme (lib/mapAccent.ts,
// lib/tenantBrand.ts mapAccent, components/MapView.tsx). Run: npm test.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { MAP_ACCENT_AA, MAP_ACCENT_LAYERS, MAP_ACCENT_VARS, mapAccentFrom } from "./mapAccent.ts";
import { mapAccent, plannerBrandCss } from "./tenantBrand.ts";

// An invented agency brand: a teal accent and a deep teal for text.
const TEAL = { accent: "#1f6f8b", "accent-deep": "#14485a" };
const AA_GOLDS = Object.values(MAP_ACCENT_AA).map((c) => c.toLowerCase());

/** The custom properties of a plannerBrandCss block, as getComputedStyle would answer them on <body>. */
function reader(css: string) {
  return (name: string) => (css.match(new RegExp(`${name}:(#[0-9a-f]{6})`)) ?? [])[1] ?? "";
}

test("without an agency theme the map keeps AA's gold", () => {
  assert.deepEqual(mapAccentFrom(() => ""), MAP_ACCENT_AA);
  assert.deepEqual(mapAccentFrom(() => null), MAP_ACCENT_AA);
  assert.deepEqual(mapAccentFrom(() => "var(--x)"), MAP_ACCENT_AA);                 // not a colour: AA's
  assert.equal(mapAccent({}), null);
  assert.ok(!plannerBrandCss({}).includes("--tp-map-"));
});

test("an agency theme reaches every trip layer of the map, no AA gold left", () => {
  const css = plannerBrandCss(TEAL);
  for (const name of Object.values(MAP_ACCENT_VARS)) assert.ok(css.includes(`${name}:#`), name);
  const accent = mapAccentFrom(reader(css));
  assert.deepEqual(accent, mapAccent(TEAL));
  assert.equal(accent.fill, "#1f6f8b");                                             // the brand colour
  assert.equal(accent.label, "#14485a");                                            // the agency's text colour
  for (const role of ["fill", "line", "label"] as const) {
    assert.ok(!AA_GOLDS.includes(accent[role].toLowerCase()), role);
  }
  // Only the accent given: the label is a derived deep shade that reads on its white halo.
  const derived = mapAccent({ accent: "#1f6f8b" });
  assert.ok(derived && derived.label !== derived.fill && /^#[0-9a-f]{6}$/.test(derived.label));
});

test("MapView paints each trip layer from the map accent, never a gold literal", () => {
  const src = readFileSync(join(import.meta.dirname, "..", "components", "MapView.tsx"), "utf8");
  for (const [id, prop, role] of MAP_ACCENT_LAYERS) {
    const at = src.indexOf(`id: "${id}"`);
    assert.ok(at > 0, id);
    const end = src.indexOf("map.addLayer(", at);
    const block = src.slice(at, end > 0 ? end : undefined);
    assert.ok(block.includes(`"${prop}": accent.${role}`), `${id} ${prop}`);
  }
  for (const gold of Object.values(MAP_ACCENT_AA)) assert.ok(!src.toUpperCase().includes(`"${gold}"`), gold);
});
