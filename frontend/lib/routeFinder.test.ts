// AA-1130: the route finder of an agency's planner (lib/routeFinder.ts). Run: npm test.
import { test } from "node:test";
import assert from "node:assert/strict";
import { headingCountries, routeBadge, scopedUpstreamPath, showRouteFinder, withTenant } from "./routeFinder.ts";
import type { CountryOption, RouteProposal } from "./types.ts";

test("an agency planner names its tenant on its route finder reads; AA's planner sends none", () => {
  const p = withTenant(new URLSearchParams({ resource: "routes", country: "Laos", days: "7" }), "quillmere-voyages");
  assert.equal(p.toString(), "resource=routes&country=Laos&days=7&tenant=quillmere-voyages");
  for (const none of [null, undefined, "", "plan", "Bad Slug"]) {
    assert.equal(withTenant(new URLSearchParams({ resource: "countries" }), none).get("tenant"), null, String(none));
  }
});

test("the BFF passes the tenant to the countries, routes and tour-route reads only", () => {
  assert.equal(scopedUpstreamPath("countries", "/browse/countries", "quillmere-voyages"),
    "/browse/countries?tenant=quillmere-voyages");
  assert.equal(scopedUpstreamPath("routes", "/browse/routes?country=Laos&days=7", "quillmere-voyages"),
    "/browse/routes?country=Laos&days=7&tenant=quillmere-voyages");
  assert.equal(scopedUpstreamPath("tour-route", "/browse/tour-route?source_id=abc", "quillmere-voyages"),
    "/browse/tour-route?source_id=abc&tenant=quillmere-voyages");
  // no tenant: the path as it was (AA's planner)
  assert.equal(scopedUpstreamPath("routes", "/browse/routes?country=Laos&days=7", null),
    "/browse/routes?country=Laos&days=7");
  assert.equal(scopedUpstreamPath("routes", "/browse/routes?country=Laos&days=7", ""),
    "/browse/routes?country=Laos&days=7");
  // a path the BFF already refused stays refused
  assert.equal(scopedUpstreamPath("tenant-tours", null, null), null);
  // other resources never carry it
  assert.equal(scopedUpstreamPath("tiles", "/browse/tiles/1_2?", "quillmere-voyages"), "/browse/tiles/1_2?");
  // a slug that cannot be a tenant is refused before the Lambda (the BFF answers 400)
  for (const bad of ["plan", "../x", "Quillmere", "a b"]) {
    assert.equal(scopedUpstreamPath("routes", "/browse/routes?country=Laos&days=7", bad), null, bad);
  }
});

const C = (country: string, tour_count?: number): CountryOption => ({ country, component_count: 10, tour_count });

test("'Heading to' lists only the countries with a tour (all of them from an older Lambda without counts)", () => {
  assert.deepEqual(headingCountries([C("Laos", 4), C("Invented Border Land", 0), C("Nepal", 2)]).map((c) => c.country),
    ["Laos", "Nepal"]);
  assert.deepEqual(headingCountries([C("Laos"), C("Nepal")]).map((c) => c.country), ["Laos", "Nepal"]);
});

test("an agency page whose agency sells no tour has no route finder; AA's planner always has one", () => {
  assert.equal(showRouteFinder("quillmere-voyages", true, []), false);
  assert.equal(showRouteFinder("quillmere-voyages", true, [C("Laos", 0)]), false);
  assert.equal(showRouteFinder("quillmere-voyages", true, [C("Laos", 1)]), true);
  // not answered yet (or the read failed): not hidden on a guess
  assert.equal(showRouteFinder("quillmere-voyages", false, []), true);
  assert.equal(showRouteFinder(null, true, []), true);
});

const P = (over: Partial<RouteProposal>): RouteProposal => ({
  total_days: 7, transfers: 0, transfer_km: 0, whole_tour: true,
  segments: [{ tour_id: "t1", tour_name: "Invented ridge walk", day_from: 1, day_to: 7, days: 7 }],
  days: [], ...over,
});

test("a first-to-last-stop route shorter than its tour says so instead of 'Whole tour'", () => {
  assert.deepEqual(routeBadge(P({ tour_days: 8 })), { kind: "span", label: "Full route · days 1–7 of 8" });
  assert.deepEqual(routeBadge(P({ tour_days: 7 })), { kind: "whole", label: "Whole tour" });
  assert.deepEqual(routeBadge(P({ tour_days: null })), { kind: "whole", label: "Whole tour" });
  assert.deepEqual(routeBadge(P({})), { kind: "whole", label: "Whole tour" });
  assert.equal(routeBadge(P({ whole_tour: false, tour_days: 8 })), null);
  const chain = P({ whole_tour: false, transfers: 1, segments: [
    { tour_id: "t1", tour_name: "A", day_from: 1, day_to: 3, days: 3 },
    { tour_id: "t2", tour_name: "B", day_from: 2, day_to: 5, days: 4 },
  ] });
  assert.equal(routeBadge(chain), null);
});
