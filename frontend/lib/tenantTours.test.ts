// AA-1094: the tenant's tour list and tour pages (lib/tenantTours.ts). Run: npm test.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  itineraryParagraphs,
  tenantTextMark,
  tenantPlanHref,
  tenantTourPath,
  tenantToursPath,
  tourHref,
} from "./tenantTours.ts";

test("a tenant's list path keeps only the list keys and refuses a reserved or malformed slug", () => {
  const q = new URLSearchParams({ country: "Laos", sort: "duration_asc", limit: "24", tenant: "x", junk: "1" });
  assert.equal(
    tenantToursPath("quillmere-voyages", q),
    "/browse/tenant/quillmere-voyages/tours?country=Laos&sort=duration_asc&limit=24",
  );
  assert.equal(tenantToursPath("quillmere-voyages", new URLSearchParams()), "/browse/tenant/quillmere-voyages/tours");
  for (const bad of ["tours", "plan", "api", "", "../x", "Quillmere", null, undefined]) {
    assert.equal(tenantToursPath(bad, q), null, String(bad));
  }
});

test("a tenant's tour path needs a tenant slug and a tour slug of the browse shape", () => {
  assert.equal(
    tenantTourPath("quillmere-voyages", "invented-ridge-walk-5f1e0c2a"),
    "/browse/tenant/quillmere-voyages/tours/invented-ridge-walk-5f1e0c2a",
  );
  for (const [tenant, tour] of [
    ["quillmere-voyages", "Bad_Slug"],
    ["quillmere-voyages", "a/b"],
    ["quillmere-voyages", ""],
    ["tours", "x-1"],
    [null, "x-1"],
  ] as const) {
    assert.equal(tenantTourPath(tenant, tour), null, `${tenant} ${tour}`);
  }
});

test("a tour of the list opens the tenant's page only for the tenant's own list", () => {
  assert.equal(tourHref("quillmere-voyages", "tenant", "x-1"), "/quillmere-voyages/tours/x-1");
  // the demo tenant's list is the AA catalogue: AA tour pages
  assert.equal(tourHref("aa-tripplanner", "aa", "x-1"), "/tours/x-1");
  // the bare planner (no tenant) and an older BFF answer without `source`
  assert.equal(tourHref(null, undefined, "x-1"), "/tours/x-1");
  assert.equal(tourHref("quillmere-voyages", undefined, "x-1"), "/tours/x-1");
  assert.equal(tourHref("tours", "tenant", "x-1"), "/tours/x-1");
});

test("plan this trip stays on the tenant's planner", () => {
  const href = tenantPlanHref("quillmere-voyages", {
    tour_id: "770a38dd-4e7b-4b55-9ded-0e9ced757301",
    source_id: "5f1e0c2a-7d3b-4c9e-8a61-2b4d6f8e0a13",
    country: "Laos",
    duration_days: 45,
  });
  assert.ok(href.startsWith("/quillmere-voyages/plan?"));
  const q = new URLSearchParams(href.split("?")[1]);
  assert.equal(q.get("tour"), "770a38dd-4e7b-4b55-9ded-0e9ced757301");
  assert.equal(q.get("source"), "5f1e0c2a-7d3b-4c9e-8a61-2b4d6f8e0a13");
  assert.equal(q.get("days"), "30");
});

test("the itinerary text splits into paragraphs on blank lines", () => {
  assert.deepEqual(itineraryParagraphs("Day 1 — Arrive\nRest.\n\n  \nDay 2 — Walk\nUp."), [
    "Day 1 — Arrive\nRest.",
    "Day 2 — Walk\nUp.",
  ]);
  assert.deepEqual(itineraryParagraphs(null), []);
});

test("the tenant text mark says AI, when, and whether the agency edited it", () => {
  assert.deepEqual(tenantTextMark({ generated_at: "2031-03-04T05:06:00+00:00", human_edited: false }), {
    "data-ai-generated": "true",
    "data-ai-source": "tenant",
    "data-ai-human-edited": "false",
    "data-ai-generated-at": "2031-03-04T05:06:00+00:00",
  });
  assert.deepEqual(tenantTextMark({ generated_at: null, human_edited: true }), {
    "data-ai-generated": "true",
    "data-ai-source": "tenant",
    "data-ai-human-edited": "true",
  });
  assert.equal(tenantTextMark(undefined)["data-ai-generated"], "true");
});
