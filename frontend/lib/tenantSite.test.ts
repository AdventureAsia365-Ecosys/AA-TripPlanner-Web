// AA-1038 R5 slice 2: the /{slug} tenant resolver (lib/tenantSite.ts) and the planner's tenant state
// (lib/plannerTenant.ts). Run: npm test.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync } from "node:fs";
import {
  RESERVED_TENANT_SLUGS,
  TENANT_MISS_TTL_MS,
  TENANT_SLUG_HEADER,
  TENANT_SLUG_RE,
  TENANT_TTL_MS,
  TenantLookupUnavailable,
  createTenantResolver,
  isTenantSlug,
  type TenantFetch,
} from "./tenantSite.ts";
import {
  IDS_KEY,
  TENANT_SLUG_HEADER as CLIENT_HEADER,
  accountLinks,
  clearGuestIdKeys,
  idsStorageKey,
  myTripsTargets,
  tenantOfPath,
  tenantRequestHeaders,
} from "./plannerTenant.ts";
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
  await assert.rejects(r.resolve("down"), TenantLookupUnavailable);
  await assert.rejects(r.resolve("down"), TenantLookupUnavailable);
  assert.equal(calls.filter((c) => c === "down").length, 2);
  assert.equal(await r.resolve("Not A Slug"), null);
  assert.ok(!calls.includes("Not A Slug"));
});

test("a browse 5xx, a network error or a refused key is 'unavailable', never 'unknown tenant', and not cached", async () => {
  for (const status of [500, 502, 503, 504, 401, 403, 0]) {
    const { f, calls } = fakeFetch({ "quillmere-voyages": { status, body: null } });
    const r = createTenantResolver(f);
    await assert.rejects(r.resolve("quillmere-voyages"), (e: unknown) => {
      assert.ok(e instanceof TenantLookupUnavailable);
      assert.equal((e as TenantLookupUnavailable).status, status);
      return true;
    });
    await assert.rejects(r.resolve("quillmere-voyages"), TenantLookupUnavailable);
    assert.equal(calls.length, 2, `status ${status} must not be cached`);
  }
  // Once browse answers again, the tenant resolves at once (no cached failure in between).
  let answer = { status: 503, body: null as unknown };
  const r = createTenantResolver(async () => answer);
  await assert.rejects(r.resolve("quillmere-voyages"), TenantLookupUnavailable);
  answer = { status: 200, body: BODY };
  assert.deepEqual(await r.resolve("quillmere-voyages"), BODY.tenant);
});

test("a reserved slug (a static top-level path) is never a tenant and is not looked up", async () => {
  const { f, calls } = fakeFetch({});
  const r = createTenantResolver(f);
  for (const slug of ["plan", "tours", "destinations", "trips", "privacy", "terms", "sign-in", "api", "brand", "hero"]) {
    assert.ok(RESERVED_TENANT_SLUGS.has(slug), slug);
    assert.ok(!isTenantSlug(slug), slug);
    assert.equal(await r.resolve(slug), null);
  }
  assert.deepEqual(calls, []);
  assert.ok(isTenantSlug("aa-tripplanner"));
});

test("the reserved list is exactly the top-level folders of app/ and public/ (a new route updates it)", () => {
  const app = readdirSync(new URL("../app/", import.meta.url), { withFileTypes: true })
    .filter((d) => d.isDirectory() && !d.name.startsWith("[") && !d.name.startsWith("(") && !d.name.startsWith("_"))
    .map((d) => d.name);
  const pub = readdirSync(new URL("../public/", import.meta.url)).map((n) => n.replace(/\.[^.]+$/, ""));
  const servable = [...new Set([...app, ...pub])].filter((n) => TENANT_SLUG_RE.test(n)).sort();
  assert.deepEqual([...RESERVED_TENANT_SLUGS].sort(), servable);
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

test("sign-out forgets the bare ids key and every tenant's ids key, nothing else", () => {
  const data = new Map<string, string>([
    [STORAGE_KEY, "{}"],
    [`${STORAGE_KEY}:quillmere-voyages`, "{}"],
    [`${STORAGE_KEY}:ottervale-journeys`, "{}"],
    ["aa-tripplanner:reset-notice", "1"],
    ["aa-tripplanner:idsx", "keep"],
    ["theme", "dark"],
  ]);
  const storage = {
    get length() {
      return data.size;
    },
    key: (i: number) => [...data.keys()][i] ?? null,
    removeItem: (k: string) => {
      data.delete(k);
    },
  };
  clearGuestIdKeys(storage);
  assert.deepEqual([...data.keys()].sort(), ["aa-tripplanner:idsx", "aa-tripplanner:reset-notice", "theme"]);
});

test("the tenant of a same-site path (sign-in next, My Trips)", () => {
  assert.equal(tenantOfPath("/quillmere-voyages/plan"), "quillmere-voyages");
  assert.equal(tenantOfPath("/quillmere-voyages"), "quillmere-voyages");
  assert.equal(tenantOfPath("/quillmere-voyages/plan?country=vn"), "quillmere-voyages");
  assert.equal(tenantOfPath("/trips?tenant=quillmere-voyages"), "quillmere-voyages");
  for (const bare of ["/trips", "/plan", "/plan?tenant=Bad Slug", "/", "", null, "//evil.example/x", "https://x/y",
    "/tours/some-tour", "/trips?tenant=plan"]) {
    assert.equal(tenantOfPath(bare), null, String(bare));
  }
});

test("account links and My Trips targets keep the tenant; the bare paths are unchanged", () => {
  assert.deepEqual(accountLinks(null), { signIn: "/sign-in", trips: "/trips" });
  const a = accountLinks("quillmere-voyages");
  assert.equal(a.trips, "/trips?tenant=quillmere-voyages");
  assert.equal(new URL(a.signIn, "https://x").searchParams.get("next"), "/quillmere-voyages/plan");
  assert.equal(tenantOfPath(new URL(a.signIn, "https://x").searchParams.get("next")), "quillmere-voyages");
  assert.deepEqual(myTripsTargets(null), { idsKey: STORAGE_KEY, planner: "/plan", signIn: "/sign-in?next=/trips" });
  const t = myTripsTargets("quillmere-voyages");
  assert.equal(t.idsKey, idsStorageKey("quillmere-voyages"));
  assert.equal(t.planner, "/quillmere-voyages/plan");
  assert.equal(tenantOfPath(new URL(t.signIn, "https://x").searchParams.get("next")), "quillmere-voyages");
});
