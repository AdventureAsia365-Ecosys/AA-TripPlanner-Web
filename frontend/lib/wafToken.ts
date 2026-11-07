// AA-1038 R5 hosting (review S272) — the AWS WAF token of the two model calls once the site runs behind CloudFront.
//
// The CLOUDFRONT-scope web ACL (AA-CIS-Infra tripplanner_web_edge.tf, rule ChallengePlanner) answers a call to
//   GET /api/browse/search   or   POST /api/trip/narrate
// that carries no valid aws-waf-token with HTTP 202 + `x-amzn-waf-action: challenge`; the app never sees it. The rule
// also challenges a document load of a planner page, but most ways into the planner are client-side Next navigations
// (header link, home search, tour and destination CTAs: an RSC fetch, never challenged), so the planner cannot count on
// a token from its own page load. So:
//   1. the planner loads the web ACL's JavaScript integration (challenge.js from its application integration URL,
//      build value NEXT_PUBLIC_TP_WAF_INTEGRATION_URL) and asks it for a token before each model call
//      (AwsWafIntegration.getToken() fetches or refreshes the token in the background and stores the aws-waf-token
//      cookie, so the same-origin fetch carries it; this also covers the token's immunity time running out);
//   2. a call that is still challenged gets one token refresh and one retry (a challenged request never reached the
//      app, so a retry of the POST is safe);
//   3. still challenged (no integration, or it failed to load): one reload of the planner page, a document load the
//      rule challenges, at most once per WAF_RELOAD_GAP_MS; otherwise the caller shows the botCheck notice. Never a
//      silent empty answer.
// Off the edge (Vercel, local, CI: no integration URL) step 1 is a no-op and no answer is ever a challenge.
//
// No next/* import: `npm test` runs it.

export const WAF_ACTION_HEADER = "x-amzn-waf-action";
export const WAF_RELOAD_KEY = "aa-tripplanner:waf-reload";
export const WAF_RELOAD_GAP_MS = 5 * 60 * 1000;
export const WAF_WAIT_MS = 5000;

// The challenge.js URL of the web ACL's integration URL (Terraform output tripplanner_web_edge.waf_integration_url,
// repository variable TP_WAF_INTEGRATION_URL, a build arg of the image). Only an https URL on an AWS WAF host; anything
// else is null (no script is loaded).
export function wafIntegrationScript(
  value: string | undefined = process.env.NEXT_PUBLIC_TP_WAF_INTEGRATION_URL,
): string | null {
  const v = (value ?? "").trim();
  if (!v) return null;
  let u: URL;
  try {
    u = new URL(v);
  } catch {
    return null;
  }
  if (u.protocol !== "https:" || !u.hostname.endsWith(".awswaf.com") || u.search || u.hash || u.username) return null;
  const path = u.pathname.endsWith("/challenge.js") ? u.pathname : `${u.pathname.replace(/\/?$/, "/")}challenge.js`;
  return `${u.origin}${path}`;
}

interface ResponseLike {
  status: number;
  headers: { get(name: string): string | null };
}

// The web ACL's Challenge answer (never the app's).
export function isWafChallenge(res: ResponseLike): boolean {
  return res.status === 202 && (res.headers.get(WAF_ACTION_HEADER) ?? "").trim().toLowerCase() === "challenge";
}

export interface EdgeDeps {
  // Get or refresh the token; false when there is no integration or it failed.
  token: () => Promise<boolean>;
  // Reload the planner page once (a challenged document load); false when it was done too recently.
  reload: () => boolean;
}

// One model call through the edge (steps 1-3 above). Returns the last answer: the caller treats one that is still a
// challenge (isWafChallenge) as refused.
export async function throughWafEdge(send: () => Promise<Response>, deps: EdgeDeps = browserEdgeDeps): Promise<Response> {
  await deps.token();
  let res = await send();
  if (!isWafChallenge(res)) return res;
  if (await deps.token()) {
    res = await send();
    if (!isWafChallenge(res)) return res;
  }
  deps.reload();
  return res;
}

// --- browser side ---------------------------------------------------------------------------------------------------

export interface WafIntegration {
  getToken(): Promise<string>;
}

type WafWindow = { AwsWafIntegration?: WafIntegration };

let loading: Promise<WafIntegration | null> | null = null;

function withTimeout<T>(p: Promise<T>, fallback: T, ms: number = WAF_WAIT_MS): Promise<T> {
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(fallback), ms);
    p.then(
      (v) => {
        clearTimeout(timer);
        resolve(v);
      },
      () => {
        clearTimeout(timer);
        resolve(fallback);
      },
    );
  });
}

// Loads challenge.js once per page (the planner preloads it on mount). Null off the edge or when it does not load.
export function loadWafIntegration(src: string | null = wafIntegrationScript()): Promise<WafIntegration | null> {
  if (typeof window === "undefined" || !src) return Promise.resolve(null);
  const w = window as unknown as WafWindow;
  if (w.AwsWafIntegration) return Promise.resolve(w.AwsWafIntegration);
  if (loading) return loading;
  const p = new Promise<WafIntegration | null>((resolve) => {
    const s = document.createElement("script");
    s.src = src;
    s.async = true;
    s.onload = () => resolve(w.AwsWafIntegration ?? null);
    s.onerror = () => resolve(null);
    document.head.appendChild(s);
  });
  loading = withTimeout(p, null).then((api) => {
    if (!api) loading = null; // a failed load is tried again on the next call
    return api;
  });
  return loading;
}

// Gets (or refreshes) the aws-waf-token cookie. Safe to call before every model call: the integration answers from
// its stored token while it is valid.
export async function wafToken(): Promise<boolean> {
  const api = await loadWafIntegration();
  if (!api) return false;
  return withTimeout(
    api.getToken().then((t) => Boolean(t)),
    false,
  );
}

type SessionStore = Pick<Storage, "getItem" | "setItem">;

// One reload of the planner page per WAF_RELOAD_GAP_MS (the page's document load is challenged and issues the token;
// the trip ids live in localStorage, so the trip survives it). False when it already reloaded recently or storage is
// unavailable (no reload loop).
export function reloadForWafToken(
  now: number = Date.now(),
  store?: SessionStore | null,
  reload: () => void = () => window.location.reload(),
): boolean {
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

export const browserEdgeDeps: EdgeDeps = {
  token: wafToken,
  reload: () => reloadForWafToken(),
};
