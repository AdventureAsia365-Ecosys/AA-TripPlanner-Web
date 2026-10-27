// AA-837 — WCAG 2.x contrast of the theme tokens (ui/tokens.css), so a token change that breaks
// AA in either theme fails `npm test` before it reaches a page. Pure functions, no imports.

// "#DB9628" / "#fff" → [r, g, b] 0-255; null for anything else (rgba(), var(), names).
export function parseHex(value: string): [number, number, number] | null {
  const m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(value.trim());
  if (!m) return null;
  const h = m[1].length === 3 ? m[1].replace(/./g, (c) => c + c) : m[1];
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)) as [number, number, number];
}

function channel(c: number): number {
  const s = c / 255;
  return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
}

export function luminance([r, g, b]: [number, number, number]): number {
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

// Contrast ratio between two hex colours (1 to 21).
export function contrastRatio(a: string, b: string): number {
  const pa = parseHex(a);
  const pb = parseHex(b);
  if (!pa || !pb) throw new Error(`not a hex colour: ${pa ? b : a}`);
  const [hi, lo] = [luminance(pa), luminance(pb)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

// AA-995: "rgba(15, 20, 27, 0.7)" → [r, g, b, a]; null for anything else.
export function parseRgba(value: string): [number, number, number, number] | null {
  const m = /^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+)\s*)?\)$/i.exec(value.trim());
  if (!m) return null;
  return [Number(m[1]), Number(m[2]), Number(m[3]), m[4] === undefined ? 1 : Number(m[4])];
}

// AA-995: the opaque colour a translucent scrim gives over a solid pixel (a photo's worst case is
// pure white under white text), as "#rrggbb" so it can go into contrastRatio.
export function compositeOver(scrim: string, under: string): string {
  const s = parseRgba(scrim);
  const u = parseHex(under);
  if (!s || !u) throw new Error(`cannot composite ${scrim} over ${under}`);
  const a = s[3];
  const mix = [0, 1, 2].map((i) => Math.round(s[i] * a + u[i] * (1 - a)));
  return `#${mix.map((c) => c.toString(16).padStart(2, "0")).join("")}`;
}

// The `--aa-*` and `--tp-*` custom properties of each theme block in tokens.css:
// `:root, [data-theme="light"] { … }` and `[data-theme="dark"] { … }`. `--aa-ink` is keyed "ink",
// `--tp-band` is keyed "tp-band" (AA-995).
export function readThemeTokens(css: string): { light: Record<string, string>; dark: Record<string, string> } {
  const block = (selector: RegExp): Record<string, string> => {
    const m = selector.exec(css);
    if (!m) throw new Error(`theme block not found: ${selector}`);
    const body = css.slice(m.index + m[0].length, css.indexOf("}", m.index));
    const out: Record<string, string> = {};
    for (const d of body.matchAll(/--(aa|tp)-([\w-]+)\s*:\s*([^;]+);/g)) {
      out[d[1] === "tp" ? `tp-${d[2]}` : d[2]] = d[3].trim();
    }
    return out;
  };
  return {
    light: block(/\[data-theme="light"\]\s*\{/),
    dark: block(/\[data-theme="dark"\]\s*\{/),
  };
}
