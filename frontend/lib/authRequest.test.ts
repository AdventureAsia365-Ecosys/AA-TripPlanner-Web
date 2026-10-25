// AA-908: auth route helpers (lib/authRequest.ts) and the My Trips labels (lib/myTrips.ts). Run: npm test.
import { test } from "node:test";
import assert from "node:assert/strict";
import { authEnv, authLogLine, callContext, safeNext } from "./authRequest.ts";
import { tripFacts, tripStatusLabel, tripTitle, updatedLabel, type MyTrip } from "./myTrips.ts";

test("sign-in is off without a session secret of at least 32 characters", () => {
  assert.equal(authEnv({}).sessionSecret, "");
  assert.equal(authEnv({ TP_SESSION_SECRET: "short" }).sessionSecret, "");
  assert.equal(authEnv({ TP_SESSION_SECRET: "x".repeat(32), TP_COGNITO_BFF_KEY: " k " }).sessionSecret, "x".repeat(32));
  assert.equal(authEnv({ TP_COGNITO_BFF_KEY: " k " }).bffKey, "k");
  assert.equal(authEnv({ TP_COGNITO_SIGNUP_PROOF_KEY: " p " }).signupProofKey, "p");
  assert.equal(authEnv({}).signupProofKey, "");
});

test("the call context carries the BFF key and the traveller address", () => {
  const ctx = callContext({ sessionSecret: "s", bffKey: "k", signupProofKey: "p" },
    { get: (n) => (n === "x-forwarded-for" ? "192.0.2.9" : null) });
  assert.deepEqual([ctx.bffKey, ctx.signupProofKey, ctx.travellerIp], ["k", "p", "192.0.2.9"]);
});

test("next paths stay on this site", () => {
  assert.equal(safeNext("/plan?x=1"), "/plan?x=1");
  for (const bad of [undefined, "", "https://evil.example", "//evil.example", "/\\evil.example", "plan"]) {
    assert.equal(safeNext(bad), "/trips");
  }
});

test("auth log lines carry the step and outcome only", () => {
  assert.deepEqual(JSON.parse(authLogLine("start", "signup", "r1")),
    { evt: "traveller_signin", step: "start", outcome: "signup", request_id: "r1" });
});

const TRIP: MyTrip = {
  trip_id: "t", session_id: "s", status: "sent", stops: 4, unavailable_stops: 1, days: 3,
  stop_names: ["Hanoi", "Ha Long", "Hue"], date_start: null, date_end: null, created_at: null,
  updated_at: "2026-10-25T03:00:00+00:00",
};

test("My Trips labels", () => {
  assert.equal(tripTitle(TRIP), "Hanoi · Ha Long · Hue …");
  assert.equal(tripTitle({ ...TRIP, stop_names: [], stops: 0 }), "A trip with no stops yet");
  assert.equal(tripStatusLabel("sent"), "Sent to an advisor");
  assert.equal(tripStatusLabel("draft"), "Draft");
  assert.equal(tripFacts(TRIP), "3 days · 4 stops · 1 no longer offered");
  assert.equal(tripFacts({ ...TRIP, days: 1, stops: 1, unavailable_stops: 0 }), "1 day · 1 stop");
  assert.equal(updatedLabel(TRIP.updated_at), "Updated 25 Oct 2026");
  assert.equal(updatedLabel(null), "");
});
