// AA-1038 R5 hosting (review S272): the model calls get the AWS WAF token behind CloudFront, retry once after a token
// refresh, and never read the web ACL's Challenge answer as the app's. Run: npm test.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  WAF_ACTION_HEADER,
  WAF_RELOAD_GAP_MS,
  WAF_RELOAD_KEY,
  isWafChallenge,
  reloadForWafToken,
  throughWafEdge,
  wafIntegrationScript,
  type EdgeDeps,
} from "./wafToken.ts";

const read = (p: string) => readFileSync(new URL(p, import.meta.url), "utf8");

const challenge = () => new Response("<html>challenge</html>", { status: 202, headers: { [WAF_ACTION_HEADER]: "challenge" } });
const answer = () => new Response(JSON.stringify({ destinations: [] }), { status: 200 });

function deps(tokens: boolean[]): EdgeDeps & { tokenCalls: number; reloads: number } {
  const d = {
    tokenCalls: 0,
    reloads: 0,
    token: async () => tokens[d.tokenCalls++] ?? false,
    reload: () => {
      d.reloads += 1;
      return true;
    },
  };
  return d;
}

test("the integration script is challenge.js on an AWS WAF https host only", () => {
  const base = "https://abc123.edge.sdk.awswaf.com/abc123/def456/";
  assert.equal(wafIntegrationScript(base), `${base}challenge.js`);
  assert.equal(wafIntegrationScript(base.slice(0, -1)), `${base}challenge.js`);
  assert.equal(wafIntegrationScript(`${base}challenge.js`), `${base}challenge.js`);
  assert.equal(wafIntegrationScript(` ${base} `), `${base}challenge.js`);
  for (const bad of [undefined, "", "not a url", "http://abc.sdk.awswaf.com/x/", "https://example.com/x/",
    "https://awswaf.com.example.com/x/", `${base}?x=1`]) {
    assert.equal(wafIntegrationScript(bad), null, String(bad));
  }
});

test("only a 202 with the challenge action header is the web ACL's challenge", () => {
  assert.equal(isWafChallenge(challenge()), true);
  assert.equal(isWafChallenge(new Response(null, { status: 202, headers: { [WAF_ACTION_HEADER]: " Challenge " } })), true);
  assert.equal(isWafChallenge(new Response(null, { status: 202 })), false);
  assert.equal(isWafChallenge(new Response(null, { status: 405, headers: { [WAF_ACTION_HEADER]: "captcha" } })), false);
  assert.equal(isWafChallenge(answer()), false);
});

test("off the edge or with a valid token: one call, no reload", async () => {
  const d = deps([false]);
  let calls = 0;
  const res = await throughWafEdge(async () => (calls++, answer()), d);
  assert.equal(res.status, 200);
  assert.equal(calls, 1);
  assert.equal(d.reloads, 0);
});

test("a challenged call gets one token refresh and one retry", async () => {
  const d = deps([true, true]);
  const answers = [challenge(), answer()];
  let calls = 0;
  const res = await throughWafEdge(async () => answers[calls++], d);
  assert.equal(res.status, 200);
  assert.equal(calls, 2);
  assert.equal(d.tokenCalls, 2);
  assert.equal(d.reloads, 0);
});

test("no integration: no retry, one reload, the challenge goes back to the caller", async () => {
  const d = deps([false, false]);
  let calls = 0;
  const res = await throughWafEdge(async () => (calls++, challenge()), d);
  assert.equal(isWafChallenge(res), true);
  assert.equal(calls, 1);
  assert.equal(d.reloads, 1);
});

test("still challenged after the refresh: one reload, never a third call", async () => {
  const d = deps([true, true]);
  let calls = 0;
  const res = await throughWafEdge(async () => (calls++, challenge()), d);
  assert.equal(isWafChallenge(res), true);
  assert.equal(calls, 2);
  assert.equal(d.reloads, 1);
});

test("the reload happens at most once per gap and never without storage", () => {
  const store = new Map<string, string>();
  const storage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => void store.set(k, v) };
  let reloads = 0;
  const reload = () => void (reloads += 1);
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
  assert.match(api, /throughWafEdge\(\(\) => fetch\(\s*`\$\{SEARCH_PATH\}/);
  assert.match(api, /if \(isWafChallenge\(res\)\) return \{ pins: \[\], notice: "botCheck" \};/);
  assert.match(api, /path === NARRATE_PATH \? await throughWafEdge\(post\) : await post\(\)/);
  assert.match(api, /ok: res\.ok && !challenged/);
  assert.match(api, /if \(challenged \|\| isBotRefusal\(status, data\)\)/);
});

test("the planner fetches the token as it opens, and the image builds the integration URL in", () => {
  assert.match(read("../app/plan/PlannerApp.tsx"), /useEffect\(\(\) => \{\s*void wafToken\(\);\s*\}, \[\]\);/);
  const docker = read("../Dockerfile");
  assert.match(docker, /^ARG NEXT_PUBLIC_TP_WAF_INTEGRATION_URL=""$/m);
  assert.match(docker, /NEXT_PUBLIC_TP_WAF_INTEGRATION_URL=\$\{NEXT_PUBLIC_TP_WAF_INTEGRATION_URL\}/);
  const deploy = read("../../.github/workflows/deploy-web-dev.yml");
  assert.match(deploy, /--build-arg NEXT_PUBLIC_TP_WAF_INTEGRATION_URL="\$WAF_URL"/);
});
