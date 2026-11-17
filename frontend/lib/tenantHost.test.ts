// AA-1090 R8: an agency's own domain (lib/tenantHost.ts): which hosts are ours, the path mapping on an agency's
// domain, the cached host resolver, and that proxy.ts wires them in front of its old behaviour. Run: npm test.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  createTenantHostResolver,
  hostNotFoundPage,
  isOwnHost,
  requestHost,
  siteHost,
  tenantHostPath,
  type TenantHostFetch,
} from "./tenantHost.ts";
import { TENANT_MISS_TTL_MS, TENANT_TTL_MS, TenantLookupUnavailable } from "./tenantSite.ts";

const OWN = "tripplanner.planner-zone.example.test";
const TENANT = "4b1d7e57-0000-4000-8000-0000000000a1";
const BODY = { tenant: { tenant_id: TENANT, slug: "quillmere-voyages", name: "Quillmere Voyages", kind: "agency",
  launcher: true, branding_badge: false, theme: { accent: "#1a2b3c" }, locale: "en" } };

test("request host: lower-case, no port, no trailing dot, first of a list", () => {
  assert.equal(requestHost("Trip.Quillmere.Example.Invalid:443"), "trip.quillmere.example.invalid");
  assert.equal(requestHost("trip.quillmere.example.invalid., other"), "trip.quillmere.example.invalid");
  assert.equal(requestHost("[::1]:3000"), "[::1]");
  assert.equal(requestHost(null), "");
  assert.equal(siteHost("https://tripplanner.planner-zone.example.test/"), OWN);
  assert.equal(siteHost("not a url"), "");
});

test("our own hosts keep the old behaviour", () => {
  for (const h of [OWN, "", "localhost", "app.localhost", "127.0.0.1", "10.0.3.7", "[::1]", "ip-10-0-1-2"]) {
    assert.equal(isOwnHost(h, OWN), true, h);
  }
  for (const h of ["trip.quillmere.example.invalid", "d111abcdef.cloudfront.net", "evil.example.invalid"]) {
    assert.equal(isOwnHost(h, OWN), false, h);
  }
});

test("paths on an agency's domain", () => {
  const s = "quillmere-voyages";
  assert.deepEqual(tenantHostPath("/", s), { kind: "rewrite", path: "/quillmere-voyages" });
  assert.deepEqual(tenantHostPath("/plan", s), { kind: "rewrite", path: "/quillmere-voyages/plan" });
  assert.deepEqual(tenantHostPath("/plan/shared", s), { kind: "rewrite", path: "/quillmere-voyages/plan/shared" });
  assert.deepEqual(tenantHostPath("/tours/invented-ridge-5f1e0c2a", s),
    { kind: "rewrite", path: "/quillmere-voyages/tours/invented-ridge-5f1e0c2a" });
  // AA-1115: the legal pages are the agency's own, never AA's
  assert.deepEqual(tenantHostPath("/privacy", s), { kind: "rewrite", path: "/quillmere-voyages/privacy" });
  assert.deepEqual(tenantHostPath("/terms", s), { kind: "rewrite", path: "/quillmere-voyages/terms" });
  // our path keeps working, the BFF and static files pass
  for (const p of ["/quillmere-voyages", "/quillmere-voyages/plan", "/quillmere-voyages/privacy", "/api/trip",
    "/api/browse/search", "/_next/static/x.js", "/photo-cdn/a.webp", "/brand/logo.svg", "/robots.txt"]) {
    assert.deepEqual(tenantHostPath(p, s), { kind: "pass" }, p);
  }
  // AA's own catalogue, the retired sign-in and My Trips (AA-1115) and another agency's site are not served here
  for (const p of ["/destinations", "/destinations/laos", "/tours", "/ottervale-journeys/plan", "/ottervale-journeys",
    "/quillmere-voyagesx/plan", "/sign-in", "/trips", "/privacyx", "/terms/x"]) {
    assert.deepEqual(tenantHostPath(p, s), { kind: "not_found" }, p);
  }
});

test("the host resolver caches answers and misses, never an outage", async () => {
  let t = 1_000;
  const calls: string[] = [];
  let status = 200;
  const fetchHost: TenantHostFetch = async (host) => {
    calls.push(host);
    return { status, body: status === 200 ? BODY : null };
  };
  const r = createTenantHostResolver(fetchHost, () => t);
  const site = await r.resolve("trip.quillmere.example.invalid");
  assert.equal(site?.slug, "quillmere-voyages");
  assert.equal(site?.theme.accent, "#1a2b3c");
  await r.resolve("trip.quillmere.example.invalid");
  assert.equal(calls.length, 1);
  t += TENANT_TTL_MS + 1;
  status = 404;
  assert.equal(await r.resolve("trip.quillmere.example.invalid"), null);
  t += TENANT_MISS_TTL_MS - 1;
  assert.equal(await r.resolve("trip.quillmere.example.invalid"), null);
  assert.equal(calls.length, 2);
  status = 503;
  await assert.rejects(r.resolve("go.quillmere.example.invalid"), TenantLookupUnavailable);
  await assert.rejects(r.resolve("go.quillmere.example.invalid"), TenantLookupUnavailable);
  assert.equal(calls.length, 4);
  assert.equal(await r.resolve("10.0.0.1"), null);         // a bad shape asks nothing
  assert.equal(calls.length, 4);
});

test("a body that is not a tenant is a miss", async () => {
  const r = createTenantHostResolver(async () => ({ status: 200, body: { tenant: { tenant_id: "x", slug: "api" } } }));
  assert.equal(await r.resolve("trip.quillmere.example.invalid"), null);
});

test("proxy.ts resolves an agency's host before its old behaviour, on every path", () => {
  const src = readFileSync(new URL("../proxy.ts", import.meta.url), "utf8");
  assert.match(src, /if \(!isOwnHost\(host, OWN_HOST\)\) return onTenantHost\(req, event, host\);/);
  assert.match(src, /if \(!actsOn\(req\.nextUrl\.pathname\)\) return NextResponse\.next\(\);/);
  assert.match(src, /matcher: \["\/\(\(\?!_next\/static\|_next\/image\)\.\*\)"\]/);
  assert.ok(!/AA-\d+/.test(hostNotFoundPage()));
});
