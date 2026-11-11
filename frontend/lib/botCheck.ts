// AA-1015 (S262) — the bot check of the BFF calls a script must not reach unchecked: the two that can reach a model
// (free-text search: an embedding call, narration: a chat model call) and, since AA-1038 slice 3 (S272 review),
// "Send to advisor", which stores a traveller's contact for an agency (a script must not fill an agency's inbox or CRM).
//
// Where the check runs (AA-1038 R5 hosting): the site is served by the ECS task behind CloudFront, and the TripPlanner
// rules of the ALB web ACL are the whole bot check (AA-CIS-Infra tripplanner_web_edge.tf, modules/waf): a rate rule
// per viewer IP, a tighter rate rule and a silent JavaScript Challenge on the protected paths below. A browser gets its
// aws-waf-token from the Challenge on the planner's own document load. The app's edge gate (lib/edgeGate.ts) answers
// the older spellings of the same calls 404, so nothing reaches a model or stores a lead outside those rules.
// (Vercel BotID did this job while the site ran on Vercel; it left with the Vercel project, AA-1038.)
//
// Each protected call has its own pathname, so the web ACL matches it (it sees the path, not the query or the body)
// and nothing else (map tiles, trip edits) is challenged:
//   GET  /api/browse/search  -> the search branch of /api/browse
//   POST /api/trip/narrate   -> the narrate op of /api/trip
//   POST /api/trip/send      -> the send op of /api/trip
//   POST /api/trip/chat      -> the chat op of /api/trip (AA-1041: the planning chat, a model call per message)
//
// A challenged call (HTTP 202 from the web ACL, lib/wafChallenge.ts) is shown with the "botCheck" notice
// (lib/aiNotices.ts); a challenged "Send to advisor" is answered to the form as BOT_CHECK_FAILED (lib/api.ts), whose
// line asks the traveller to reload the page.

export const SEARCH_PATH = "/api/browse/search";
export const NARRATE_PATH = "/api/trip/narrate";
export const SEND_PATH = "/api/trip/send";
export const CHAT_PATH = "/api/trip/chat";

export const BOT_CHECK_FAILED = "bot_check_failed";

// The browser side: is this BFF answer a bot-check refusal?
export function isBotRefusal(status: number, body: { error?: string } | null | undefined): boolean {
  return status === 403 && body?.error === BOT_CHECK_FAILED;
}
