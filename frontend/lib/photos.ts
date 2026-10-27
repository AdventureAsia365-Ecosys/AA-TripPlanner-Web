// AA-995 — photo rules of the customer pages (spec §3.4, §3.6, §4.1 row 7). Pure functions, no imports, so
// they run in Server Components and in `node --test`.

// The key a cover is compared on: the URL without its query (?size=small|large of the same photo).
export function photoKey(url: string | null | undefined): string | null {
  if (!url) return null;
  return url.split("?")[0];
}

// Duplicate-cover rule (spec §3.4): in one list, a cover already shown earlier is not shown again; that
// card gets the no-photo tile instead (the catalog reuses one Bhutan and one Sri Lanka photo on many
// tours). Returns, per item, whether its photo should be shown.
export function firstUseOfCover<T>(items: T[], cover: (item: T) => string | null | undefined): boolean[] {
  const seen = new Set<string>();
  return items.map((it) => {
    const k = photoKey(cover(it));
    if (!k || seen.has(k)) return false;
    seen.add(k);
    return true;
  });
}

// Destinations grid order (spec §4.1 row 7): tiles with a photo first, then by tour count (desc), then
// by name, so the grid opens on photos and the no-photo tiles sit together at the end.
export function sortForGrid<T extends { country: string; tour_count: number }>(
  items: T[],
  hasPhoto: (item: T) => boolean,
): T[] {
  return [...items].sort(
    (a, b) =>
      Number(hasPhoto(b)) - Number(hasPhoto(a)) || b.tour_count - a.tour_count || a.country.localeCompare(b.country),
  );
}

// The destination grid: photo tiles first, no-photo tiles together at the end. "Photo" means the tile really
// shows one: a cover already used by a bigger country (firstUseOfCover, in tour_count order) counts as none.
export function gridWithPhotos<T extends { country: string; tour_count: number }>(
  items: T[],
  cover: (item: T) => string | null | undefined,
): { items: T[]; shown: boolean[] } {
  const ranked = sortForGrid(items, () => true);
  const firstUse = firstUseOfCover(ranked, cover);
  const shownOf = new Map(ranked.map((it, i) => [it, firstUse[i]]));
  const sorted = sortForGrid(ranked, (it) => shownOf.get(it) ?? false);
  return { items: sorted, shown: sorted.map((it) => shownOf.get(it) ?? false) };
}

export interface RibbonItem {
  // null = a photo without a page of its own (destination gallery): the tile is not a link.
  href: string | null;
  src: string | null;
  alt: string;
  caption: string;
}

// Ribbon content (spec §3.6): skip items without a photo, dedupe by photo.
export function uniqueRibbonItems(items: RibbonItem[]): (RibbonItem & { src: string })[] {
  const keep = firstUseOfCover(items, (i) => i.src);
  return items.filter((_, i) => keep[i]) as (RibbonItem & { src: string })[];
}

// >= 8 unique photos: the ribbon moves; 4–7: a still swipe row; < 4: no ribbon.
export const RIBBON_MIN_STATIC = 4;
export const RIBBON_MIN_ANIMATED = 8;
export type RibbonMode = "animated" | "static" | "none";

export function ribbonMode(uniqueCount: number): RibbonMode {
  if (uniqueCount >= RIBBON_MIN_ANIMATED) return "animated";
  if (uniqueCount >= RIBBON_MIN_STATIC) return "static";
  return "none";
}

// Fill rule (map-first graft): one copy of the moving track must be wider than any screen, so the -50%
// loop never shows a gap. The set is repeated inside copy A until it is >= minWidth px wide at the
// widest tile stride (248 px = 240 tile + 8 gap). The track duration follows the tile count, so the
// speed stays 40–50 px/s whatever the repeat (ui/tokens.css --tp-ribbon-s-per-tile).
export const RIBBON_FILL_PX = 2400;
export const RIBBON_STRIDE_PX = 248;

export function ribbonRepeats(uniqueCount: number, minWidth = RIBBON_FILL_PX, stride = RIBBON_STRIDE_PX): number {
  if (uniqueCount <= 0) return 0;
  return Math.max(1, Math.ceil(minWidth / (uniqueCount * stride)));
}
