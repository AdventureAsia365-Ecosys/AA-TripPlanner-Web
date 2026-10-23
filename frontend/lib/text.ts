// AA-812 — small text helpers for catalog content (server and client safe).

// "Private guide\n- Transfers; Breakfast" → ["Private guide", "Transfers", "Breakfast"]
export function splitLines(text: string | null | undefined): string[] {
  if (!text) return [];
  return text
    .split(/\r?\n|;|•/)
    .map((s) => s.replace(/^[\s\-*–—]+/, "").trim())
    .filter(Boolean);
}

// First ~max chars on a word boundary, for meta descriptions.
export function clip(text: string | null | undefined, max = 160): string {
  const t = (text ?? "").replace(/\s+/g, " ").trim();
  if (t.length <= max) return t;
  const cut = t.slice(0, max - 1);
  return `${cut.slice(0, Math.max(cut.lastIndexOf(" "), max - 20))}…`;
}

// AA-837 (D12): every day-count label goes through these two, so a one-day tour reads "1 day",
// never "1 days".
export function formatDays(n: number): string {
  return `${n} ${n === 1 ? "day" : "days"}`;
}

// A min–max length ("3 to 5 days", "3–5 days"); a single length when both ends are equal ("1 day").
export function formatDayRange(min: number, max: number, sep = " to "): string {
  return min === max ? formatDays(min) : `${min}${sep}${max} days`;
}

export function daysText(n: number | null | undefined, raw?: string | null): string | null {
  if (n) return formatDays(n);
  return raw || null;
}

export function titleCase(v: string): string {
  return v.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

// Same rule as the backend's catalog.slugify (and catalog.v_tour.slug): lowercase, every run of
// characters outside [a-z0-9] becomes "-", trimmed. Takes a raw route param (URL-encoded or not).
export function slugify(raw: string): string {
  let text = raw;
  try {
    text = decodeURIComponent(raw);
  } catch {
    /* already decoded / malformed: use as is */
  }
  return text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
}

// Alt text for a tour cover. A fallback cover is a photo of a destination, not of the tour, so it
// is named as such ("Hanoi — destination photo") instead of carrying the tour's title.
export function coverAlt(
  t: { title: string; cover_source?: string | null; cover_place?: string | null },
): string {
  if ((t.cover_source === "destination" || t.cover_source === "country") && t.cover_place) {
    return `${t.cover_place} — destination photo`;
  }
  return t.title;
}

// Travel months for the month filter (value = 1-12, the browse API's `month`).
export const MONTH_NAMES = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
] as const;
