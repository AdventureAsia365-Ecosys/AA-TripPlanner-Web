// AA-1015 (S262) — bot check before the two BFF calls that can reach a model: free-text search (an embedding call)
// and narration (a chat model call). Vercel BotID at the **Basic** level only: free on every Vercel plan, no
// third-party script (the challenge is served from this site's own origin by Vercel), invisible to travellers.
// Deep Analysis (Kasada) is a paid add-on and is never used: every protected path pins `checkLevel: "basic"` on both
// sides (the route-level setting overrides the project's dashboard setting, Vercel docs "Advanced configuration"),
// so turning Deep Analysis on in the dashboard cannot start charging for these calls.
//
// The BotID client patches fetch and attaches its headers to a request whose PATHNAME and method match a protected
// entry (the query string is not looked at). So each protected call has its own pathname, and nothing else (map
// tiles, trip edits) waits for the challenge:
//   GET  /api/browse/search  -> the search branch of /api/browse
//   POST /api/trip/narrate   -> the narrate op of /api/trip
// The server check runs in the shared branch (`resource=search`, `op: "narrate"`) whatever the path, so the old
// spelling (/api/browse?resource=search, POST /api/trip {op: "narrate"}) is refused too when the check is on.
//
// On / off: one build-time value, NEXT_PUBLIC_TP_BOT_CHECK, set by next.config.mjs (`botCheckFlag`) from TP_BOT_CHECK
// ("on" / "off") or, when unset, on for a build on Vercel and off elsewhere (local `next dev` / `next start`, CI,
// node tests). The browser bundle and the route handlers read the same inlined value, so the server never checks
// a request the client did not prepare (that would refuse every traveller). Set TP_BOT_CHECK, never the
// NEXT_PUBLIC_ name; a change needs a new build (a Vercel redeploy).

export const SEARCH_PATH = "/api/browse/search";
export const NARRATE_PATH = "/api/trip/narrate";

// Free level only (see above). Must be the same on the client entry and in checkBotId().
export const BOT_CHECK_LEVEL = "basic" as const;

export interface BotProtectEntry {
  path: string;
  method: string;
  advancedOptions: { checkLevel: typeof BOT_CHECK_LEVEL };
}

// The client list for initBotId (instrumentation-client.ts).
export const BOT_PROTECT: BotProtectEntry[] = [
  { path: SEARCH_PATH, method: "GET", advancedOptions: { checkLevel: BOT_CHECK_LEVEL } },
  { path: NARRATE_PATH, method: "POST", advancedOptions: { checkLevel: BOT_CHECK_LEVEL } },
];

export const BOT_CHECK_FAILED = "bot_check_failed";

// The inlined build value (next.config.mjs `env`). Only the exact "on" turns the check on.
export function botCheckOn(value: string | undefined = process.env.NEXT_PUBLIC_TP_BOT_CHECK): boolean {
  return value === "on";
}

export interface BotVerdict {
  isBot: boolean;
}

export interface BotRefusal {
  status: 403;
  body: { error: typeof BOT_CHECK_FAILED };
}

type Logger = Pick<Console, "log" | "error">;

// The BFF's decision for one protected request, before the Lambda is called (so a refused request is never counted
// by the per-session / per-IP limits and never reaches a model). `check` is BotID's checkBotId (route handlers pass
// it; tests pass a fake). Off -> null without calling it. A bot -> the 403 to answer. A check that throws (the
// Vercel API unreachable, OIDC token missing) lets the request through with an ERROR line: the daily spend cap and
// the per-session / per-IP limits still bound the cost, and search / narration stay up (same fail-open rule as the
// rate counter). One line per refusal, no address or session in it.
export async function botCheckRefusal(
  route: "search" | "narrate",
  on: boolean,
  check: () => Promise<BotVerdict>,
  log: Logger = console,
): Promise<BotRefusal | null> {
  if (!on) return null;
  let verdict: BotVerdict;
  try {
    verdict = await check();
  } catch (e) {
    const detail = e instanceof Error ? e.message : String(e);
    log.error(JSON.stringify({ evt: "bot_check_error", level: "error", route, detail: detail.slice(0, 300) }));
    return null;
  }
  if (!verdict.isBot) return null;
  log.log(JSON.stringify({ evt: "bot_check_refused", route }));
  return { status: 403, body: { error: BOT_CHECK_FAILED } };
}

// The browser side: is this BFF answer a bot-check refusal?
export function isBotRefusal(status: number, body: { error?: string } | null | undefined): boolean {
  return status === 403 && body?.error === BOT_CHECK_FAILED;
}
