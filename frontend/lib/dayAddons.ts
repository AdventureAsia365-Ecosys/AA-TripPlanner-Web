// web: lib/dayAddons.ts — AA-1152 (S285 advisory PR-29 T6, B2 option C): the AA add-ons of each day card. The pure half:
// the Lambda paths the BFF (app/api/trip/addons/route.ts) maps to, the shapes, and the words of a fact line. No Next
// imports, so `npm test` runs it. Prices are the agency's own (its CRM published them); AA's planner shows none.

export interface DayAddonPrice {
  from_per_person: string;
  currency: string;
}

export interface DayAddon {
  component_id: string;
  title: string;
  unit: "person" | "group" | "vehicle" | "day";
  duration_minutes: number | null;
  min_pax: number;
  operating_weekdays: number[];
  start_times: string[];
  destination_id: string;
  at: "sleep" | "visited";
  selected: boolean;
  price: DayAddonPrice | null;
}

export interface DayAddons {
  day: number;
  addons: DayAddon[];
}

export interface TripAddons {
  trip_id: string;
  days: DayAddons[];
  prices_shown: boolean;
}

const SV_RE = /^sv:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const UUIDISH = /^[0-9A-Za-z-]{1,64}$/;

type PickBody = { session_id?: unknown; day?: unknown; component_id?: unknown };

/** A pick's {session, day, sv id}, or null when one is missing or malformed. */
function pickOf(body: PickBody): { session: string; day: number; cid: string } | null {
  const day = Number(body.day);
  const cid = String(body.component_id ?? "");
  const dayOk = Number.isInteger(day) && day >= 1 && day <= 90;
  if (!dayOk || !SV_RE.test(cid) || typeof body.session_id !== "string") return null;
  return { session: body.session_id, day, cid };
}

/** The Lambda request of a BFF call: GET (the days), POST (a pick) or DELETE (an unpick); null when malformed. */
export function addonsUpstream(
  method: "GET" | "POST" | "DELETE",
  tripId: string | null,
  body: PickBody = {},
): { method: string; path: string; payload?: Record<string, unknown> } | null {
  if (!tripId || !UUIDISH.test(tripId)) return null;
  const base = `/trip/${encodeURIComponent(tripId)}/addons`;
  if (method === "GET") return { method, path: base };
  const pick = pickOf(body);
  if (!pick) return null;
  if (method === "POST") return { method, path: base, payload: { session_id: pick.session, day: pick.day, component_id: pick.cid } };
  return { method, path: `${base}/${pick.day}/${encodeURIComponent(pick.cid)}`, payload: { session_id: pick.session } };
}

/** The add-ons of one day (none when the trip has no such day). */
export function addonsOfDay(data: TripAddons | null, day: number): DayAddon[] {
  return data?.days.find((d) => d.day === day)?.addons ?? [];
}

/** "3 h", "1 h 30 min", "45 min" (null = nothing). */
export function durationText(minutes: number | null): string | null {
  if (minutes === null || minutes <= 0) return null;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (h === 0) return `${m} min`;
  return m === 0 ? `${h} h` : `${h} h ${m} min`;
}

/** The price per traveller in the agency's currency ("from USD 42"); VND without decimals. */
export function priceText(price: DayAddonPrice, locale: string): string {
  const n = Number(price.from_per_person);
  if (!Number.isFinite(n)) return `${price.from_per_person} ${price.currency}`;
  try {
    return new Intl.NumberFormat(locale, { style: "currency", currency: price.currency, maximumFractionDigits: n % 1 === 0 ? 0 : 2 }).format(n);
  } catch {
    return `${price.from_per_person} ${price.currency}`;
  }
}
