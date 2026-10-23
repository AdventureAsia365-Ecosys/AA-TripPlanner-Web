// AA-837 (D11): Customise on a tour with no planner places must not log a red 404 in the console.
// Run: npm test (node --test with type stripping; no extra dependencies).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readTourRoute, TOUR_ROUTE_NONE, tourRouteBffAnswer } from "./tourRoute.ts";

const FALLBACK = {
  title: "Sri Lanka Hill Country Trek",
  country: "Sri Lanka",
  duration_days: 10,
  days: [{ day: 1, title: "Colombo" }],
};

test("the BFF turns the Lambda's 'no planner route' 404 into a 200 with match none", () => {
  const a = tourRouteBffAnswer(404, JSON.stringify({ error: "no planner route for this tour", tour: FALLBACK }));
  assert.equal(a.status, 200);
  assert.equal(a.rewritten, true);
  assert.deepEqual(JSON.parse(a.body), { match: TOUR_ROUTE_NONE, tour: FALLBACK });
});

test("an unknown tour or a non-JSON 404 is still 'none', without tour details", () => {
  for (const body of [JSON.stringify({ error: "no planner route for this tour", tour: null }), "<html>Not Found</html>", ""]) {
    const a = tourRouteBffAnswer(404, body);
    assert.equal(a.status, 200);
    assert.deepEqual(JSON.parse(a.body), { match: "none", tour: null });
  }
});

test("every other upstream status passes through untouched", () => {
  for (const [status, body] of [[200, '{"proposal":{}}'], [400, '{"error":"tour_id must be a uuid"}'], [500, "boom"], [503, ""]] as const) {
    assert.deepEqual(tourRouteBffAnswer(status, body), { status, body, rewritten: false });
  }
});

test("the client reads match none (and a legacy 404) as missing, with the fallback", () => {
  assert.deepEqual(readTourRoute(200, { match: "none", tour: FALLBACK }), { missing: true, tour: FALLBACK });
  assert.deepEqual(readTourRoute(404, { error: "x", tour: FALLBACK }), { missing: true, tour: FALLBACK });
  assert.deepEqual(readTourRoute(404, null), { missing: true, tour: null });
});

test("the client reads a proposal, and anything else that failed is an error", () => {
  const proposal = { total_days: 10, transfers: 0, transfer_km: 0, whole_tour: true, segments: [], days: [] };
  assert.deepEqual(readTourRoute(200, { tour_id: "t", proposal }), proposal);
  assert.deepEqual(readTourRoute(200, {}), { missing: true, tour: null });
  assert.equal(readTourRoute(500, null), "error");
  assert.equal(readTourRoute(400, { error: "bad" }), "error");
});
