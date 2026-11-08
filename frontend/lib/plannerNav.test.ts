// AA-1038 R5 hosting (S272): every way into the planner is a document load (the web ACL issues the AWS WAF token
// there), so links to it are plain anchors and a planner reached client-side reloads once. Run: npm test.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { isPlannerHref, isPlannerPath, plannerNeedsDocumentLoad, reloadIntoPlannerDocument } from "./plannerNav.ts";

const read = (p: string) => readFileSync(new URL(p, import.meta.url), "utf8");

test("planner paths match the WAF rule: ends with /plan or contains /plan/", () => {
  for (const p of ["/plan", "/plan/", "/plan/x", "/sample-agency/plan", "/sample-agency/plan/x"]) {
    assert.equal(isPlannerPath(p), true, p);
  }
  for (const p of ["/", "/plans", "/planner", "/tours/plan-b-day", "/destinations", "/explain"]) {
    assert.equal(isPlannerPath(p), false, p);
  }
});

test("only same-site hrefs to a planner page are planner links", () => {
  assert.equal(isPlannerHref("/plan"), true);
  assert.equal(isPlannerHref("/plan?country=Examplestan&days=7"), true);
  assert.equal(isPlannerHref("/plan?ask=1#trip"), true);
  assert.equal(isPlannerHref("/sample-agency/plan"), true);
  assert.equal(isPlannerHref("/tours/sample-tour"), false);
  assert.equal(isPlannerHref("/#tours"), false);
  assert.equal(isPlannerHref("#tours"), false);
  assert.equal(isPlannerHref("//elsewhere.example/plan"), false);
  assert.equal(isPlannerHref("https://elsewhere.example/plan"), false);
});

test("a planner reached by a client-side navigation needs one document load; a planner document does not", () => {
  assert.equal(plannerNeedsDocumentLoad("/plan", "https://tp.example/tours/sample-tour"), true);
  assert.equal(plannerNeedsDocumentLoad("/sample-agency/plan", "https://tp.example/sample-agency"), true);
  assert.equal(plannerNeedsDocumentLoad("/plan", "https://tp.example/plan?days=7"), false);
  assert.equal(plannerNeedsDocumentLoad("/plan", "https://tp.example/sample-agency/plan"), false);
  assert.equal(plannerNeedsDocumentLoad("/tours/sample-tour", "https://tp.example/"), false);
  assert.equal(plannerNeedsDocumentLoad("/plan", null), false);
  assert.equal(plannerNeedsDocumentLoad("/plan", "not a url"), false);
});

test("the reload replaces the entry with the same URL, once", () => {
  const replaced: string[] = [];
  const loc = { pathname: "/plan", href: "https://tp.example/plan?days=7", replace: (u: string | URL) => void replaced.push(String(u)) };
  assert.equal(reloadIntoPlannerDocument(loc, () => "https://tp.example/"), true);
  assert.deepEqual(replaced, ["https://tp.example/plan?days=7"]);
  // After that load the document IS the planner: no second reload (no loop).
  assert.equal(reloadIntoPlannerDocument(loc, () => "https://tp.example/plan?days=7"), false);
  assert.equal(reloadIntoPlannerDocument(null, () => "https://tp.example/"), false);
  assert.equal(replaced.length, 1);
});

test("every in-site way into the planner is a document load", () => {
  // Links: SiteLink renders <a> for a planner href; ButtonLink goes through it.
  const siteLink = read("../ui/SiteLink.tsx");
  assert.match(siteLink, /if \(isPlannerHref\(href\)\) \{/);
  assert.match(siteLink, /<a href=\{href\} \{\.\.\.rest\}>/);
  const button = read("../ui/Button.tsx");
  assert.match(button, /<SiteLink href=\{href\}/);
  assert.doesNotMatch(button, /from "next\/link"/);
  // Files that link to the planner never use next/link directly.
  for (const f of [
    "../components/site/SiteHeader.tsx",
    "../components/site/SiteFooter.tsx",
    "../components/site/WaysIn.tsx",
    "../components/site/CtaBand.tsx",
    "../app/page.tsx",
    "../app/not-found.tsx",
    "../app/destinations/[country]/page.tsx",
    "../app/tours/[slug]/page.tsx",
    "../components/MyTrips.tsx",
  ]) {
    assert.doesNotMatch(read(f), /from "next\/link"/, f);
  }
  // Programmatic ways in: a document load, never router.push.
  const home = read("../components/site/HomeSearch.tsx");
  assert.match(home, /window\.location\.assign\(planHref\(country, days, travellers\)\)/);
  assert.doesNotMatch(home, /router\.push|useRouter/);
  // The safety net on the planner itself.
  assert.match(read("../app/plan/PlannerApp.tsx"), /useEffect\(\(\) => \{\s*reloadIntoPlannerDocument\(\);\s*\}, \[\]\);/);
});
