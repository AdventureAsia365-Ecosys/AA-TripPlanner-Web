// AA-909: the /api/trip BFF forwards the guest token on the four trip reads, not only on mutations.
// Run: npm test (node --test with type stripping; no extra dependencies).
import { test } from "node:test";
import assert from "node:assert/strict";
import { GUEST_TOKEN_HEADER } from "./guestToken.ts";
import { EMPTY_TRIP_BODY, parseTripBody, tripHeaders, tripReadAnswer, tripReadPath } from "./tripProxy.ts";

const TRIP = "00000000-0000-4000-8000-0000000000b1";
const DEST = "00000000-0000-4000-8000-0000000000d1";
const TOKEN = "M".repeat(43);

function params(p: Record<string, string>) {
  const q = new URLSearchParams(p);
  return { get: (k: string) => q.get(k) };
}

test("the Lambda headers carry the shared key and the guest token", () => {
  const h = tripHeaders("key-1", TOKEN);
  assert.equal(h["x-tripplanner-key"], "key-1");
  assert.equal(h[GUEST_TOKEN_HEADER], TOKEN);
  assert.equal(GUEST_TOKEN_HEADER, "x-tripplanner-guest");
});

test("no token, no guest header (the Lambda then answers a read with 404)", () => {
  for (const t of [null, undefined, ""]) {
    assert.equal(GUEST_TOKEN_HEADER in tripHeaders("key-1", t), false);
  }
  assert.equal("x-tripplanner-key" in tripHeaders("", TOKEN), false);
});

test("each read maps to its assembly Lambda path", () => {
  assert.deepEqual(tripReadPath(params({ trip_id: TRIP })), { path: `/trip/${TRIP}` });
  assert.deepEqual(tripReadPath(params({ trip_id: TRIP, resource: "coverage" })), {
    path: `/trip/${TRIP}/coverage`,
  });
  assert.deepEqual(
    tripReadPath(params({ trip_id: TRIP, resource: "activities", destination_id: DEST })),
    { path: `/trip/${TRIP}/stops/${DEST}/activities` },
  );
  assert.deepEqual(tripReadPath(params({ trip_id: TRIP, resource: "suggestions" })), {
    path: `/trip/${TRIP}/suggestions`,
  });
  assert.deepEqual(
    tripReadPath(params({ trip_id: TRIP, resource: "suggestions", activity: "trekking", other: "x" })),
    { path: `/trip/${TRIP}/suggestions?activity=trekking` },
  );
});

test("a read without trip_id or destination_id is refused, and ids are URL-encoded", () => {
  assert.deepEqual(tripReadPath(params({})), { error: "trip_id required" });
  assert.deepEqual(tripReadPath(params({ trip_id: TRIP, resource: "activities" })), {
    error: "destination_id required",
  });
  assert.deepEqual(tripReadPath(params({ trip_id: "a/../b" })), { path: "/trip/a%2F..%2Fb" });
});

test("the POST body must be one JSON object, else the route answers 400", () => {
  assert.deepEqual(parseTripBody('{"op":"add","trip_id":"t1"}'), { op: "add", trip_id: "t1" });
  for (const raw of ["", "not json", "null", "[]", "42", '"op"', "{"]) {
    assert.equal(parseTripBody(raw), null, raw);
  }
});

// AA-837: a first visit to /plan reads a trip the Lambda does not have yet (404) — the browser gets a
// 200 empty trip, so the console stays clean; nothing else is rewritten.
test("the itinerary read's 404 reaches the browser as a 200 empty trip", () => {
  const a = tripReadAnswer("itinerary", 404, '{"error":"not found"}');
  assert.equal(a.status, 200);
  assert.deepEqual(JSON.parse(a.body), { itinerary: [], custom_days: [] });
  assert.equal(a.body, EMPTY_TRIP_BODY);
});

test("other reads and other statuses pass through untouched", () => {
  for (const op of ["suggestions", "coverage", "activities"]) {
    assert.deepEqual(tripReadAnswer(op, 404, '{"error":"not found"}'), { status: 404, body: '{"error":"not found"}' });
  }
  assert.deepEqual(tripReadAnswer("itinerary", 200, '{"itinerary":[1]}'), { status: 200, body: '{"itinerary":[1]}' });
  assert.deepEqual(tripReadAnswer("itinerary", 500, "boom"), { status: 500, body: "boom" });
});
