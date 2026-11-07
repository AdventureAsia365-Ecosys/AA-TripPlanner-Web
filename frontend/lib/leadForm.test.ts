// AA-1038 R5 slice 3 — the trip request form of an agency tenant's planner (lib/leadForm.ts). Run: npm test.
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  EMPTY_LEAD,
  canSendLead,
  childAges,
  isAgencyPlanner,
  leadBody,
  leadFormErrors,
  leadResultMessage,
} from "./leadForm.ts";

const FILLED = { ...EMPTY_LEAD, name: " Ada Fernwick ", email: "ada@example.invalid", consent: true };

test("only an agency tenant's planner sends a lead; the bare path and the AA demo keep the demo", () => {
  assert.equal(isAgencyPlanner("quillmere-voyages"), true);
  for (const slug of [null, undefined, "", "aa-tripplanner"]) assert.equal(isAgencyPlanner(slug), false);
});

test("the form needs a name, a valid e-mail and the contact consent", () => {
  assert.deepEqual(leadFormErrors(EMPTY_LEAD), ["name", "email"]);
  assert.equal(canSendLead({ ...FILLED, consent: false }), false);
  assert.equal(canSendLead(FILLED), true);
  assert.deepEqual(leadFormErrors({ ...FILLED, phone: "call me", adults: 0, children: "4, 30" }),
    ["phone", "adults", "child_ages"]);
});

test("children's ages parse as 0-17 numbers", () => {
  assert.deepEqual(childAges(""), []);
  assert.deepEqual(childAges(" 4, 9 "), [4, 9]);
  assert.equal(childAges("4, x"), null);
  assert.equal(childAges("18"), null);
});

test("the body has the shape the Lambda validates, without empty optional answers", () => {
  const body = leadBody({ ...FILLED, children: "9", budget: "2000_3500", message: "  " }, "vi");
  assert.deepEqual(body, {
    traveller: { name: "Ada Fernwick", email: "ada@example.invalid", phone: null, locale: "vi" },
    party: { adults: 2, child_ages: [9] },
    trip: { start_date: null, budget_band: "2000_3500", message: null },
    consent: { contact: true, marketing: false },
  });
});

test("each answer of the Lambda has a calm line that names the agency", () => {
  assert.match(leadResultMessage(200, null, "Quillmere Voyages"), /^Sent to Quillmere Voyages\./);
  assert.match(leadResultMessage(409, { error: "lead_route_missing" }, "Quillmere Voyages"), /contact them directly/);
  assert.match(leadResultMessage(422, { error: "consent_required" }, "Q"), /tick the box/);
  assert.match(leadResultMessage(500, null, "Q"), /try again/);
});
