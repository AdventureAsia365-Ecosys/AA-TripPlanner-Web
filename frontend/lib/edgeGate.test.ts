// AA-1038 R5 hosting: behind the web ACL the model calls and Send to advisor answer only on their protected paths.
// Run: npm test.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { CHAT_PATH, NARRATE_PATH, SEARCH_PATH, SEND_PATH } from "./botCheck.ts";
import { PROTECTED_PATHS, USE_PROTECTED_PATH, edgeGateOn, edgeGateRefusal } from "./edgeGate.ts";

const read = (p: string) => readFileSync(new URL(p, import.meta.url), "utf8");

test("only the exact value on turns the gate on", () => {
  assert.equal(edgeGateOn({}), false);
  assert.equal(edgeGateOn({ TP_EDGE_GATE: "off" }), false);
  assert.equal(edgeGateOn({ TP_EDGE_GATE: " ON " }), true);
  assert.equal(edgeGateOn({ TP_EDGE_GATE: "1" }), false);
});

test("gate off: every spelling goes on (local next dev / start, CI)", () => {
  assert.equal(edgeGateRefusal("search", "/api/browse", false), null);
  assert.equal(edgeGateRefusal("narrate", "/api/trip", false), null);
  assert.equal(edgeGateRefusal("send", "/api/trip", false), null);
});

test("gate on: the protected paths go on, the older spellings get 404 use_protected_path", () => {
  assert.equal(edgeGateRefusal("search", SEARCH_PATH, true), null);
  assert.equal(edgeGateRefusal("narrate", NARRATE_PATH, true), null);
  assert.equal(edgeGateRefusal("send", SEND_PATH, true), null);
  assert.deepEqual(edgeGateRefusal("search", "/api/browse", true), { status: 404, body: { error: USE_PROTECTED_PATH } });
  assert.deepEqual(edgeGateRefusal("narrate", "/api/trip", true), { status: 404, body: { error: USE_PROTECTED_PATH } });
  // Review S272: "Send to advisor" stores a traveller's contact; on ECS only the web ACL checks it, so the older
  // spelling POST /api/trip {op: "send"} is refused like the model calls.
  assert.deepEqual(edgeGateRefusal("send", "/api/trip", true), { status: 404, body: { error: USE_PROTECTED_PATH } });
  // Another call's path never stands in.
  assert.notEqual(edgeGateRefusal("search", NARRATE_PATH, true), null);
  assert.notEqual(edgeGateRefusal("send", NARRATE_PATH, true), null);
  assert.notEqual(edgeGateRefusal("narrate", SEND_PATH, true), null);
});

test("every gated branch checks the gate, and search keeps its protected pathname", () => {
  const browse = read("../app/api/browse/route.ts");
  const trip = read("../app/api/trip/route.ts");
  const search = read("../app/api/browse/search/route.ts");
  assert.match(browse, /edgeGateRefusal\("search", req\.nextUrl\.pathname, edgeGateOn\(\)\)/);
  // One gate call for every op of the shared branch (narrate, send and, AA-1041, chat), before the Lambda.
  // Plain string search, no regex (CodeQL js/redos): only blank or comment lines may sit between the two.
  const branch = 'if (b.op === "narrate" || b.op === "send" || b.op === "chat") {';
  const gateCall = "const gated = edgeGateRefusal(b.op, req.nextUrl.pathname, edgeGateOn());";
  const at = trip.indexOf(branch);
  const callAt = trip.indexOf(gateCall, at);
  assert.ok(at >= 0 && callAt > at, "the shared narrate/send/chat branch calls the gate");
  const between = trip.slice(at + branch.length, callAt).split("\n").map((l) => l.trim());
  assert.deepEqual(between.filter((l) => l !== "" && !l.startsWith("//")), []);
  assert.ok(trip.indexOf("edgeGateRefusal(b.op") < trip.indexOf("await fetch(`${TRIP_API_URL}${path}`"));
  // The search route must not rewrite the pathname to /api/browse, or the gate would refuse every search.
  assert.doesNotMatch(search, /pathname\s*=/);
});

test("a dot-segment spelling reaches the gate as the protected path, so the web ACL must normalize too", () => {
  // Next builds req.nextUrl with WHATWG new URL. These spellings resolve to the model routes, and the gate lets them
  // through: only the web ACL's URL_DECODE + NORMALIZE_PATH and its CONTAINS match (tripplanner_web_edge.tf,
  // modules/waf) put them under rules 3 and 4. A leading back-reference survives NORMALIZE_PATH (review S272).
  const resolved = (raw: string) => new URL(raw, "http://tp.invalid").pathname;
  for (const raw of ["/api/browse/%2e/search", "/api/browse/%2E/search", "/api/browse/./search", "/api/x/../browse/search"]) {
    assert.equal(resolved(raw), SEARCH_PATH, raw);
    assert.equal(edgeGateRefusal("search", resolved(raw), true), null, raw);
  }
  for (const raw of ["/../api/browse/search", "/%2e%2e/api/browse/search"]) {
    assert.equal(resolved(raw), SEARCH_PATH, raw);
    assert.equal(edgeGateRefusal("search", resolved(raw), true), null, raw);
  }
  for (const raw of ["/api/trip/%2e/narrate", "/api/trip/./narrate", "/./api/trip/narrate", "/%2e%2e/api/trip/narrate"]) {
    assert.equal(resolved(raw), NARRATE_PATH, raw);
    assert.equal(edgeGateRefusal("narrate", resolved(raw), true), null, raw);
  }
  for (const raw of ["/api/trip/%2e/chat", "/api/trip/./chat", "/%2e%2e/api/trip/chat"]) {
    assert.equal(resolved(raw), CHAT_PATH, raw);
    assert.equal(edgeGateRefusal("chat", resolved(raw), true), null, raw);
  }
  for (const raw of ["/api/trip/%2e/send", "/api/x/../trip/send", "/%2e%2e/api/trip/send", "/%2E%2E/api/trip/send"]) {
    assert.equal(resolved(raw), SEND_PATH, raw);
    assert.equal(edgeGateRefusal("send", resolved(raw), true), null, raw);
  }
  // An encoded letter stays encoded, so it is not the protected path (and the route never matches it).
  assert.notEqual(resolved("/api/browse/%73earch"), SEARCH_PATH);
  assert.notEqual(edgeGateRefusal("search", resolved("/api/browse/%73earch"), true), null);
});

test("the web ACL paths of AA-CIS-Infra (local.tp_web_protected_paths) are the four protected paths", () => {
  assert.equal(SEARCH_PATH, "/api/browse/search");
  assert.equal(NARRATE_PATH, "/api/trip/narrate");
  assert.equal(SEND_PATH, "/api/trip/send");
  assert.equal(CHAT_PATH, "/api/trip/chat");
  assert.deepEqual(Object.values(PROTECTED_PATHS),
    ["/api/browse/search", "/api/trip/narrate", "/api/trip/send", "/api/trip/chat"]);
});

test("the edge gate covers every protected path of lib/botCheck.ts (the web ACL is their only bot check)", () => {
  // Review S272: Send to advisor was once missing from the edge list. A protected path added to lib/botCheck.ts must
  // be added here and to AA-CIS-Infra local.tp_web_protected_paths in the same change.
  const declared = [...read("./botCheck.ts").matchAll(/^export const \w+_PATH = "([^"]+)";$/gm)].map((m) => m[1]);
  assert.deepEqual(declared.sort(), Object.values(PROTECTED_PATHS).sort());
});
