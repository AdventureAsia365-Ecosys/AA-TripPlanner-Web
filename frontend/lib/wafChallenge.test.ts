// AA-1038 R5 hosting (S272): the two model calls never read the web ACL's Challenge answer as the app's, reload the
// planner at most once per 5 minutes for a new token, and nothing loads AWS WAF's JavaScript SDK. Run: npm test.
import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import {
  WAF_ACTION_HEADER,
  WAF_RELOAD_GAP_MS,
  WAF_RELOAD_KEY,
  isWafChallenge,
  reloadForWafToken,
  throughWafEdge,
} from "./wafChallenge.ts";

const read = (p: string) => readFileSync(new URL(p, import.meta.url), "utf8");

const challenge = () => new Response("<html>challenge</html>", { status: 202, headers: { [WAF_ACTION_HEADER]: "challenge" } });
const answer = () => new Response(JSON.stringify({ destinations: [] }), { status: 200 });

test("only a 202 with x-amzn-waf-action: challenge is the web ACL's answer", () => {
  assert.equal(isWafChallenge(challenge()), true);
  assert.equal(isWafChallenge(new Response("", { status: 202, headers: { [WAF_ACTION_HEADER]: " Challenge " } })), true);
  assert.equal(isWafChallenge(new Response("", { status: 202 })), false); // an app 202
  assert.equal(isWafChallenge(new Response("", { status: 200, headers: { [WAF_ACTION_HEADER]: "challenge" } })), false);
  assert.equal(isWafChallenge(new Response("", { status: 405, headers: { [WAF_ACTION_HEADER]: "captcha" } })), false);
});

test("an answer goes through untouched, with no reload", async () => {
  let reloads = 0;
  let calls = 0;
  const res = await throughWafEdge(async () => (calls++, answer()), () => (reloads++, true));
  assert.equal(res.status, 200);
  assert.equal(calls, 1);
  assert.equal(reloads, 0);
});

test("a challenge asks for one reload and is returned as is, never sent twice", async () => {
  let reloads = 0;
  let calls = 0;
  const res = await throughWafEdge(async () => (calls++, challenge()), () => (reloads++, false));
  assert.equal(isWafChallenge(res), true);
  assert.equal(calls, 1);
  assert.equal(reloads, 1);
});

test("the reload happens at most once per gap and never without storage", () => {
  const store = new Map<string, string>();
  const storage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => void store.set(k, v) };
  let reloads = 0;
  const reload = () => void (reloads += 1);
  assert.equal(WAF_RELOAD_GAP_MS, 5 * 60 * 1000);
  assert.equal(reloadForWafToken(1_000_000, storage, reload), true);
  assert.equal(store.get(WAF_RELOAD_KEY), "1000000");
  assert.equal(reloadForWafToken(1_000_000 + WAF_RELOAD_GAP_MS - 1, storage, reload), false);
  assert.equal(reloadForWafToken(1_000_000 + WAF_RELOAD_GAP_MS, storage, reload), true);
  assert.equal(reloads, 2);
  assert.equal(reloadForWafToken(5, null, reload), false);
  const broken = { getItem: () => { throw new Error("blocked"); }, setItem: () => {} };
  assert.equal(reloadForWafToken(5, broken, reload), false);
  assert.equal(reloads, 2);
});

test("search and narrate go through the edge step and never read a challenge as an answer", () => {
  const api = read("./api.ts");
  assert.match(api, /throughWafEdge\(\(\) => tenantFetch\(\s*`\$\{SEARCH_PATH\}/);
  assert.match(api, /if \(isWafChallenge\(res\)\) return \{ pins: \[\], notice: "botCheck" \};/);
  assert.match(api, /path === NARRATE_PATH \? await throughWafEdge\(post\) : await post\(\)/);
  assert.match(api, /ok: res\.ok && !challenged/);
  assert.match(api, /if \(challenged \|\| isBotRefusal\(status, data\)\)/);
});

test("a challenged Send to advisor is the bot-check refusal, without a reload that would lose the form", () => {
  const api = read("./api.ts");
  const send = api.slice(api.indexOf("export async function sendToAdvisor"), api.indexOf("export interface Suggestion"));
  // Only the narrate path reloads the planner; the send path never goes through throughWafEdge.
  assert.doesNotMatch(send, /throughWafEdge|reloadForWafToken/);
  assert.match(api, /path === NARRATE_PATH \? await throughWafEdge\(post\) : await post\(\)/);
  assert.match(send, /\}, SEND_PATH\);/);
  assert.match(send, /if \(challenged\) return \{ status: 403, body: \{ error: BOT_CHECK_FAILED \}/);
});

test("no AWS WAF JavaScript SDK: no challenge.js loader, no integration URL in the build or the deploy", () => {
  assert.equal(existsSync(new URL("./wafToken.ts", import.meta.url)), false);
  const files = [
    "./api.ts",
    "../app/plan/PlannerApp.tsx",
    "../Dockerfile",
    "../next.config.mjs",
    "../../.github/workflows/deploy-web-dev.yml",
  ];
  for (const f of files) {
    const text = read(f);
    assert.doesNotMatch(text, /TP_WAF_INTEGRATION_URL|AwsWafIntegration|awswaf\.com|wafToken/, f);
  }
});
