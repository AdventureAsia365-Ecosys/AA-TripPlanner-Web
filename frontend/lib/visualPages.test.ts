// AA-994 S5b (plan guard G16) — every page route of the app is in the visual run's list (e2e/visual/pages.ts) or
// excluded there with a reason, so a new page cannot ship without light / dark / 1440 / 390 captures.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync } from "node:fs";

// Loaded at run time, not imported: the list lives outside frontend/, and `next build` type-checks this file without
// following a path it may not have (the Vercel build reads frontend/ only).
type VisualPage = { slug: string; route: string; path?: string; resolve?: { from: string; hrefPrefix: string } };
const { EXCLUDED, PAGES } = (await import(new URL("../../e2e/visual/pages.ts", import.meta.url).href)) as {
  EXCLUDED: Record<string, string>;
  PAGES: VisualPage[];
};

const APP = new URL("../app/", import.meta.url);

// app/destinations/[country]/page.tsx -> /destinations/[country]; route groups "(x)" drop out of the URL.
function routes(): string[] {
  return (readdirSync(APP, { recursive: true }) as string[])
    .map((f) => f.split("\\").join("/"))
    .filter((f) => f === "page.tsx" || f.endsWith("/page.tsx"))
    .map((f) => "/" + f.replace(/(^|\/)page\.tsx$/, "").split("/").filter((s) => s && !/^\(.*\)$/.test(s)).join("/"));
}

test("every app route is captured by the visual run or excluded with a reason", () => {
  const listed = new Set(PAGES.map((p) => p.route));
  const missing = routes().filter((r) => !listed.has(r) && !(r in EXCLUDED));
  assert.deepEqual(missing, [], "add the route to e2e/visual/pages.ts (or to EXCLUDED with the reason)");
});

test("the visual list names no route that does not exist, and every exclusion has a reason", () => {
  const known = new Set(routes());
  const stale = PAGES.filter((p) => !p.route.startsWith("(") && !known.has(p.route)).map((p) => p.route);
  assert.deepEqual(stale, []);
  for (const [route, why] of Object.entries(EXCLUDED)) assert.ok(why.trim().length > 10, `reason for ${route}`);
});

test("each visual page has exactly one way to reach it and a file-name-safe slug", () => {
  for (const p of PAGES) {
    assert.ok(Boolean(p.path) !== Boolean(p.resolve), `${p.slug}: path or resolve, not both`);
    assert.match(p.slug, /^[a-z0-9-]+$/);
  }
  assert.equal(new Set(PAGES.map((p) => p.slug)).size, PAGES.length);
});
