// The pure half of the /api/trip BFF (app/api/trip/route.ts): which assembly Lambda path a read
// maps to, and the headers every Lambda call carries. No Next imports, so `npm test` runs it.
// Server-only by convention: the headers carry the shared API key and the guest token.
import { GUEST_TOKEN_HEADER } from "./guestToken.ts";
import { REQUEST_ID_HEADER } from "./requestId.ts";

// AA-827 / AA-909: every Lambda call carries the shared key and, when the request has one, the guest
// token (HttpOnly cookie, or the one proxy.ts minted on this request). Mutations and the four
// trip reads both need it: the Lambda answers 404 to a read without the owner's token. The token is
// never logged or echoed.
// AA-871: and the request id (lib/requestId.ts), which the Lambda binds to its log lines.
export function tripHeaders(
  apiKey: string,
  guestToken?: string | null,
  requestId?: string | null,
): Record<string, string> {
  const h: Record<string, string> = { "content-type": "application/json" };
  if (apiKey) h["x-tripplanner-key"] = apiKey;
  if (guestToken) h[GUEST_TOKEN_HEADER] = guestToken;
  if (requestId) h[REQUEST_ID_HEADER] = requestId;
  return h;
}

export interface SearchParamSource {
  get(name: string): string | null;
}

// GET /api/trip?trip_id=…[&resource=suggestions|coverage|activities] -> the Lambda path.
// resource absent -> the itinerary (restore after reload); suggestions -> next-to-pin (with optional
// activity/intensity filters); coverage -> the AA leg chain (AA-674); activities -> what AA tours do
// at one stop (AA-674, needs destination_id).
export function tripReadPath(q: SearchParamSource): { path: string } | { error: string } {
  const tripId = encodeURIComponent(q.get("trip_id") ?? "");
  if (!tripId) return { error: "trip_id required" };
  const resource = q.get("resource");
  if (resource === "suggestions") {
    const f = new URLSearchParams();
    for (const k of ["activity", "intensity_level"]) {
      const v = q.get(k);
      if (v) f.set(k, v);
    }
    return { path: `/trip/${tripId}/suggestions${f.toString() ? `?${f}` : ""}` };
  }
  if (resource === "coverage") return { path: `/trip/${tripId}/coverage` };
  if (resource === "activities") {
    const dest = encodeURIComponent(q.get("destination_id") ?? "");
    if (!dest) return { error: "destination_id required" };
    return { path: `/trip/${tripId}/stops/${dest}/activities` };
  }
  return { path: `/trip/${tripId}` };
}

// AA-837: the itinerary read of a trip this browser has not saved yet (a first visit, or after a
// reset) is a 404 from the Lambda, which the client already treats as an empty trip. A 404 on a
// browser fetch prints a red console error on every first visit to /plan, so the BFF answers that
// one read with a 200 empty trip instead. Same body for "no such trip" and "not your trip", so
// existence still never leaks. Other reads (suggestions, coverage, activities) and statuses pass
// through untouched.
export const EMPTY_TRIP_BODY = JSON.stringify({ itinerary: [], custom_days: [] });

// `op`: the read tripReadPath mapped to ("itinerary" when no known resource was asked for).
export function tripReadAnswer(
  op: string,
  upstreamStatus: number,
  upstreamBody: string,
): { status: number; body: string } {
  if (op === "itinerary" && upstreamStatus === 404) return { status: 200, body: EMPTY_TRIP_BODY };
  return { status: upstreamStatus, body: upstreamBody };
}

// The POST body is one JSON object ({ op, trip_id, ... }). Anything else (not JSON, or JSON that is
// not an object) is the caller's mistake: the route answers 400 instead of throwing a 500.
export function parseTripBody(raw: string): Record<string, unknown> | null {
  try {
    const b: unknown = JSON.parse(raw);
    return b !== null && typeof b === "object" && !Array.isArray(b) ? (b as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}
