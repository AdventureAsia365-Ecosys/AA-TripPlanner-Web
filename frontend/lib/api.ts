// Thin client that talks to the BFF routes (never the Lambdas directly).
import { limitNotice, narrationNotice, type AiNoticeKey } from "./aiNotices";
import type {
  BrowseFilters,
  CountryOption,
  Coverage,
  CustomDay,
  DestinationDetail,
  DestinationPin,
  ItineraryDay,
  GeoJSONLine,
  RouteProposal,
  ShapeStatus,
  StopActivity,
  UnavailableStop,
} from "./types";
import type { TourList, TourSort } from "./catalogTypes";
import { readTourRoute, type TourRouteResult } from "./tourRoute";
import { COOKIES_NOTICE, COOKIES_REQUIRED, resetGuestIds, showGuestNotice } from "./guestIds";

function filterParams(f: BrowseFilters): string {
  const p = new URLSearchParams();
  if (f.activity) p.set("activity", f.activity);
  if (f.intensity_level) p.set("intensity_level", f.intensity_level);
  if (f.country) p.set("country", f.country);
  if (f.season) p.set("season", String(f.season));
  return p.toString();
}

export function tileScope(filters: BrowseFilters): string {
  return filterParams(filters);
}

// AA-812 hotfix: throws on a non-2xx (e.g. 503 under Lambda throttling) so the tile loader does not
// cache a failed tile as empty; it retries it on the next view instead.
export async function fetchTile(
  tileId: string,
  filters: BrowseFilters,
  signal?: AbortSignal,
): Promise<DestinationPin[]> {
  const qs = filterParams(filters);
  const res = await fetch(
    `/api/browse?resource=tiles&tile_id=${encodeURIComponent(tileId)}&${qs}`,
    { signal },
  );
  if (!res.ok) throw new Error(`tile ${tileId}: HTTP ${res.status}`);
  const data = await res.json();
  return data.destinations ?? [];
}

// AA-847 slice 6d: the geometry of line places (rivers, roads, trails) by id. Cached per id and level for the page's
// lifetime (shapes change rarely; the CDN caches the answer too). Ids already cached are not asked again.
export interface LineShape {
  id: string;
  status: ShapeStatus;
  geometry: GeoJSONLine | null;
}
const SHAPE_BATCH = 50;
const shapeCache = new Map<string, LineShape>();

export async function fetchShapes(ids: string[], level: "lo" | "mid"): Promise<Map<string, LineShape>> {
  const out = new Map<string, LineShape>();
  const missing: string[] = [];
  for (const id of new Set(ids)) {
    const hit = shapeCache.get(`${level}:${id}`);
    if (hit) out.set(id, hit);
    else missing.push(id);
  }
  for (let i = 0; i < missing.length; i += SHAPE_BATCH) {
    const batch = missing.slice(i, i + SHAPE_BATCH).sort();
    const res = await fetch(`/api/browse?resource=shapes&level=${level}&ids=${batch.map(encodeURIComponent).join(",")}`);
    if (!res.ok) continue; // a failed batch draws nothing now and is asked again on the next view
    const data = await res.json();
    for (const sh of (data.shapes ?? []) as LineShape[]) {
      shapeCache.set(`${level}:${sh.id}`, sh);
      out.set(sh.id, sh);
    }
  }
  return out;
}

export async function fetchDestination(
  id: string,
): Promise<DestinationDetail | null> {
  const res = await fetch(
    `/api/browse?resource=destination&id=${encodeURIComponent(id)}`,
  );
  if (!res.ok) return null;
  return res.json();
}

export async function fetchCountries(): Promise<CountryOption[]> {
  const res = await fetch(`/api/browse?resource=countries`);
  if (!res.ok) return [];
  const data = await res.json();
  return data.countries ?? [];
}

export async function fetchByCountry(
  country: string,
): Promise<DestinationPin[]> {
  const res = await fetch(
    `/api/browse?resource=by-country&country=${encodeURIComponent(country)}`,
  );
  if (!res.ok) return [];
  const data = await res.json();
  return data.destinations ?? [];
}

// `limited` (AA-1015): this visitor reached the per-IP search limit (429); the caller keeps what the map shows.
export async function search(
  q: string,
  filters: BrowseFilters,
): Promise<{ pins: DestinationPin[]; limited: boolean }> {
  const qs = filterParams(filters);
  const res = await fetch(
    `/api/browse?resource=search&q=${encodeURIComponent(q)}&${qs}`,
  );
  if (res.status === 429) return { pins: [], limited: true };
  if (!res.ok) return { pins: [], limited: false };
  const data = await res.json();
  return { pins: data.destinations ?? [], limited: false };
}

interface TripResponse {
  trip_id: string;
  itinerary: ItineraryDay[];
  status?: string;
  custom_days?: CustomDay[];
  // AA-986 L6: saved stops whose tour AA no longer serves (absent from older Lambdas).
  unavailable_stops?: UnavailableStop[];
}

export async function fetchTrip(tripId: string): Promise<TripResponse | null> {
  const res = await fetch(`/api/trip?trip_id=${encodeURIComponent(tripId)}`);
  if (!res.ok) return null;
  return res.json();
}

// AA-827: every trip mutation goes through here (body always carries session_id + trip_id).
// - 401 guest_session_invalid (this browser's cookie does not match the stored session) or 403
//   forbidden (the trip belongs to another session): the stored ids are unusable, start a new trip
//   (resetGuestIds reloads the page, unless it just did).
// - 401 cookies_required: the request came without a cookie and the token minted for it was refused.
//   Retry once: if the cookie was only lost, the browser now holds the new one and the retry gets the
//   real answer. A second cookies_required means this browser does not keep the cookie: keep the ids,
//   say cookies are needed. A refused mutation changed nothing, so the retry is safe.
async function postTrip<T = Record<string, unknown>>(
  body: Record<string, unknown> & { session_id: string; trip_id: string },
): Promise<{ ok: boolean; status: number; data: T & { error?: string } }> {
  const send = async () => {
    const res = await fetch("/api/trip", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = (await res.json().catch(() => ({}))) as T & { error?: string };
    return { res, data };
  };
  const cookiesRequired = (r: { res: Response; data: { error?: string } }) =>
    r.res.status === 401 && r.data.error === COOKIES_REQUIRED;

  let r = await send();
  if (cookiesRequired(r)) {
    r = await send();
    if (cookiesRequired(r)) showGuestNotice(COOKIES_NOTICE);
  }
  const { res, data } = r;
  if (
    (res.status === 401 && data.error === "guest_session_invalid") ||
    (res.status === 403 && data.error === "forbidden")
  ) {
    resetGuestIds({ sessionId: body.session_id, tripId: body.trip_id });
  }
  return { ok: res.ok, status: res.status, data };
}

export async function addComponent(
  tripId: string,
  sessionId: string,
  componentId: string,
): Promise<TripResponse> {
  const { data } = await postTrip<TripResponse>({
    op: "add",
    trip_id: tripId,
    session_id: sessionId,
    component_id: componentId,
  });
  return data;
}

export async function removeComponent(
  tripId: string,
  sessionId: string,
  componentId: string,
): Promise<TripResponse> {
  const { data } = await postTrip<TripResponse>({
    op: "remove",
    trip_id: tripId,
    session_id: sessionId,
    component_id: componentId,
  });
  return data;
}

export async function reorder(
  tripId: string,
  sessionId: string,
  orderedComponentIds: string[],
): Promise<TripResponse> {
  const { data } = await postTrip<TripResponse>({
    op: "reorder",
    trip_id: tripId,
    session_id: sessionId,
    ordered_component_ids: orderedComponentIds,
  });
  return data;
}

export async function narrate(
  tripId: string,
  sessionId: string,
  mode: "compose" | "renarrate" = "compose",
): Promise<{ ok: boolean; narration: string; source: string; notice: AiNoticeKey | null }> {
  const { ok, status, data } = await postTrip<{
    narration?: string;
    source?: string;
    ai_paused?: boolean;
    ai_budget_exhausted?: boolean;
  }>({
    op: "narrate",
    trip_id: tripId,
    session_id: sessionId,
    mode,
  });
  // AA-1015: the per-session narration limit (429) keeps the current narration and says why.
  if (status === 429) return { ok: false, narration: "", source: "", notice: limitNotice("narrate") };
  if (!ok) return { ok: false, narration: "", source: "", notice: null };
  return {
    ok: true,
    narration: data.narration ?? "",
    source: data.source ?? "",
    // AA-642 S0: AI paused / daily budget spent -> the basic narration plus a calm notice.
    notice: narrationNotice(data),
  };
}

export async function sendToAdvisor(
  tripId: string,
  sessionId: string,
  customer?: { name: string; phone: string; email: string; consent: boolean },
): Promise<{ status: number; body: TripResponse & { error?: string } }> {
  const { status, data } = await postTrip<TripResponse>({
    op: "send",
    trip_id: tripId,
    session_id: sessionId,
    customer,
  });
  return { status, body: data };
}

export interface Suggestion {
  id: string;
  name: string;
  lat: number;
  lng: number;
  country: string;
  component_count: number;
  // AA-674: how many real AA tours go there next from the trip's last stop (0 = taste fallback).
  tour_count?: number;
  why: string;
}

// Next-to-pin suggestions for the current trip (Assembly Lambda; per-visitor
// state, never cached). Returns [] when the trip is empty or has no basis.
// AA-674: candidates follow real AA tours; activity/intensity filters narrow them.
export async function fetchSuggestions(
  tripId: string,
  filters: BrowseFilters = {},
): Promise<Suggestion[]> {
  const p = new URLSearchParams({ resource: "suggestions", trip_id: tripId });
  if (filters.activity) p.set("activity", filters.activity);
  if (filters.intensity_level) p.set("intensity_level", filters.intensity_level);
  const res = await fetch(`/api/trip?${p}`);
  if (!res.ok) return [];
  const data = await res.json();
  return data.suggestions ?? [];
}

// --- AA-674 / Jira PR-11 ----------------------------------------------------

// Whole-route proposals from real AA tours for a country and a number of days.
export async function fetchRoutes(country: string, days: number): Promise<RouteProposal[]> {
  const p = new URLSearchParams({ resource: "routes", country, days: String(days) });
  const res = await fetch(`/api/browse?${p}`);
  if (!res.ok) return [];
  const data = await res.json();
  return data.proposals ?? [];
}

// The leg chain of real tours that covers the trip, plus added days.
export async function fetchCoverage(tripId: string): Promise<Coverage | null> {
  const res = await fetch(`/api/trip?resource=coverage&trip_id=${encodeURIComponent(tripId)}`);
  if (!res.ok) return null;
  return res.json();
}

// What AA tours do at a stop; selecting one pins its component.
export async function fetchStopActivities(tripId: string, destinationId: string): Promise<StopActivity[]> {
  const p = new URLSearchParams({ resource: "activities", trip_id: tripId, destination_id: destinationId });
  const res = await fetch(`/api/trip?${p}`);
  if (!res.ok) return [];
  const data = await res.json();
  return data.activities ?? [];
}

// Start from a proposed route: pin its components in its day order.
export async function applyRoute(
  tripId: string,
  sessionId: string,
  componentIds: string[],
): Promise<TripResponse | null> {
  const { ok, data } = await postTrip<TripResponse>({
    op: "apply_route", trip_id: tripId, session_id: sessionId, component_ids: componentIds,
  });
  return ok ? data : null;
}

export async function addDay(
  tripId: string,
  sessionId: string,
  kind: CustomDay["kind"],
  afterComponentId?: string,
): Promise<CustomDay[] | null> {
  const { ok, data } = await postTrip<{ custom_days?: CustomDay[] }>({
    op: "add_day", trip_id: tripId, session_id: sessionId, kind, after_component_id: afterComponentId,
  });
  return ok ? data.custom_days ?? [] : null;
}

export async function removeDay(tripId: string, sessionId: string, dayId: string): Promise<CustomDay[] | null> {
  const { ok, data } = await postTrip<{ custom_days?: CustomDay[] }>({ op: "remove_day", trip_id: tripId, session_id: sessionId, day_id: dayId });
  return ok ? data.custom_days ?? [] : null;
}

// --- AA-812: catalog reads for the planner (list view, tour pre-fill) -------------------------

// Tour cards from the CIS catalog (via the browse Lambda). null = the read failed (show an error
// state), not "no tours".
export async function fetchCatalogTours(q: {
  country?: string;
  month?: number;
  sort?: TourSort;
  limit?: number;
  offset?: number;
}): Promise<TourList | null> {
  const p = new URLSearchParams({ resource: "catalog-tours" });
  if (q.country) p.set("country", q.country);
  if (q.month) p.set("month", String(q.month));
  if (q.sort) p.set("sort", q.sort);
  if (q.limit) p.set("limit", String(q.limit));
  if (q.offset) p.set("offset", String(q.offset));
  try {
    const res = await fetch(`/api/browse?${p}`);
    if (!res.ok) return null;
    return (await res.json()) as TourList;
  } catch {
    return null;
  }
}

// One tour's whole route as a route proposal (for "Customise this trip"); see lib/tourRoute.ts.
// AA-837: "no planner route" comes back as a 200 `{ match: "none" }`, so the console stays clean.
export type { TourRouteResult } from "./tourRoute";

export async function fetchTourRoute(
  tourId: string | undefined,
  sourceId?: string,
): Promise<TourRouteResult> {
  try {
    const p = new URLSearchParams({ resource: "tour-route" });
    if (tourId) p.set("tour_id", tourId);
    if (sourceId) p.set("source_id", sourceId);
    const res = await fetch(`/api/browse?${p}`);
    const data = res.status === 404 || res.ok ? await res.json().catch(() => null) : null;
    return readTourRoute(res.status, data);
  } catch {
    return "error";
  }
}
