// AA-1038 R5 hosting — the edge gate of the BFF calls a script must not reach unchecked, once the site runs on ECS
// behind CloudFront: the two that can reach a model (search: an embedding call, narration: a chat model call) and
// "Send to advisor" (stores a traveller's contact and delivers it to an agency; review S272).
//
// The TripPlanner rules of the ALB web ACL are their bot check (lib/botCheck.ts; AA-CIS-Infra tripplanner_web_edge.tf,
// modules/waf): a rate rule and a Challenge on the URI paths
//   GET  /api/browse/search,   POST /api/trip/narrate   and   POST /api/trip/send.
// A WAF rule sees the path, not the query string or the JSON body, so the older spellings of the same calls
// (GET /api/browse?resource=search, POST /api/trip {op: "narrate" | "send"}) would pass the edge unchallenged. With the
// gate on, the BFF answers those spellings 404 `use_protected_path` before any Lambda call, so every model call and
// every lead has passed the edge rules. The planner already calls only the protected paths (AA-1015, AA-1038).
//
// The pathname here is the one Next resolved with WHATWG `new URL`: a dot segment, plain or percent-encoded
// (/api/browse/%2e/search, /api/trip/./narrate), is already gone, so such a spelling reaches the route as the
// protected path and this gate lets it through. The web ACL must see it the same way: CloudFront hands WAF the raw
// path, so every path match there URL-decodes, then normalizes (tripplanner_web_edge.tf, review S272). A LEADING dot
// segment (/%2e%2e/api/trip/send, /../api/browse/search, /./api/trip/narrate) is gone here too, but AWS WAF's
// NORMALIZE_PATH keeps a back-reference at the start of the path, so the web ACL matches a protected path with
// CONTAINS, never EXACTLY (modules/waf, review S272). An encoded letter (/api/browse/%73earch) stays encoded in
// WHATWG and never reaches these routes.
//
// On / off: the runtime env TP_EDGE_GATE ("on" on the ECS task, set by Terraform), read per request. Off only where no
// edge is in front (local `next dev` / `next start`, CI), so every spelling still works there.
//
// No next/* import: `npm test` runs it.

import { CHAT_PATH, NARRATE_PATH, SEARCH_PATH, SEND_PATH } from "./botCheck.ts";

export const USE_PROTECTED_PATH = "use_protected_path";

export function edgeGateOn(env: Record<string, string | undefined> = process.env): boolean {
  return (env.TP_EDGE_GATE ?? "").trim().toLowerCase() === "on";
}

export interface EdgeGateRefusal {
  status: 404;
  body: { error: typeof USE_PROTECTED_PATH };
}

// AA-1041 slice 2: + the planning chat (a model call per message), POST /api/trip/chat.
export type GatedRoute = "search" | "narrate" | "send" | "chat";

// Each gated call and the only pathname it is answered on behind the edge (the web ACL's protected paths).
export const PROTECTED_PATHS: Record<GatedRoute, string> = {
  search: SEARCH_PATH,
  narrate: NARRATE_PATH,
  send: SEND_PATH,
  chat: CHAT_PATH,
};

/** The refusal for a gated call that did not come through its protected pathname, or null to go on. */
export function edgeGateRefusal(route: GatedRoute, pathname: string, on: boolean): EdgeGateRefusal | null {
  if (!on) return null;
  return pathname === PROTECTED_PATHS[route] ? null : { status: 404, body: { error: USE_PROTECTED_PATH } };
}
