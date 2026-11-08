// AA-1038 R5 hosting (review S272): in the ECS image the catalog routes render on request, never a page baked at build
// with no catalog. Run: npm test.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { rendersOnRequest } from "./renderMode.ts";

const read = (p: string) => readFileSync(new URL(p, import.meta.url), "utf8");

test("only the image build (NEXT_OUTPUT=standalone) renders on request", () => {
  assert.equal(rendersOnRequest({}), false);
  assert.equal(rendersOnRequest({ NEXT_OUTPUT: "standalone" }), true);
  assert.equal(rendersOnRequest({ NEXT_OUTPUT: " standalone " }), true);
  assert.equal(rendersOnRequest({ NEXT_OUTPUT: "export" }), false);
});

// Every route with ISR config that reads the catalog with no request API (so `next build` would prerender it).
for (const route of ["../app/page.tsx", "../app/destinations/page.tsx", "../app/sitemap.ts"]) {
  test(`${route} renders on request in the image`, () => {
    const src = read(route);
    assert.match(src, /^import \{ connection \} from "next\/server";$/m);
    assert.match(src, /^  if \(rendersOnRequest\(\)\) await connection\(\);$/m);
  });
}

test("both Dockerfile stages set NEXT_OUTPUT=standalone", () => {
  const docker = read("../Dockerfile");
  assert.equal((docker.match(/^(?:ENV)?\s+NEXT_OUTPUT=standalone \\$/gm) ?? []).length, 2);
});
