// AA-1015 (S262): the BotID Basic check before search and narration. Run: npm test.
import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import {
  BOT_CHECK_FAILED,
  BOT_CHECK_LEVEL,
  BOT_PROTECT,
  NARRATE_PATH,
  SEARCH_PATH,
  botCheckOn,
  botCheckRefusal,
  isBotRefusal,
} from "./botCheck.ts";
import { AI_NOTICES } from "./aiNotices.ts";
import { botCheckFlag } from "../next.config.mjs";

const src = (rel: string) => readFileSync(new URL(`../${rel}`, import.meta.url), "utf8");

function recorder() {
  const lines: string[] = [];
  return { lines, log: { log: (s: string) => lines.push(s), error: (s: string) => lines.push(s) } };
}

test("TP_BOT_CHECK wins; unset = on for a Vercel build and off elsewhere", () => {
  assert.equal(botCheckFlag({ TP_BOT_CHECK: "on" }), "on");
  assert.equal(botCheckFlag({ TP_BOT_CHECK: " OFF ", VERCEL: "1" }), "off");
  assert.equal(botCheckFlag({ VERCEL: "1" }), "on");
  assert.equal(botCheckFlag({ VERCEL: "1", TP_BOT_CHECK: "" }), "on");
  assert.equal(botCheckFlag({}), "off");
  assert.equal(botCheckFlag({ TP_BOT_CHECK: "maybe" }), "off");
  // only the exact inlined "on" turns the handlers and the client on (tests run with nothing inlined: off)
  assert.equal(botCheckOn("on"), true);
  for (const v of [undefined, "", "off", "ON", "1"]) assert.equal(botCheckOn(v), false, String(v));
  assert.equal(botCheckOn(), false);
});

test("off: the check is never called and nothing is refused", async () => {
  let called = 0;
  const r = await botCheckRefusal("search", false, async () => {
    called += 1;
    return { isBot: true };
  });
  assert.equal(r, null);
  assert.equal(called, 0);
});

test("on: a bot gets 403 bot_check_failed and one line without request data; a human goes through", async () => {
  const rec = recorder();
  const refused = await botCheckRefusal("narrate", true, async () => ({ isBot: true }), rec.log);
  assert.deepEqual(refused, { status: 403, body: { error: "bot_check_failed" } });
  assert.deepEqual(rec.lines.map((l) => JSON.parse(l)), [{ evt: "bot_check_refused", route: "narrate" }]);
  assert.equal(await botCheckRefusal("search", true, async () => ({ isBot: false }), rec.log), null);
  assert.equal(rec.lines.length, 1);
});

test("on: a check that throws lets the request through with an ERROR line (limits and spend cap still apply)", async () => {
  const rec = recorder();
  const r = await botCheckRefusal(
    "search",
    true,
    async () => {
      throw new Error("The 'x-vercel-oidc-token' header is missing from the request.");
    },
    rec.log,
  );
  assert.equal(r, null);
  const line = JSON.parse(rec.lines[0]);
  assert.equal(line.evt, "bot_check_error");
  assert.equal(line.level, "error");
  assert.equal(line.route, "search");
});

test("the browser maps only a 403 bot_check_failed to the bot notice, in both languages", () => {
  assert.equal(isBotRefusal(403, { error: BOT_CHECK_FAILED }), true);
  assert.equal(isBotRefusal(403, { error: "forbidden" }), false);
  assert.equal(isBotRefusal(429, { error: BOT_CHECK_FAILED }), false);
  assert.equal(isBotRefusal(403, null), false);
  assert.ok(AI_NOTICES.en.botCheck.includes("reload"));
  assert.ok(AI_NOTICES.vi.botCheck.length > 20);
});

test("only the free Basic level, on both protected paths, and the server pins the same level", () => {
  assert.equal(BOT_CHECK_LEVEL, "basic");
  assert.deepEqual(
    BOT_PROTECT.map((p) => [p.path, p.method, p.advancedOptions.checkLevel]),
    [
      [SEARCH_PATH, "GET", "basic"],
      [NARRATE_PATH, "POST", "basic"],
    ],
  );
  for (const route of ["app/api/browse/route.ts", "app/api/trip/route.ts"]) {
    const s = src(route);
    assert.match(s, /checkBotId\(\{ advancedOptions: \{ checkLevel: BOT_CHECK_LEVEL \} \}\)/, route);
    assert.doesNotMatch(s, /deepAnalysis/, route);
  }
  assert.doesNotMatch(src("instrumentation-client.ts"), /deepAnalysis/);
});

test("each protected path has its route, the planner calls it, and proxy.ts mints the guest cookie on narrate", () => {
  assert.ok(existsSync(new URL(`../app${SEARCH_PATH}/route.ts`, import.meta.url)));
  assert.ok(existsSync(new URL(`../app${NARRATE_PATH}/route.ts`, import.meta.url)));
  const api = src("lib/api.ts");
  assert.match(api, /\$\{SEARCH_PATH\}\?q=/);
  assert.match(api, /\}, NARRATE_PATH\);/);
  assert.doesNotMatch(api, /resource=search/);
  assert.match(src("proxy.ts"), /"\/api\/trip\/narrate"/);
  // the client init is gated by the same build value as the handlers
  assert.match(src("instrumentation-client.ts"), /if \(botCheckOn\(\)\) initBotId\(\{ protect: BOT_PROTECT \}\)/);
  assert.match(src("next.config.mjs"), /NEXT_PUBLIC_TP_BOT_CHECK: botCheckFlag\(\)/);
});

test("the check runs before the Lambda call in both handlers", () => {
  const browse = src("app/api/browse/route.ts");
  assert.ok(browse.indexOf('botCheckRefusal("search"') < browse.indexOf("await fetch(`${BROWSE_API_URL}"));
  assert.ok(browse.indexOf('botCheckRefusal("search"') < browse.indexOf("aiLimitHeaders("));
  const trip = src("app/api/trip/route.ts");
  const post = trip.slice(trip.indexOf("export async function POST"));
  assert.ok(post.indexOf('botCheckRefusal("narrate"') > 0);
  assert.ok(post.indexOf('botCheckRefusal("narrate"') < post.indexOf("await fetch(`${TRIP_API_URL}"));
});
