// AA-812 — the /plan query string → what the planner pre-fills. Validated here so a hand-edited
// URL can never reach the BFF with junk (tour must be a uuid, numbers in range).
import type { PlanIntent } from "./useTrip";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function one(v: string | string[] | undefined): string | undefined {
  const s = Array.isArray(v) ? v[0] : v;
  return s && s.trim() ? s.trim() : undefined;
}

function intInRange(v: string | undefined, lo: number, hi: number): number | undefined {
  if (!v || !/^\d+$/.test(v)) return undefined;
  const n = Number(v);
  return n >= lo && n <= hi ? n : undefined;
}

export function parseIntent(sp: Record<string, string | string[] | undefined>): PlanIntent {
  const tour = one(sp.tour);
  return {
    country: one(sp.country)?.slice(0, 80),
    days: intInRange(one(sp.days), 2, 30),
    travellers: intInRange(one(sp.travellers), 1, 20),
    tourId: tour && UUID.test(tour) ? tour : undefined,
    ask: one(sp.ask) === "1",
  };
}
