// AA-837 (D11): "no planner route for this tour" is a normal answer, not a failed fetch.
// The browse Lambda answers GET /browse/tour-route with a 404 (+ the tour's title, country and day
// list) when the planner has nothing for the tour. A 404 on a browser fetch is printed as a red
// network error in the console, so the /api/browse BFF turns that one answer into a 200
// `{ match: "none", tour }` (no-store), and the client reads it as "missing". The Lambda contract
// is unchanged. No Next imports, so `npm test` runs it.
import type { RouteProposal, TourRouteFallback } from "./types.ts";

export const TOUR_ROUTE_NONE = "none" as const;

export interface TourRouteNone {
  match: typeof TOUR_ROUTE_NONE;
  tour: TourRouteFallback | null;
}

// BFF half: the status and body the browser gets for an upstream tour-route answer. Only the 404
// is rewritten; every other status (200, 400, 5xx) passes through untouched.
export function tourRouteBffAnswer(
  upstreamStatus: number,
  upstreamBody: string,
): { status: number; body: string; rewritten: boolean } {
  if (upstreamStatus !== 404) return { status: upstreamStatus, body: upstreamBody, rewritten: false };
  let tour: TourRouteFallback | null = null;
  try {
    const parsed = JSON.parse(upstreamBody) as { tour?: TourRouteFallback | null } | null;
    tour = parsed?.tour ?? null;
  } catch {
    /* not JSON (e.g. a gateway 404 page): no tour details, still "none" */
  }
  const none: TourRouteNone = { match: TOUR_ROUTE_NONE, tour };
  return { status: 200, body: JSON.stringify(none), rewritten: true };
}

// One tour's whole route as a route proposal (for "Customise this trip"). `missing` = the planner
// has nothing for the tour (carries the tour's title and day list when the catalog knows it);
// "error" = the read failed.
export type TourRouteResult =
  | RouteProposal
  | { missing: true; tour: TourRouteFallback | null }
  | "error";

// Client half: what fetchTourRoute returns for a /api/browse?resource=tour-route response. A 404
// is still read as "missing" so a client never breaks against an older BFF.
export function readTourRoute(status: number, data: unknown): TourRouteResult {
  const body = (data ?? null) as { match?: string; tour?: TourRouteFallback | null; proposal?: RouteProposal } | null;
  if (status === 404 || (status === 200 && body?.match === TOUR_ROUTE_NONE)) {
    return { missing: true, tour: body?.tour ?? null };
  }
  if (status < 200 || status >= 300) return "error";
  return body?.proposal ?? { missing: true, tour: null };
}
