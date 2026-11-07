// AA-1038 R5 hosting: behind the CloudFront web ACL the model calls answer only on their protected paths. Run: npm test.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { NARRATE_PATH, SEARCH_PATH } from "./botCheck.ts";
import { USE_PROTECTED_PATH, edgeGateOn, edgeGateRefusal } from "./edgeGate.ts";

const read = (p: string) => readFileSync(new URL(p, import.meta.url), "utf8");

test("only the exact value on turns the gate on", () => {
  assert.equal(edgeGateOn({}), false);
  assert.equal(edgeGateOn({ TP_EDGE_GATE: "off" }), false);
  assert.equal(edgeGateOn({ TP_EDGE_GATE: " ON " }), true);
  assert.equal(edgeGateOn({ TP_EDGE_GATE: "1" }), false);
});

test("gate off: every spelling goes on (Vercel, local, CI)", () => {
  assert.equal(edgeGateRefusal("search", "/api/browse", false), null);
  assert.equal(edgeGateRefusal("narrate", "/api/trip", false), null);
});

test("gate on: the protected paths go on, the older spellings get 404 use_protected_path", () => {
  assert.equal(edgeGateRefusal("search", SEARCH_PATH, true), null);
  assert.equal(edgeGateRefusal("narrate", NARRATE_PATH, true), null);
  assert.deepEqual(edgeGateRefusal("search", "/api/browse", true), { status: 404, body: { error: USE_PROTECTED_PATH } });
  assert.deepEqual(edgeGateRefusal("narrate", "/api/trip", true), { status: 404, body: { error: USE_PROTECTED_PATH } });
  // The other model's path never stands in.
  assert.notEqual(edgeGateRefusal("search", NARRATE_PATH, true), null);
});

test("both model branches check the gate, and search keeps its protected pathname", () => {
  const browse = read("../app/api/browse/route.ts");
  const trip = read("../app/api/trip/route.ts");
  const search = read("../app/api/browse/search/route.ts");
  assert.match(browse, /edgeGateRefusal\("search", req\.nextUrl\.pathname, edgeGateOn\(\)\)/);
  assert.match(trip, /edgeGateRefusal\("narrate", req\.nextUrl\.pathname, edgeGateOn\(\)\)/);
  // The search route must not rewrite the pathname to /api/browse, or the gate would refuse every search.
  assert.doesNotMatch(search, /pathname\s*=/);
});

test("the web ACL paths of AA-CIS-Infra are the two protected paths", () => {
  assert.equal(SEARCH_PATH, "/api/browse/search");
  assert.equal(NARRATE_PATH, "/api/trip/narrate");
});
