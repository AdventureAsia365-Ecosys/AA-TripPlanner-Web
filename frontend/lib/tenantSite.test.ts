// AA-1038 R5 slice 2: the /{slug} tenant resolver (lib/tenantSite.ts) and the planner's tenant state
// (lib/plannerTenant.ts). Run: npm test.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  TENANT_MISS_TTL_MS,
  TENANT_SLUG_HEADER,
  TENANT_TTL_MS,
  createTenantResolver,
  isTenantSlug,
  type TenantFetch,
} from "./tenantSite.ts";
import { IDS_KEY, TENANT_SLUG_HEADER as CLIENT_HEADER, idsStorageKey, tenantRequestHeaders } from "./plannerTenant.ts";
import { STORAGE_KEY } from "./guestIds.ts";

const TENANT_A = "4b1d7e57-0000-4000-8000-0000000000a1";
const BODY = {
  tenant: {
    tenant_id: TENANT_A,
    slug: "quillmere-voyages",
    name: "Quillmere Voyages",
    kind: "agency",
    launcher: true,
    branding_badge: false,
    theme: { accent: "#1a2b3c" },
    locale: "vi",
  },
};

function fakeFetch(answers: Record<string, { status: number; body: unknown }>) {
  const calls: string[] = [];
  const f: TenantFetch = async (slug) => {
    calls.push(slug);
    return answers[slug] ?? { status: 404, body: null };
  };
  return { f, calls };
}

test("slug shape", () => {
  for (const ok of ["aa-tripplanner", "quillmere-voyages", "a", "agency_1"]) assert.ok(isTenantSlug(ok), ok);
  for (const bad of ["", "Quill", "-lead", "a b", "a.b", "../x", "x".repeat(101), "favicon.ico"]) {
    assert.ok(!isTenantSlug(bad), bad);
  }
});

test("a served tenant resolves and is cached for a minute", async () => {
  let now = 1_000;
  const { f, calls } = fakeFetch({ "quillmere-voyages": { status: 200, body: BODY } });
  const r = createTenantResolver(f, () => now);
  const site = await r.resolve("quillmere-voyages");
  assert.deepEqual(site, BODY.tenant);
  await r.resolve("quillmere-voyages");
  assert.equal(calls.length, 1);
  now += TENANT_TTL_MS + 1;
  await r.resolve("quillmere-voyages");
  assert.equal(calls.length, 2);
});

test("a miss is cached shortly, a failure not at all, a bad slug never asks", async () => {
  let now = 1_000;
  const { f, calls } = fakeFetch({ down: { status: 502, body: null } });
  const r = createTenantResolver(f, () => now);
  assert.equal(await r.resolve("nobody-here"), null);
  assert.equal(await r.resolve("nobody-here"), null);
  assert.equal(calls.filter((c) => c === "nobody-here").length, 1);
  now += TENANT_MISS_TTL_MS + 1;
  await r.resolve("nobody-here");
  assert.equal(calls.filter((c) => c === "nobody-here").length, 2);
  await r.resolve("down");
  await r.resolve("down");
  assert.equal(calls.filter((c) => c === "down").length, 2);
  assert.equal(await r.resolve("Not A Slug"), null);
  assert.ok(!calls.includes("Not A Slug"));
});

test("an answer for another slug or without a uuid tenant id is refused", async () => {
  const other = { tenant: { ...BODY.tenant, slug: "someone-else" } };
  const noId = { tenant: { ...BODY.tenant, tenant_id: "not-a-uuid" } };
  const { f } = fakeFetch({ "quillmere-voyages": { status: 200, body: other }, "ottervale": { status: 200, body: noId } });
  const r = createTenantResolver(f);
  assert.equal(await r.resolve("quillmere-voyages"), null);
  assert.equal(await r.resolve("ottervale"), null);
});

test("the planner's ids key and header (bare path unchanged)", () => {
  assert.equal(IDS_KEY, STORAGE_KEY);
  assert.equal(CLIENT_HEADER, TENANT_SLUG_HEADER);
  assert.equal(idsStorageKey(null), STORAGE_KEY);
  assert.equal(idsStorageKey("quillmere-voyages"), `${STORAGE_KEY}:quillmere-voyages`);
  assert.deepEqual(tenantRequestHeaders(null), {});
  assert.deepEqual(tenantRequestHeaders("quillmere-voyages"), { "x-tp-tenant-slug": "quillmere-voyages" });
});
