// AA-1160 (S285 live check, scope 3) — an agency page's whole accent family follows the agency's theme.
//
// The kit's brand layer (ui/core/brandTheme.ts, CIS canonical) themes only the tokens a tenant set. The agency app
// lets an agency set two (Brand colour = accent, Text and link colour = accent-deep), so the tokens it did not set
// (accent-soft, accent-tint, accent-border, and every dark-theme token but the accent) kept the kit's gold: the dashed
// "Start building" box, the tinted chips and the dark theme stayed orange under a teal brand. And the navy band's
// accent (--tp-band-accent: the "TRIP PLANNER" label, the AI chip's hover ring, the AI notice icon) is TripPlanner's own
// token, outside the brand layer.
//
// plannerBrandCss(theme) = the kit's block for the theme with the missing accent tokens derived from the accent
// (mixes towards the light surface, a deep shade darkened until it reads), then one block for the dark theme's derived
// accent family, then --tp-band-accent: the accent lightened until it reads on the band (4.5:1, small caps text), in
// both themes. Everything it adds is checked against the same surfaces the kit checks (card, bg, tint, soft); a token
// that cannot be made to read is left out, so the kit's value stays. A theme without a valid accent changes nothing
// beyond the kit's own block. Pure and import-light (the pages render it on the server into one <style> element; no
// quote, angle bracket or ampersand in the output).
// The planner map (Mapbox paint, which reads no CSS variable) gets its own three colours as TripPlanner tokens
// (--tp-map-fill / --tp-map-line / --tp-map-label, lib/mapAccent.ts): MapView reads them when the map loads.
// Follow-up: the derivation belongs in the kit core brand layer (CIS frontend/app/_kit/core/brandTheme.ts) so every
// app gets it; it lives here until that change is made there and synced.
import { BRAND_TOKENS, KIT_DARK_CARD, KIT_LIGHT, brandThemeCss, contrast, onColour, type BrandToken } from "../ui/core/brandTheme.ts";
import { MAP_ACCENT_VARS, type MapAccent } from "./mapAccent.ts";

const HEX = /^#[0-9a-fA-F]{6}$/;
const WHITE = "#ffffff";
const BLACK = "#000000";
/** Kit surfaces (ui/core/tokens.css; lib/tenantBrand.test.ts keeps them equal). */
export const KIT_DARK_BG = "#0f141b";
/** The navy band of the B2C surfaces (ui/tokens.css --tp-band, light and dark). */
export const BAND_LIGHT = "#1f2933";
export const BAND_DARK = "#0a0e13";
const TEXT_MIN = 4.5;
/** Non-text contrast of a control against its surface (WCAG 1.4.11). */
const UI_MIN = 3;

function rgb(hex: string): [number, number, number] {
  return [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)) as [number, number, number];
}

/** `a` moved towards `b` by the share `t` (0 = a, 1 = b), "#rrggbb" lower case. */
export function mix(a: string, b: string, t: number): string {
  const [x, y] = [rgb(a), rgb(b)];
  return `#${x.map((v, i) => Math.round(v + (y[i] - v) * t).toString(16).padStart(2, "0")).join("")}`;
}

/** The first shade of `from` towards `to` (in steps of 5 %) that reads at `min` on every surface, else null. */
export function readableShade(from: string, to: string, surfaces: string[], min = TEXT_MIN): string | null {
  for (let step = 0; step <= 20; step += 1) {
    const c = mix(from, to, step / 20);
    if (surfaces.every((s) => contrast(c, s) >= min)) return c;
  }
  return null;
}

function accentOf(theme: unknown): string | null {
  if (!theme || typeof theme !== "object" || Array.isArray(theme)) return null;
  const a = (theme as Record<string, unknown>).accent;
  return typeof a === "string" && HEX.test(a) ? a.toLowerCase() : null;
}

/** The theme with the light tokens the agency did not set derived from its accent (its own tokens win). */
export function completeTheme(theme: unknown): unknown {
  const accent = accentOf(theme);
  if (!accent) return theme;
  const given = theme as Record<string, unknown>;
  const derived: Partial<Record<BrandToken, string>> = {
    "accent-tint": mix(accent, KIT_LIGHT.card, 0.9),
    "accent-soft": mix(accent, KIT_LIGHT.card, 0.75),
    "accent-border": mix(accent, KIT_LIGHT.card, 0.35),
  };
  const tint = (typeof given["accent-tint"] === "string" ? given["accent-tint"] : derived["accent-tint"]) as string;
  const soft = (typeof given["accent-soft"] === "string" ? given["accent-soft"] : derived["accent-soft"]) as string;
  const deep = readableShade(accent, BLACK, [KIT_LIGHT.card, KIT_LIGHT.bg, tint, soft]);
  if (deep) derived["accent-deep"] = deep;
  const out: Record<string, unknown> = {};
  for (const t of BRAND_TOKENS) {
    if (given[t] !== undefined) out[t] = given[t];
    else if (derived[t]) out[t] = derived[t];
  }
  for (const [k, v] of Object.entries(given)) if (!(k in out)) out[k] = v;   // unknown keys: the kit drops them
  return out;
}

/** The dark theme's accent family derived from the agency's accent (the kit themes only the accent there). */
export function darkFamily(theme: unknown): Partial<Record<BrandToken, string>> {
  const accent = accentOf(theme);
  if (!accent) return {};
  const tint = mix(accent, KIT_DARK_CARD, 0.85);
  const soft = mix(accent, KIT_DARK_CARD, 0.72);
  const out: Partial<Record<BrandToken, string>> = {
    "accent-tint": tint,
    "accent-soft": soft,
    "accent-border": mix(accent, KIT_DARK_CARD, 0.45),
  };
  const deep = readableShade(accent, WHITE, [KIT_DARK_CARD, KIT_DARK_BG, tint, soft]);
  if (deep) out["accent-deep"] = deep;
  // S289 live check: the kit keeps its gold accent in the dark theme when the agency accent is under 3:1 on the dark
  // card (a deep teal), so buttons and the active tab stayed gold. Lighten the accent until it holds 3:1 (WCAG 1.4.11).
  if (contrast(accent, KIT_DARK_CARD) < UI_MIN) {
    const lit = readableShade(accent, WHITE, [KIT_DARK_CARD], UI_MIN);
    if (lit) {
      out.accent = lit;
      out["on-accent"] = onColour(lit);
    }
  }
  return out;
}

/** The band accent (--tp-band-accent) for each theme: the accent lightened until it reads on the band, else none. */
export function bandAccent(theme: unknown): { light: string | null; dark: string | null } {
  const accent = accentOf(theme);
  if (!accent) return { light: null, dark: null };
  return { light: readableShade(accent, WHITE, [BAND_LIGHT]), dark: readableShade(accent, WHITE, [BAND_DARK]) };
}

/** The planner map's colours under the agency's theme (the light outdoors basemap in both themes): the accent as the
 * country fill, the accent a shade deeper for the route, outline and featured legs (AA's own route is its accent 16 %
 * towards black), and the light accent-deep (the agency's or the derived one) for the leg labels on their white halo. */
export function mapAccent(theme: unknown): MapAccent | null {
  const accent = accentOf(theme);
  if (!accent) return null;
  const deep = (completeTheme(theme) as Record<string, unknown>)["accent-deep"];
  const line = mix(accent, BLACK, 0.16);
  const label = typeof deep === "string" && HEX.test(deep) ? deep.toLowerCase()
    : (readableShade(accent, BLACK, [WHITE]) ?? line);
  return { fill: accent, line, label };
}

const LIGHT_SEL = "html:not([data-theme]) body,html[data-theme=light] body";
const DARK_SEL = "html[data-theme=dark] body";

/** One CSS block for an agency page's <style> element (empty string when the theme sets nothing usable). */
export function plannerBrandCss(theme: unknown): string {
  let css = brandThemeCss(completeTheme(theme));
  const dark = darkFamily(theme);
  const darkDecl = BRAND_TOKENS.filter((t) => dark[t]).map((t) => `--aa-${t}:${dark[t]};`).join("");
  if (darkDecl) css += `${DARK_SEL}{${darkDecl}}`;
  const band = bandAccent(theme);
  if (band.light) css += `${LIGHT_SEL}{--tp-band-accent:${band.light};}`;
  if (band.dark) css += `${DARK_SEL}{--tp-band-accent:${band.dark};}`;
  const map = mapAccent(theme);
  if (map) css += `body{${(Object.keys(MAP_ACCENT_VARS) as (keyof MapAccent)[]).map((r) => `${MAP_ACCENT_VARS[r]}:${map[r]};`).join("")}}`;
  return css;
}
