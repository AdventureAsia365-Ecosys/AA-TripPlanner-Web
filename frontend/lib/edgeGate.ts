// AA-1038 R5 hosting — the edge gate of the two BFF calls that can reach a model (search: an embedding call,
// narration: a chat model call) once the site runs on ECS behind CloudFront.
//
// On ECS the BotID check is off (it needs the Vercel edge; lib/botCheck.ts) and the CLOUDFRONT-scope web ACL takes
// its place (AA-CIS-Infra tripplanner_web_edge.tf): a rate rule and a Challenge on the URI paths
//   GET  /api/browse/search   and   POST /api/trip/narrate.
// A WAF rule sees the path, not the query string or the JSON body, so the older spellings of the same calls
// (GET /api/browse?resource=search, POST /api/trip {op: "narrate"}) would pass the edge unchallenged. With the gate on,
// the BFF answers those spellings 404 `use_protected_path` before any Lambda call, so every model call has passed
// the edge rules. The planner already calls only the two protected paths (AA-1015).
//
// On / off: the runtime env TP_EDGE_GATE ("on" only on the ECS task, set by Terraform), read per request. Off
// (Vercel, local, CI) keeps today's behaviour, where BotID guards the same two calls in the shared branch.
//
// No next/* import: `npm test` runs it.

import { NARRATE_PATH, SEARCH_PATH } from "./botCheck.ts";

export const USE_PROTECTED_PATH = "use_protected_path";

export function edgeGateOn(env: Record<string, string | undefined> = process.env): boolean {
  return (env.TP_EDGE_GATE ?? "").trim().toLowerCase() === "on";
}

export interface EdgeGateRefusal {
  status: 404;
  body: { error: typeof USE_PROTECTED_PATH };
}

/** The refusal for a model call that did not come through its protected pathname, or null to go on. */
export function edgeGateRefusal(route: "search" | "narrate", pathname: string, on: boolean): EdgeGateRefusal | null {
  if (!on) return null;
  const protectedPath = route === "search" ? SEARCH_PATH : NARRATE_PATH;
  return pathname === protectedPath ? null : { status: 404, body: { error: USE_PROTECTED_PATH } };
}
