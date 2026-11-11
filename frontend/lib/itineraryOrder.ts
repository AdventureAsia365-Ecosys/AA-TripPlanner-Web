// AA-1041 (R7 slice 1, item 4) — the order of a trip's days and stops, DOM-free so node:test covers it.
//
// The assembly Lambda keeps the trip as an event log; a manual order is one `reorder` event carrying every component
// id in the new order (PATCH /trip/{id}/reorder, the way the planner has always saved an order). Days are derived
// from that order (backend/assembly/sequencing.py group_into_days): one stop per day, except that consecutive stops
// of the same AA tour day share a day. So:
//   - moving a STOP moves one component id in the flat list (a stop that leaves its shared tour day becomes a day of
//     its own; the server decides, `assignDays` predicts it for the optimistic redraw);
//   - moving a DAY moves all of that day's component ids together.
// The same rule numbers the days of the read-only share view (`assignDays` on the shared stops).

export interface OrderedStop {
  component_id: string;
  day: number;
  source_tour_id?: string | null;
  source_day_index?: number | null;
}

export interface DayGroup<T> {
  day: number;
  stops: T[];
}

/** The component ids in visiting order. */
export function orderOf(itinerary: readonly { component_id: string }[]): string[] {
  return itinerary.map((d) => d.component_id);
}

export function sameOrder(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((x, i) => x === b[i]);
}

/** Consecutive stops with the same day number form one day (the list the trip panel draws). */
export function groupByDay<T extends { day: number }>(itinerary: readonly T[]): DayGroup<T>[] {
  const out: DayGroup<T>[] = [];
  for (const d of itinerary) {
    const last = out[out.length - 1];
    if (last && last.day === d.day) last.stops.push(d);
    else out.push({ day: d.day, stops: [d] });
  }
  return out;
}

/** A copy of `list` with the item at `from` moved to `to` (both clamped). */
export function moveItem<T>(list: readonly T[], from: number, to: number): T[] {
  const out = list.slice();
  if (from < 0 || from >= out.length) return out;
  const target = Math.max(0, Math.min(out.length - 1, to));
  const [item] = out.splice(from, 1);
  out.splice(target, 0, item);
  return out;
}

/** The new component order after moving one stop to flat position `to`; null when nothing changes. */
export function moveStop(itinerary: readonly { component_id: string }[], componentId: string, to: number): string[] | null {
  const ids = orderOf(itinerary);
  const from = ids.indexOf(componentId);
  if (from < 0) return null;
  const next = moveItem(ids, from, to);
  return sameOrder(ids, next) ? null : next;
}

/** The new component order after moving day group `from` to group position `to`; null when nothing changes. */
export function moveDay(itinerary: readonly OrderedStop[], from: number, to: number): string[] | null {
  const groups = groupByDay(itinerary);
  if (from < 0 || from >= groups.length) return null;
  const next = moveItem(groups, from, to).flatMap((g) => g.stops.map((s) => s.component_id));
  return sameOrder(orderOf(itinerary), next) ? null : next;
}

/** Python's group_into_days: a new day unless the stop continues the previous stop's AA tour day. */
export function assignDays<T extends Omit<OrderedStop, "day">>(stops: readonly T[]): (T & { day: number })[] {
  let day = 0;
  let prev: string | null = null;
  return stops.map((s) => {
    const key = s.source_tour_id != null && s.source_day_index != null ? `${s.source_tour_id}|${s.source_day_index}` : null;
    if (!(key !== null && key === prev)) day += 1;
    prev = key;
    return { ...s, day };
  });
}

/** The itinerary in a new order with its days renumbered (the optimistic redraw before the server answers). */
export function reorderLocally<T extends OrderedStop>(itinerary: readonly T[], ids: readonly string[]): T[] {
  const byId = new Map(itinerary.map((d) => [d.component_id, d]));
  const ordered = ids.map((id) => byId.get(id)).filter((d): d is T => d !== undefined);
  for (const d of itinerary) if (!ids.includes(d.component_id)) ordered.push(d);
  return assignDays(ordered) as T[];
}

/** Keyboard reorder on a drag handle: the target position for a key, or null (not a move key / already there). */
export function keyTarget(key: string, index: number, length: number): number | null {
  let to: number;
  if (key === "ArrowUp") to = index - 1;
  else if (key === "ArrowDown") to = index + 1;
  else if (key === "Home") to = 0;
  else if (key === "End") to = length - 1;
  else return null;
  if (to < 0 || to >= length || to === index) return null;
  return to;
}

/**
 * Pointer drag (mouse and touch): the slot the dragged row lands on, from the vertical centres of every row measured
 * when the drag started and the pointer's current y. The dragged row's own centre is skipped: dropping it on itself
 * keeps it where it is.
 */
export function dropIndex(centres: readonly number[], y: number, from: number): number {
  let to = 0;
  for (let i = 0; i < centres.length; i++) {
    if (i === from) continue;
    if (y > centres[i]) to = i < from ? i + 1 : i;
  }
  return Math.max(0, Math.min(centres.length - 1, to));
}

/** The live-region sentence after a move ("Moved Day 3 to position 1 of 4."). */
export function moveAnnouncement(label: string, to: number, length: number): string {
  return `Moved ${label} to position ${to + 1} of ${length}.`;
}
