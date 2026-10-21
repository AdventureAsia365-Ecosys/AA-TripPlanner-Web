// The pure half of the /api/trip BFF (app/api/trip/route.ts): which assembly Lambda path a read
// maps to, and the headers every Lambda call carries. No Next imports, so `npm test` runs it.
// Server-only by convention: the headers carry the shared API key and the guest token.
import { GUEST_TOKEN_HEADER } from "./guestToken.ts";

// AA-827 / AA-909: every Lambda call carries the shared key and, when the request has one, the guest
// token (HttpOnly cookie, or the one middleware.ts minted on this request). Mutations and the four
// trip reads both need it: the Lambda answers 404 to a read without the owner's token. The token is
// never logged or echoed.
export function tripHeaders(apiKey: string, guestToken?: string | null): Record<string, string> {
  const h: Record<string, string> = { "content-type": "application/json" };
  if (apiKey) h["x-tripplanner-key"] = apiKey;
  if (guestToken) h[GUEST_TOKEN_HEADER] = guestToken;
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
