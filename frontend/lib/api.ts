// Thin client that talks to the BFF routes (never the Lambdas directly).
import type {
  BrowseFilters,
  CountryOption,
  DestinationDetail,
  DestinationPin,
  ItineraryDay,
} from "./types";

function filterParams(f: BrowseFilters): string {
  const p = new URLSearchParams();
  if (f.activity) p.set("activity", f.activity);
  if (f.intensity_level) p.set("intensity_level", f.intensity_level);
  if (f.country) p.set("country", f.country);
  if (f.season) p.set("season", String(f.season));
  return p.toString();
}

export async function fetchTile(
  tileId: string,
  filters: BrowseFilters,
): Promise<DestinationPin[]> {
  const qs = filterParams(filters);
  const res = await fetch(
    `/api/browse?resource=tiles&tile_id=${encodeURIComponent(tileId)}&${qs}`,
  );
  if (!res.ok) return [];
  const data = await res.json();
  return data.destinations ?? [];
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

export async function search(
  q: string,
  filters: BrowseFilters,
): Promise<DestinationPin[]> {
  const qs = filterParams(filters);
  const res = await fetch(
    `/api/browse?resource=search&q=${encodeURIComponent(q)}&${qs}`,
  );
  if (!res.ok) return [];
  const data = await res.json();
  return data.destinations ?? [];
}

interface TripResponse {
  trip_id: string;
  itinerary: ItineraryDay[];
  status?: string;
}

export async function fetchTrip(tripId: string): Promise<TripResponse | null> {
  const res = await fetch(`/api/trip?trip_id=${encodeURIComponent(tripId)}`);
  if (!res.ok) return null;
  return res.json();
}

export async function addComponent(
  tripId: string,
  sessionId: string,
  componentId: string,
): Promise<TripResponse> {
  const res = await fetch("/api/trip", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      op: "add",
      trip_id: tripId,
      session_id: sessionId,
      component_id: componentId,
    }),
  });
  return res.json();
}

export async function removeComponent(
  tripId: string,
  sessionId: string,
  componentId: string,
): Promise<TripResponse> {
  const res = await fetch("/api/trip", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      op: "remove",
      trip_id: tripId,
      session_id: sessionId,
      component_id: componentId,
    }),
  });
  return res.json();
}

export async function reorder(
  tripId: string,
  sessionId: string,
  orderedComponentIds: string[],
): Promise<TripResponse> {
  const res = await fetch("/api/trip", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      op: "reorder",
      trip_id: tripId,
      session_id: sessionId,
      ordered_component_ids: orderedComponentIds,
    }),
  });
  return res.json();
}

export async function narrate(
  tripId: string,
  sessionId: string,
  mode: "compose" | "renarrate" = "compose",
): Promise<{ ok: boolean; narration: string; source: string }> {
  const res = await fetch("/api/trip", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      op: "narrate",
      trip_id: tripId,
      session_id: sessionId,
      mode,
    }),
  });
  if (!res.ok) return { ok: false, narration: "", source: "" };
  const data = await res.json();
  return {
    ok: true,
    narration: data.narration ?? "",
    source: data.source ?? "",
  };
}

export async function sendToAdvisor(
  tripId: string,
  sessionId: string,
  customer?: { name: string; phone: string; email: string },
): Promise<{ status: number; body: TripResponse & { error?: string } }> {
  const res = await fetch("/api/trip", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      op: "send",
      trip_id: tripId,
      session_id: sessionId,
      customer,
    }),
  });
  return { status: res.status, body: await res.json() };
}

export interface Suggestion {
  id: string;
  name: string;
  lat: number;
  lng: number;
  country: string;
  component_count: number;
  why: string;
}

// Next-to-pin suggestions for the current trip (Assembly Lambda; per-visitor
// state, never cached). Returns [] when the trip is empty or has no basis.
export async function fetchSuggestions(tripId: string): Promise<Suggestion[]> {
  const res = await fetch(
    `/api/trip?resource=suggestions&trip_id=${encodeURIComponent(tripId)}`,
  );
  if (!res.ok) return [];
  const data = await res.json();
  return data.suggestions ?? [];
}
