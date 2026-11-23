// AA-1160 (scope 3, review fix) — the planner map's "in your trip" colours follow an agency's theme.
//
// Mapbox paint properties never read CSS custom properties, so the brand layer alone left the chosen-country fill and
// outline, the trip route, the featured legs (a river / road / trail stretch of the trip) and their labels in AA gold
// under a teal agency theme, while the day-stop pins (DOM, bg-k-accent) took the agency colour. An agency page's
// <style> block (lib/tenantBrand.ts plannerBrandCss) sets the three TripPlanner tokens below; MapView reads them from
// the computed style of <body> when the map loads and paints the layers with them. AA's own pages set none, so the map
// keeps AA's gold (MAP_ACCENT_AA). The basemap is always the light outdoors style, so one family serves both themes.
// Pure (node --test, lib/mapAccent.test.ts).

const HEX = /^#[0-9a-fA-F]{6}$/;

export type MapAccent = { fill: string; line: string; label: string };

/** AA's map gold: the country fill, the route / outline / featured legs, the featured-leg label text. */
export const MAP_ACCENT_AA: MapAccent = { fill: "#DB9628", line: "#B87A1A", label: "#7A5210" };

/** The custom properties an agency page sets (lib/tenantBrand.ts) for each role. */
export const MAP_ACCENT_VARS: Record<keyof MapAccent, string> = {
  fill: "--tp-map-fill",
  line: "--tp-map-line",
  label: "--tp-map-label",
};

/** The map colours from a property reader (getComputedStyle(document.body).getPropertyValue): each role is the page's
 * value when it is a #rrggbb colour, else AA's. */
export function mapAccentFrom(read: (name: string) => string | null | undefined): MapAccent {
  const out = { ...MAP_ACCENT_AA };
  for (const role of Object.keys(MAP_ACCENT_VARS) as (keyof MapAccent)[]) {
    const value = (read(MAP_ACCENT_VARS[role]) ?? "").trim();
    if (HEX.test(value)) out[role] = value;
  }
  return out;
}

/** The map layers each role paints: [layer id, paint property, role]. MapView builds them with these colours. */
export const MAP_ACCENT_LAYERS: [string, string, keyof MapAccent][] = [
  ["country-highlight-fill", "fill-color", "fill"],
  ["country-highlight-line", "line-color", "line"],
  ["trip-line", "line-color", "line"],
  ["trip-feature-line", "line-color", "line"],
  ["trip-feature-approx", "line-color", "line"],
  ["trip-feature-label", "text-color", "label"],
];
