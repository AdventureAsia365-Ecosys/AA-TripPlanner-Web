// AA-1152: the day cards' AA add-ons — the BFF's Lambda paths, the day lookup, the fact and price words. Invented ids.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  addonsOfDay, addonsUpstream, agencyPriceOf, agencyPriceUpstream, durationText, priceText, type TripAddons,
} from "./dayAddons.ts";
import { ADDONS } from "./dayAddons.messages.ts";
import { translator } from "../ui/core/i18n.ts";

const SV = "sv:7d9f8b00-0000-4000-8000-0000000000c2";
const TRIP = "4a1b2c3d-0000-4000-8000-000000001152";

test("the BFF maps a read, a pick and an unpick onto the Lambda's add-on routes", () => {
  assert.deepEqual(addonsUpstream("GET", TRIP), { method: "GET", path: `/trip/${TRIP}/addons` });
  assert.deepEqual(addonsUpstream("POST", TRIP, { session_id: "s1", day: 2, component_id: SV }), {
    method: "POST", path: `/trip/${TRIP}/addons`, payload: { session_id: "s1", day: 2, component_id: SV },
  });
  assert.deepEqual(addonsUpstream("DELETE", TRIP, { session_id: "s1", day: "3", component_id: SV }), {
    method: "DELETE", path: `/trip/${TRIP}/addons/3/${encodeURIComponent(SV)}`, payload: { session_id: "s1" },
  });
  for (const bad of [
    { session_id: "s1", day: 0, component_id: SV },
    { session_id: "s1", day: 91, component_id: SV },
    { session_id: "s1", day: 2, component_id: "tp:123" },
    { day: 2, component_id: SV },
  ]) {
    assert.equal(addonsUpstream("POST", TRIP, bad), null);
  }
  assert.equal(addonsUpstream("GET", "../etc"), null);
  assert.equal(addonsUpstream("GET", null), null);
});

test("a day's add-ons, the duration and the price in the agency's currency", () => {
  const data: TripAddons = {
    trip_id: TRIP, prices_shown: true,
    days: [{ day: 1, addons: [{
      component_id: SV, title: "Evening lantern walk", unit: "person", duration_minutes: 90, min_pax: 2,
      operating_weekdays: [2, 4], start_times: ["17:30"], destination_id: "d1", at: "sleep", selected: false,
      price: { from_per_person: "42", currency: "USD" },
    }] }],
  };
  assert.equal(addonsOfDay(data, 1)[0].title, "Evening lantern walk");
  assert.deepEqual(addonsOfDay(data, 2), []);
  assert.deepEqual(addonsOfDay(null, 1), []);
  assert.equal(durationText(90), "1 h 30 min");
  assert.equal(durationText(180), "3 h");
  assert.equal(priceText({ from_per_person: "42", currency: "USD" }, "en-US"), "$42");
  assert.match(priceText({ from_per_person: "1040000", currency: "VND" }, "vi-VN"), /1\.040\.000/);
  assert.equal(priceText({ from_per_person: "x", currency: "USD" }, "en-US"), "x USD");
});

test("the add-on words in both languages", () => {
  const en = translator(ADDONS, "en");
  const vi = translator(ADDONS, "vi");
  assert.equal(en("runs", [1, 2, 3, 4, 5, 6, 7]), "Runs every day");
  assert.equal(en("runs", [6, 2]), "Runs Tue, Sat");
  assert.equal(vi("runs", [7]), "Hoạt động CN");
  assert.equal(en("from", "$42"), "From $42 per person");
  assert.doesNotMatch(en("priceNote", "Lantern Travel"), /Adventure Asia/);
});

test("the tour page reads the agency's price through a no-store path, and only a well-formed answer shows", () => {
  assert.equal(agencyPriceUpstream("SYN-TOUR-0042"), "/trip/agency-price?source_id=SYN-TOUR-0042");
  assert.equal(agencyPriceUpstream("a&b"), "/trip/agency-price?source_id=a%26b");
  assert.equal(agencyPriceUpstream(""), null);
  assert.equal(agencyPriceUpstream(null), null);
  assert.equal(agencyPriceUpstream("bad\nkey"), null);
  assert.deepEqual(agencyPriceOf({ price: { from_per_person: "1235", currency: "USD" } }), {
    from_per_person: "1235", currency: "USD",
  });
  for (const bad of [null, {}, { price: null }, { price: { from_per_person: 12, currency: "USD" } },
    { price: { from_per_person: "1.234", currency: "USD" } }, { price: { from_per_person: "10", currency: "usd" } }]) {
    assert.equal(agencyPriceOf(bad), null);
  }
});
