// AA-1038 R5 slice 3 — the trip request form of an agency tenant's planner (lib/leadForm.ts). Run: npm test.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import {
  CONTROL_RE,
  EMAIL_RE,
  EMPTY_LEAD,
  NAME_TOKEN_NORMALISATION,
  NAME_TOKEN_RE,
  PHONE_RE,
  TRAVELLER_MAX,
  canSendLead,
  childAges,
  isAgencyPlanner,
  leadBody,
  leadFormErrors,
  leadResultMessage,
  travellerFieldOk,
  type TravellerField,
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

test("a phone needs + and the country code, as the contract and the agency's CRM need it", () => {
  for (const phone of ["+84 90 000 0000", "+44 (20) 7946-0000", "+1-202-555-0100"]) {
    assert.deepEqual(leadFormErrors({ ...FILLED, phone }), [], phone);
  }
  for (const phone of ["090 000 0000", "+0 90 000 0000", "+84.90.000.0000", "+1 2"]) {
    assert.deepEqual(leadFormErrors({ ...FILLED, phone }), ["phone"], phone);
  }
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

test("the send limits and the bot check have their own calm lines", () => {
  assert.match(leadResultMessage(429, { error: "rate_limited" }, "Q"), /wait a little/);
  assert.match(leadResultMessage(429, { error: "lead_cap_reached" }, "Quillmere Voyages"), /Quillmere Voyages has/);
  assert.match(leadResultMessage(403, { error: "bot_check_failed" }, "Q"), /reload the page/);
});

// S272 review: ONE Traveller rule on all three sides (this form, TripPlanner's validate(), the CRM's traveller_errors),
// pinned by the shared vectors (root docs/contracts/rules/, byte copy under backend/tests/contracts/).
interface RuleCase { field: TravellerField; id: string; value: unknown; valid: boolean }
const RULES = JSON.parse(readFileSync(
  new URL("../../backend/tests/contracts/lead.submitted.v1.traveller-rules.json", import.meta.url), "utf8",
)) as { cases: RuleCase[]; email_pattern: string; phone_pattern: string; control_characters: string;
  max_length: Record<TravellerField, number>; name_token_pattern: string; name_token_normalisation: string };

test("the Traveller rule gives the shared verdict on every vector", () => {
  assert.ok(RULES.cases.length > 30);
  for (const c of RULES.cases) assert.equal(travellerFieldOk(c.field, c.value), c.valid, `${c.field}-${c.id}`);
});

test("the form's patterns and limits are the shared ones", () => {
  assert.equal(EMAIL_RE.source, RULES.email_pattern);
  assert.equal(PHONE_RE.source, RULES.phone_pattern);
  assert.equal(CONTROL_RE.source, RULES.control_characters);
  assert.deepEqual({ ...TRAVELLER_MAX }, RULES.max_length);
});

test("review repro: a name NFC lengthens past 120 is caught in the form, not rejected later by the CRM", () => {
  const name = "\ufb2c".repeat(41); // 41 code points typed, 123 after NFC
  assert.deepEqual(leadFormErrors({ ...FILLED, name }), ["name"]);
  assert.deepEqual(leadFormErrors({ ...FILLED, name: "\ufb2c".repeat(40) }), []);
  // code points, not UTF-16 units: 120 astral letters are 240 units and still valid
  assert.deepEqual(leadFormErrors({ ...FILLED, name: "\u{1D49C}".repeat(120) }), []);
});

test("review repro: a name without a letter or digit is caught in the form (the CRM could not file it)", () => {
  for (const name of ["-", "...", "\u2014", "\u{1F334}", "___", "\u0301"]) {
    assert.deepEqual(leadFormErrors({ ...FILLED, name }), ["name"], JSON.stringify(name));
  }
  for (const name of ["\u674e\u5c0f\u9f99", "Zo\u00eb N\u00fa\u00f1ez", "Ada \u{1F334}", "\u2122", "R2"]) {
    assert.deepEqual(leadFormErrors({ ...FILLED, name }), [], JSON.stringify(name));
  }
  assert.equal(NAME_TOKEN_RE.source, RULES.name_token_pattern);
  assert.equal(NAME_TOKEN_NORMALISATION, RULES.name_token_normalisation);
});

test("review repro: an e-mail lower-casing lengthens is caught in the form (checked as sent, lower-case)", () => {
  for (const email of [
    "\u0130".repeat(33) + "@example.com",
    "a".repeat(60) + "\u0130".repeat(4) + "@example.com",
    "a".repeat(62) + "\u0130".repeat(2) + "@" + "b".repeat(181) + ".example",
  ]) {
    assert.deepEqual(leadFormErrors({ ...FILLED, email }), ["email"], email);
  }
  assert.deepEqual(leadFormErrors({ ...FILLED, email: "\u0130\u0130@Example.Invalid" }), []);
});
