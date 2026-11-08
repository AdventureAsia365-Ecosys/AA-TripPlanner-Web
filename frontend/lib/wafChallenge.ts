// AA-1038 R5 hosting (S272) — the AWS WAF Challenge on the protected calls once the site runs behind CloudFront.
//
// The ALB web ACL (AA-CIS-Infra modules/waf, rule TripPlannerChallengePlanner, scoped to this host) answers a call to
//   GET /api/browse/search,   POST /api/trip/narrate   or   POST /api/trip/send
// that carries no valid aws-waf-token with HTTP 202 + `x-amzn-waf-action: challenge`; the app never sees it. The same
// rule challenges a document load of a planner page, which is how a browser gets its token: every in-site way into
// the planner is a full page load (ui/SiteLink.tsx, lib/plannerNav.ts), so the planner's first model call already
// carries it. There is no JavaScript SDK: AWS WAF serves challenge.js only to a web ACL with a paid Bot Control / ATP /
// ACFP group (tech-lead decision S272: none).
// A call that is still challenged (the token expired after its 4 h, or cookies were cleared): one reload of the planner
// page, a document load the rule challenges, at most once per WAF_RELOAD_GAP_MS; otherwise the caller shows the
// botCheck notice. Never a silent empty answer. A challenged request never reached the app, so nothing is lost.
// "Send to advisor" never reloads (the lead form would be lost): lib/api.ts sendToAdvisor answers it as the bot-check
// refusal, whose line asks the traveller to reload the page. Nor does a challenged search or narration while a lead
// form holds typed input (components/LeadForm.tsx holdWafReload, review S272): the form lives only in React state, so
// the call is answered with the botCheck notice instead and the traveller reloads when they choose.
// Off the edge (Vercel, local, CI) no answer is ever a challenge.
//
// No next/* import: `npm test` runs it.

export const WAF_ACTION_HEADER = "x-amzn-waf-action";
export const WAF_RELOAD_KEY = "aa-tripplanner:waf-reload";
export const WAF_RELOAD_GAP_MS = 5 * 60 * 1000;

interface ResponseLike {
  status: number;
  headers: { get(name: string): string | null };
}

// The web ACL's Challenge answer (never the app's).
export function isWafChallenge(res: ResponseLike): boolean {
  return res.status === 202 && (res.headers.get(WAF_ACTION_HEADER) ?? "").trim().toLowerCase() === "challenge";
}

type SessionStore = Pick<Storage, "getItem" | "setItem">;

// Holders that a reload would wipe (a lead form with typed input). Module state: one page, one planner.
const reloadHolds = new Set<symbol>();

// Hold the automatic reload until the returned release runs (a React effect's cleanup). Releasing twice is harmless.
export function holdWafReload(): () => void {
  const hold = Symbol("waf-reload-hold");
  reloadHolds.add(hold);
  return () => void reloadHolds.delete(hold);
}

export function wafReloadHeld(): boolean {
  return reloadHolds.size > 0;
}

// One reload of the planner page per WAF_RELOAD_GAP_MS (the page's document load is challenged and issues the token;
// the trip ids live in localStorage, so the trip survives it). False when a hold is set (holdWafReload: typed input
// would be lost), when it already reloaded recently or when storage is unavailable (no reload loop). A held refusal
// does not use up the gap.
export function reloadForWafToken(
  now: number = Date.now(),
  store?: SessionStore | null,
  reload: () => void = () => window.location.reload(),
): boolean {
  if (wafReloadHeld()) return false;
  try {
    // Reading window.sessionStorage itself throws when site data is blocked.
    const storage = store === undefined ? (typeof window === "undefined" ? null : window.sessionStorage) : store;
    if (!storage) return false;
    const last = Number(storage.getItem(WAF_RELOAD_KEY) ?? 0);
    if (Number.isFinite(last) && now - last < WAF_RELOAD_GAP_MS) return false;
    storage.setItem(WAF_RELOAD_KEY, String(now));
  } catch {
    return false;
  }
  reload();
  return true;
}

// One model call through the edge: send once; a Challenge answer triggers the (rate-limited) reload and is returned
// as is, so the caller treats it as refused (isWafChallenge). Never a second send.
export async function throughWafEdge(
  send: () => Promise<Response>,
  reload: () => boolean = () => reloadForWafToken(),
): Promise<Response> {
  const res = await send();
  if (isWafChallenge(res)) reload();
  return res;
}
