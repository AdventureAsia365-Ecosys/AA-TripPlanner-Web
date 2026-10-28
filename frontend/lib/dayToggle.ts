// AA-995 — the logic behind the tour timeline's "Expand all / Collapse all" (components/site/DayToggleAll.tsx), kept
// DOM-free so node:test covers it: anything with an `open` flag (a <details> element in the page).
export type Openable = { open: boolean };

/** True when there is at least one day and every day is open. */
export function allOpen(days: readonly Openable[]): boolean {
  return days.length > 0 && days.every((d) => d.open);
}

/** Open or close every day; only the days whose state changes are touched (each fires one "toggle" event). */
export function setAllOpen(days: readonly Openable[], open: boolean): void {
  for (const d of days) if (d.open !== open) d.open = open;
}
