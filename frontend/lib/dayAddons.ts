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
  if (!data || !Array.isArray(data.days)) return [];
  return data.days.find((d) => d.day === day)?.addons ?? [];
}

const strings = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : []);
const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);

function addonOf(v: unknown): DayAddon | null {
  if (!isRecord(v) || typeof v.component_id !== "string" || typeof v.title !== "string") return null;
  const unit = v.unit === "group" || v.unit === "vehicle" || v.unit === "day" ? v.unit : "person";
  const minutes = typeof v.duration_minutes === "number" && Number.isFinite(v.duration_minutes) ? v.duration_minutes : null;
  return {
    component_id: v.component_id,
    title: v.title,
    unit,
    duration_minutes: minutes,
    min_pax: typeof v.min_pax === "number" && Number.isFinite(v.min_pax) ? v.min_pax : 1,
    operating_weekdays: Array.isArray(v.operating_weekdays)
      ? v.operating_weekdays.filter((x): x is number => Number.isInteger(x))
      : [],
    start_times: strings(v.start_times),
    destination_id: typeof v.destination_id === "string" ? v.destination_id : "",
    at: v.at === "sleep" ? "sleep" : "visited",
    selected: v.selected === true,
    price: v.price == null ? null : agencyPriceOf({ price: v.price }),
  };
}

/**
 * The trip's add-ons from a BFF answer, or null when the answer is not that shape (a failed read is "no add-on").
 * The day cards render inside the planner's day list, so an unexpected body must never reach them: S291, a 200 with
 * another shape threw in addonsOfDay and took the whole planner down to the error page.
 */
export function tripAddonsOf(body: unknown): TripAddons | null {
  if (!isRecord(body) || !Array.isArray(body.days)) return null;
  const days: DayAddons[] = [];
  for (const d of body.days) {
    if (!isRecord(d) || !Number.isInteger(d.day) || !Array.isArray(d.addons)) continue;
    const addons = d.addons.map(addonOf).filter((a): a is DayAddon => a !== null);
    days.push({ day: d.day as number, addons });
  }
  return {
    trip_id: typeof body.trip_id === "string" ? body.trip_id : "",
    days,
    prices_shown: body.prices_shown === true,
  };
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

// The agency's own "from" price of one tour, read on every view of its tour page (app/api/trip/agency-price/route.ts,
// no-store), so the agency's "Show prices on my planner" switch and a new list show at once; the page itself is cached.
const SOURCE_RE = /^[^\x00-\x1f\x7f]{1,128}$/;
const AMOUNT_RE = /^\d{1,10}(\.\d{1,2})?$/;

/** The Lambda path of the tour's agency price, or null when the tour key is malformed. */
export function agencyPriceUpstream(sourceId: string | null): string | null {
  if (!sourceId || !SOURCE_RE.test(sourceId)) return null;
  return `/trip/agency-price?${new URLSearchParams({ source_id: sourceId })}`;
}

/** The price of an answer ({price: {from_per_person, currency} | null}); anything else is no price. */
export function agencyPriceOf(body: unknown): DayAddonPrice | null {
  const price = (body as { price?: unknown } | null)?.price as Partial<DayAddonPrice> | null | undefined;
  if (!price || typeof price.from_per_person !== "string" || typeof price.currency !== "string") return null;
  if (!AMOUNT_RE.test(price.from_per_person) || !/^[A-Z]{3}$/.test(price.currency)) return null;
  return { from_per_person: price.from_per_person, currency: price.currency };
}
