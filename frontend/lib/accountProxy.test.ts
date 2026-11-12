// AA-1038 R5 slice 2: the account calls of the BFF (POST /me/claim of /api/auth/verify, GET /me/trips of /api/me,
// lib/accountProxy.ts) carry the same signed tenant assertion as /api/trip when the browser names a tenant.
// Run: npm test.
import { test } from "node:test";
import assert from "node:assert/strict";
import { claimGuestTrips, fetchMeTrips } from "./accountProxy.ts";
import { TENANT_ASSERTION_HEADER, signTenantAssertion } from "./tenantAssertion.ts";
import { TenantLookupUnavailable, type TenantSite } from "./tenantSite.ts";

const EDGE = ["aa1038", "account", "edge"].join("-");
const TENANT_A = "4b1d7e57-0000-4000-8000-0000000000a1";
const NOW = 1762400000;
const SITE: TenantSite = {
  tenant_id: TENANT_A,
  slug: "quillmere-voyages",
  name: "Quillmere Voyages",
  kind: "agency",
  launcher: true,
  branding_badge: false,
  theme: {},
  locale: "en",
};

function recorder(answer: { status: number; body: unknown }) {
  const calls: { url: string; init: RequestInit }[] = [];
  const fetchImpl = (async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    return new Response(JSON.stringify(answer.body), { status: answer.status });
  }) as unknown as typeof fetch;
  return { calls, fetchImpl };
}

const resolveA = async (slug: string) => (slug === SITE.slug ? SITE : null);

function base(tenantSlug: string | null, fetchImpl: typeof fetch, resolve = resolveA) {
  return { tripApiUrl: "https://trip.example", edgeKey: EDGE, requestId: "req-1", accessToken: "at", tenantSlug,
    resolve, fetchImpl, nowS: NOW };
}

test("claim from a tenant's planner: the Lambda gets the assertion of the RESOLVED tenant", async () => {
  const { calls, fetchImpl } = recorder({ status: 200, body: { claimed: 2 } });
  const got = await claimGuestTrips({ ...base("quillmere-voyages", fetchImpl), guestToken: "g".repeat(43) });
  assert.deepEqual(got, { claimed: 2, status: 200 });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "https://trip.example/me/claim");
  const h = calls[0].init.headers as Record<string, string>;
  assert.equal(h[TENANT_ASSERTION_HEADER], signTenantAssertion(EDGE, TENANT_A, "quillmere-voyages", NOW));
  assert.equal(h["x-tripplanner-key"], EDGE);
  assert.equal(h["x-tripplanner-access"], "Bearer at");
});

test("claim on the bare path: no assertion (the demo tenant), as before", async () => {
  const { calls, fetchImpl } = recorder({ status: 200, body: { claimed: 1 } });
  const got = await claimGuestTrips({ ...base(null, fetchImpl), guestToken: null });
  assert.deepEqual(got, { claimed: 1, status: 200 });
  assert.ok(!(TENANT_ASSERTION_HEADER in (calls[0].init.headers as Record<string, string>)));
});

test("claim for a tenant that does not resolve, or a lookup that is down: skipped, never claimed into the demo tenant",
  async () => {
    const { calls, fetchImpl } = recorder({ status: 200, body: { claimed: 9 } });
    assert.deepEqual(await claimGuestTrips({ ...base("nobody-here", fetchImpl), guestToken: null }),
      { claimed: null, status: 404 });
    const down = async () => {
      throw new TenantLookupUnavailable(503);
    };
    assert.deepEqual(await claimGuestTrips({ ...base("quillmere-voyages", fetchImpl, down), guestToken: null }),
      { claimed: null, status: 503 });
    assert.equal(calls.length, 0);
  });

test("My Trips from a tenant's planner: /me/trips under the tenant's assertion; refusals do not call the Lambda",
  async () => {
    const { calls, fetchImpl } = recorder({ status: 200, body: { trips: [] } });
    const got = await fetchMeTrips(base("quillmere-voyages", fetchImpl));
    assert.ok("upstream" in got && got.upstream.status === 200);
    assert.equal(calls[0].url, "https://trip.example/me/trips");
    assert.equal((calls[0].init.headers as Record<string, string>)[TENANT_ASSERTION_HEADER],
      signTenantAssertion(EDGE, TENANT_A, "quillmere-voyages", NOW));

    const bare = await fetchMeTrips(base(null, fetchImpl));
    assert.ok("upstream" in bare);
    assert.ok(!(TENANT_ASSERTION_HEADER in (calls[1].init.headers as Record<string, string>)));

    assert.deepEqual(await fetchMeTrips(base("nobody-here", fetchImpl)),
      { refused: { status: 404, error: "unknown_tenant" } });
    assert.equal(calls.length, 2);
  });
