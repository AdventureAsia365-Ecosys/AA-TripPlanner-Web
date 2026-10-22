// AA-909: the /api/trip BFF forwards the guest token on the four trip reads, not only on mutations.
// Run: npm test (node --test with type stripping; no extra dependencies).
import { test } from "node:test";
import assert from "node:assert/strict";
import { GUEST_TOKEN_HEADER } from "./guestToken.ts";
import { tripHeaders, tripReadPath } from "./tripProxy.ts";

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
