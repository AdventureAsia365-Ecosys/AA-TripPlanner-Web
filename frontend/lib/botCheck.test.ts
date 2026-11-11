// AA-1015 (S262) / AA-1038: the protected paths of the bot check, and nothing of Vercel BotID left. Run: npm test.
import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { BOT_CHECK_FAILED, CHAT_PATH, NARRATE_PATH, SEARCH_PATH, SEND_PATH, isBotRefusal } from "./botCheck.ts";
import { AI_NOTICES } from "./aiNotices.ts";

const src = (rel: string) => readFileSync(new URL(`../${rel}`, import.meta.url), "utf8");
const ROOT = new URL("..", import.meta.url).pathname;

function sources(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    if (["node_modules", ".next", "public"].includes(name)) continue;
    const p = join(dir, name);
    if (statSync(p).isDirectory()) sources(p, out);
    else if (/\.(ts|tsx|mjs|js)$/.test(name) && !name.endsWith(".test.ts")) out.push(p);
  }
  return out;
}

test("the browser maps only a 403 bot_check_failed to the bot notice, in both languages", () => {
  assert.equal(isBotRefusal(403, { error: BOT_CHECK_FAILED }), true);
  assert.equal(isBotRefusal(403, { error: "forbidden" }), false);
  assert.equal(isBotRefusal(429, { error: BOT_CHECK_FAILED }), false);
  assert.equal(isBotRefusal(403, null), false);
  assert.ok(AI_NOTICES.en.botCheck.includes("reload"));
  assert.ok(AI_NOTICES.vi.botCheck.length > 20);
});

test("each protected path has its route, the planner calls it, and proxy.ts mints the guest cookie on narrate", () => {
  assert.ok(existsSync(new URL(`../app${SEARCH_PATH}/route.ts`, import.meta.url)));
  assert.ok(existsSync(new URL(`../app${NARRATE_PATH}/route.ts`, import.meta.url)));
  assert.ok(existsSync(new URL(`../app${SEND_PATH}/route.ts`, import.meta.url)));
  assert.ok(existsSync(new URL(`../app${CHAT_PATH}/route.ts`, import.meta.url)));
  const api = src("lib/api.ts");
  assert.match(api, /\$\{SEARCH_PATH\}\?q=/);
  assert.match(api, /\}, NARRATE_PATH\);/);
  assert.match(api, /\}, SEND_PATH\);/);
  assert.match(api, /\}, CHAT_PATH\);/);
  assert.doesNotMatch(api, /resource=search/);
  assert.match(src("proxy.ts"), /"\/api\/trip\/narrate"/);
  assert.match(src("proxy.ts"), /"\/api\/trip\/send"/);
  assert.match(src("proxy.ts"), /"\/api\/trip\/chat"/);
});

test("AA-1038: Vercel BotID and @vercel/functions left with the Vercel project (no package, no import, no flag)", () => {
  const pkg = JSON.parse(src("package.json")) as { dependencies?: Record<string, string>; overrides?: object };
  for (const name of ["botid", "@vercel/functions"]) assert.equal(pkg.dependencies?.[name], undefined, name);
  assert.doesNotMatch(JSON.stringify(pkg.overrides ?? {}), /@vercel\//);
  assert.equal(existsSync(new URL("../instrumentation-client.ts", import.meta.url)), false);
  assert.equal(existsSync(new URL("../vercel.json", import.meta.url)), false);
  for (const file of sources(ROOT)) {
    const s = readFileSync(file, "utf8");
    const rel = file.slice(ROOT.length);
    assert.doesNotMatch(s, /from "botid|from "@vercel\//, rel);
    assert.doesNotMatch(s, /checkBotId|initBotId|addCacheTag|dangerouslyDeleteByTag|TP_BOT_CHECK/, rel);
    // Vercel-only response headers: meaningless behind CloudFront (and they would name the old host).
    assert.doesNotMatch(s, /vercel-cdn-cache-control|vercel-cache-tag|x-vercel-enable-rewrite-caching/, rel);
  }
});
