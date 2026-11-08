// AA-1038 R5 hosting — the edge gate of the BFF calls a script must not reach unchecked, once the site runs on ECS
// behind CloudFront: the two that can reach a model (search: an embedding call, narration: a chat model call) and
// "Send to advisor" (stores a traveller's contact and delivers it to an agency; review S272).
//
// On ECS the BotID check is off (it needs the Vercel edge; lib/botCheck.ts) and the TripPlanner rules of the ALB web
// ACL take its place (AA-CIS-Infra tripplanner_web_edge.tf, modules/waf): a rate rule and a Challenge on the URI paths
//   GET  /api/browse/search,   POST /api/trip/narrate   and   POST /api/trip/send.
// A WAF rule sees the path, not the query string or the JSON body, so the older spellings of the same calls
// (GET /api/browse?resource=search, POST /api/trip {op: "narrate" | "send"}) would pass the edge unchallenged. With the
// gate on, the BFF answers those spellings 404 `use_protected_path` before any Lambda call, so every model call and
// every lead has passed the edge rules. The planner already calls only the protected paths (AA-1015, AA-1038).
//
// The pathname here is the one Next resolved with WHATWG `new URL`: a dot segment, plain or percent-encoded
// (/api/browse/%2e/search, /api/trip/./narrate), is already gone, so such a spelling reaches the route as the
// protected path and this gate lets it through. The web ACL must see it the same way: CloudFront hands WAF the raw
// path, so every path match there URL-decodes, then normalizes (tripplanner_web_edge.tf, review S272). An encoded
// letter (/api/browse/%73earch) stays encoded in WHATWG and never reaches these routes.
//
// On / off: the runtime env TP_EDGE_GATE ("on" only on the ECS task, set by Terraform), read per request. Off
// (Vercel, local, CI) keeps today's behaviour, where BotID guards the same two calls in the shared branch.
//
// No next/* import: `npm test` runs it.

import { NARRATE_PATH, SEARCH_PATH, SEND_PATH } from "./botCheck.ts";

export const USE_PROTECTED_PATH = "use_protected_path";

export function edgeGateOn(env: Record<string, string | undefined> = process.env): boolean {
  return (env.TP_EDGE_GATE ?? "").trim().toLowerCase() === "on";
}

export interface EdgeGateRefusal {
  status: 404;
  body: { error: typeof USE_PROTECTED_PATH };
}

export type GatedRoute = "search" | "narrate" | "send";

// Each gated call and the only pathname it is answered on behind the edge (the web ACL's protected paths).
export const PROTECTED_PATHS: Record<GatedRoute, string> = {
  search: SEARCH_PATH,
  narrate: NARRATE_PATH,
  send: SEND_PATH,
};

/** The refusal for a gated call that did not come through its protected pathname, or null to go on. */
export function edgeGateRefusal(route: GatedRoute, pathname: string, on: boolean): EdgeGateRefusal | null {
  if (!on) return null;
  return pathname === PROTECTED_PATHS[route] ? null : { status: 404, body: { error: USE_PROTECTED_PATH } };
}
