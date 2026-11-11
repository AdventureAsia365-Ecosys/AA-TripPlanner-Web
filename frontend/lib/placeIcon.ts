// AA-1041 (R7 slice 1, item 2) — the icon of a planner stop, from its activity and its place / experience name.
//
// The eight activity enums of the catalog (lib/types.ts ACTIVITIES) were the only icons; the benchmark asks for ~25
// classes (place types, the transport of a leg, meals). The class is chosen from words in the stop's own name and its
// place's name (English catalog text), most specific first, then from the activity enum, then a plain pin. It only
// picks a picture: no text is invented and nothing is hidden. The line glyphs below are drawn by
// components/ActivityIcon.tsx (`PlaceIcon`), 24 x 24 viewBox, stroke = currentColor, so the tenant's brand tokens
// colour them. Pure data + functions: node:test covers them (placeIcon.test.ts).

export const PLACE_KINDS = [
  // place types
  "temple", "market", "viewpoint", "beach", "island", "homestay", "village", "city", "museum", "palace",
  "mountain", "cave", "waterfall", "lake", "river", "park", "tea",
  // transport of a day or a leg
  "boat", "train", "car", "flight", "bike",
  // the catalog's activity enums
  "trekking", "culture", "wildlife", "water", "culinary", "wellness", "adventure", "local",
  // nothing matched
  "pin",
] as const;
export type PlaceKind = (typeof PLACE_KINDS)[number];

// Line glyphs, one or more path `d` strings each (24 x 24).
export const PLACE_GLYPHS: Record<PlaceKind, readonly string[]> = {
  temple: ["M12 3 4 8h16l-8-5Z", "M6 8v10M10 8v10M14 8v10M18 8v10", "M3 18h18M3 21h18"],
  market: ["M4 9h16l-1.5-5h-13L4 9Z", "M4 9c0 1.7 1.3 3 3 3s3-1.3 3-3c0 1.7 1.3 3 2 3s2-1.3 2-3c0 1.7 1.3 3 3 3s3-1.3 3-3",
    "M5 12v8h14v-8", "M10 20v-4h4v4"],
  viewpoint: ["M3 19 9 9l4 6 2-3 6 7H3Z", "M17 7a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z"],
  beach: ["M3 20c2 0 2-1.5 4-1.5S9 20 11 20s2-1.5 4-1.5 2 1.5 4 1.5", "M14 17 10 6", "M5 9c2-4 8-5 11-2-4-.5-7 .5-11 2Z"],
  island: ["M3 19c3-2 15-2 18 0", "M12 17V8", "M12 8c-2-2-5-2-7 0 2-.5 4 0 7 0Zm0 0c2-2 5-2 7 0-2-.5-4 0-7 0Z"],
  homestay: ["M4 11 12 4l8 7", "M6 10v10h12V10", "M10 20v-5h4v5"],
  village: ["M2 12 7 7l5 5", "M3.5 11v8h7v-8", "M12 12l5-5 5 5", "M13.5 11v8h7v-8"],
  city: ["M4 21V9h6v12", "M10 21V4h7v17", "M17 21v-8h3v8", "M3 21h18", "M13 8h1M13 12h1M13 16h1"],
  museum: ["M3 9 12 4l9 5", "M5 9v9M19 9v9M9.5 9v9M14.5 9v9", "M3 18h18", "M3 21h18"],
  palace: ["M4 21V10l2-2 2 2v11", "M16 21V10l2-2 2 2v11", "M8 21v-8h8v8", "M10 21v-3a2 2 0 0 1 4 0v3", "M3 21h18"],
  mountain: ["M2 20 9 7l4 7 2-3 7 9H2Z", "M7.5 10 9 12l1.5-2"],
  cave: ["M3 20c0-8 4-14 9-14s9 6 9 14", "M8 20c0-4 2-7 4-7s4 3 4 7", "M3 20h18"],
  waterfall: ["M4 4h16", "M8 4v11M12 4v13M16 4v11", "M5 19c2 0 2 1.5 4 1.5s2-1.5 3-1.5 2 1.5 3 1.5 2-1.5 4-1.5"],
  lake: ["M4 13c0-3.5 3.6-6 8-6s8 2.5 8 6-3.6 6-8 6-8-2.5-8-6Z", "M8 13c1 0 1 1 2 1s1-1 2-1 1 1 2 1 1-1 2-1"],
  river: ["M5 3c4 3-4 6 0 9s-4 6 0 9", "M13 3c4 3-4 6 0 9s-4 6 0 9", "M19 3v18"],
  park: ["M12 21v-5", "M12 16c-3.5 0-6-2.2-6-5 0-2.6 2.2-5 6-8 3.8 3 6 5.4 6 8 0 2.8-2.5 5-6 5Z", "M8 21h8"],
  tea: ["M4 10h12v4a5 5 0 0 1-5 5H9a5 5 0 0 1-5-5v-4Z", "M16 11h1.5a2.5 2.5 0 0 1 0 5H16", "M8 3c-1 1.5 1 2.5 0 4M12 3c-1 1.5 1 2.5 0 4"],
  boat: ["M3 15h18l-2.5 4h-13L3 15Z", "M12 3v12", "M12 4l6 9h-6", "M3 21c2 0 2-1 4.5-1s2.5 1 4.5 1 2-1 4.5-1 2.5 1 4.5 1"],
  train: ["M6 4h12a1 1 0 0 1 1 1v10a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V5a1 1 0 0 1 1-1Z", "M5 11h14", "M8 14h.01M16 14h.01",
    "M8 17l-2 4M16 17l2 4"],
  car: ["M5 16V12l2-5h10l2 5v4", "M3 16h18v3H3z", "M7 19v1.5M17 19v1.5", "M5 12h14"],
  flight: ["M21 15.5 13.5 11V5a1.5 1.5 0 0 0-3 0v6L3 15.5V17l7.5-2.5V19L8.5 20.5V22l3.5-1 3.5 1v-1.5L13.5 19v-4.5L21 17Z"],
  bike: ["M6 18a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z", "M18 18a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z", "M6 15l4-7h5l3 7M10 8l2 7",
    "M14 5h2"],
  trekking: ["M4 4h3v9", "M7 9c1.5 0 2 1.5 3.5 2l6 2c1.5.5 2.5 1.5 2.5 3H4", "M4 17h15"],
  culture: ["M3 9l9-5 9 5", "M5 9v8M9.5 9v8M14.5 9v8M19 9v8", "M3 17h18M4 20h16"],
  wildlife: ["M7 10.6a1.6 1.6 0 1 0 0-3.2 1.6 1.6 0 0 0 0 3.2Z", "M12 8.1a1.6 1.6 0 1 0 0-3.2 1.6 1.6 0 0 0 0 3.2Z",
    "M17 10.6a1.6 1.6 0 1 0 0-3.2 1.6 1.6 0 0 0 0 3.2Z",
    "M12 12c-2.8 0-5 2-5 4.2 0 1.6 1.4 2.3 2.7 1.8 1.5-.6 3.1-.6 4.6 0 1.3.5 2.7-.2 2.7-1.8C17 14 14.8 12 12 12Z"],
  water: ["M2 7c2 0 2 1.5 4 1.5S8 7 10 7s2 1.5 4 1.5S16 7 18 7s2 1.5 4 1.5",
    "M2 12c2 0 2 1.5 4 1.5S8 12 10 12s2 1.5 4 1.5 2-1.5 4-1.5 2 1.5 4 1.5",
    "M2 17c2 0 2 1.5 4 1.5S8 17 10 17s2 1.5 4 1.5 2-1.5 4-1.5 2 1.5 4 1.5"],
  culinary: ["M6 3v7M9 3v7M6 10a1.5 1.5 0 0 0 3 0M7.5 10v11", "M16 3c-1.5 0-2.5 1.5-2.5 4s1 4 2.5 4V3Zm0 8v10"],
  wellness: ["M12 4c1.6 1.8 2.4 3.8 2.4 6-.8.4-1.6.6-2.4.6s-1.6-.2-2.4-.6C9.6 7.8 10.4 5.8 12 4Z",
    "M4.5 9.5c2.3.2 4.1 1 5.5 2.3-.4.8-1 1.5-1.8 2C6.6 12.9 5.2 11.5 4.5 9.5Z",
    "M19.5 9.5c-2.3.2-4.1 1-5.5 2.3.4.8 1 1.5 1.8 2 1.6-.9 3-2.3 3.7-4.3Z", "M4 15c2.5 2 5 3 8 3s5.5-1 8-3"],
  adventure: ["M2.5 11a9.5 7 0 0 1 19 0Z", "M8.7 11 12 15.5 15.3 11", "M12 4v7M12 15.5V19a2 2 0 0 0 2 2"],
  local: ["M12 9.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Z", "M12 12c-3 0-5 1.8-5 4v2h10v-2c0-2.2-2-4-5-4Z",
    "M5 9.5 3 11M19 9.5 21 11"],
  pin: ["M12 21s-7-6.1-7-11a7 7 0 1 1 14 0c0 4.9-7 11-7 11Z", "M12 12.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Z"],
};

// The catalog's activity enums → their kind (the fallback when no name word matches).
export const ACTIVITY_KIND: Record<string, PlaceKind> = {
  trekking: "trekking",
  cultural_heritage: "culture",
  wildlife_nature: "wildlife",
  water_activities: "water",
  culinary: "culinary",
  wellness_relaxation: "wellness",
  adventure_sport: "adventure",
  local_immersion: "local",
};

// Name words → kind, most specific first (a "boat trip to the floating market" is a boat day before a market).
const WORD_RULES: readonly (readonly [PlaceKind, RegExp])[] = [
  ["flight", /\b(flight|fly|airport|domestic hop)\b/],
  ["train", /\b(train|railway|rail)\b/],
  ["boat", /\b(boat|cruise|ferry|junk|sampan|kayak(ing)?|canoe|longtail|rafting|sail(ing)?)\b/],
  ["bike", /\b(bike|biking|bicycle|cycling|cycle)\b/],
  ["car", /\b(drive|driving|jeep|4wd|transfer|road trip|motorbike)\b/],
  ["tea", /\b(tea|coffee)\b/],
  ["culinary", /\b(cooking|cookery|food|dinner|lunch|breakfast|meal|street food|tasting|restaurant)\b/],
  ["market", /\b(market|bazaar|souk)\b/],
  ["temple", /\b(temple|pagoda|wat|stupa|monastery|shrine|mosque|church|cathedral|dzong|gompa)\b/],
  ["museum", /\b(museum|gallery|memorial)\b/],
  ["palace", /\b(palace|fort|fortress|citadel|castle|royal tomb|tomb)\b/],
  ["waterfall", /\b(waterfalls?|falls|cascade)\b/],
  ["cave", /\b(caves?|grotto)\b/],
  ["beach", /\b(beach|coast|bay|seaside)\b/],
  ["island", /\b(island|islands|isle|atoll)\b/],
  ["lake", /\b(lake|lagoon|pond|reservoir)\b/],
  ["river", /\b(river|delta|canal|stream)\b/],
  ["viewpoint", /\b(viewpoint|view point|sunrise|sunset|lookout|panorama|summit view)\b/],
  ["homestay", /\b(homestay|home stay|guesthouse|lodge|camp|overnight)\b/],
  ["village", /\b(village|hamlet|ethnic|tribe|community)\b/],
  ["park", /\b(national park|park|garden|forest|reserve|jungle)\b/],
  ["mountain", /\b(mountain|mount|peak|pass|hill|highlands?|volcano|glacier)\b/],
  ["city", /\b(city|town|old quarter|capital|downtown)\b/],
];

/** The kind of words in a name, or null. */
export function kindOfName(name: string | null | undefined): PlaceKind | null {
  const s = (name ?? "").toLowerCase();
  if (!s) return null;
  for (const [kind, rx] of WORD_RULES) if (rx.test(s)) return kind;
  return null;
}

/** The icon of a stop: its own name, then its place's name, then its activity, then a pin. */
export function placeKind(stop: {
  name?: string | null;
  destination_name?: string | null;
  activity?: string | null;
}): PlaceKind {
  return (
    kindOfName(stop.name) ??
    kindOfName(stop.destination_name) ??
    (stop.activity ? ACTIVITY_KIND[stop.activity] : undefined) ??
    "pin"
  );
}

/** The icon of a travel leg between two stops (lib/mapbox.ts RouteLeg mode). */
export function legKind(mode: string, lineClass?: string | null): PlaceKind {
  if (mode === "flight") return "flight";
  if (mode === "feature") return lineClass === "water" ? "boat" : lineClass === "trail" ? "trekking" : "car";
  return "car";
}

/** A short, readable label of a kind (the icon's accessible name). */
export function kindLabel(kind: PlaceKind): string {
  return kind === "pin" ? "Place" : kind.charAt(0).toUpperCase() + kind.slice(1);
}
