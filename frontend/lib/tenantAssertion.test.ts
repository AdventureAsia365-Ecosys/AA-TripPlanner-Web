// AA-1038 R5 slice 2: the BFF's tenant assertion (lib/tenantAssertion.ts) and the /api/trip tenant step
// (lib/tenantProxy.ts). Run: npm test.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import {
  ASSERTION_LIFETIME_S,
  KEY_LABEL,
  MIN_DEDICATED_KEY_LENGTH,
  TENANT_ASSERTION_HEADER,
  TENANT_ASSERTION_KEY_ENV,
  assertionKeyMaterial,
  assertionKeyMaterialFromEnv,
  deriveKey,
  normaliseEdgeKey,
  signTenantAssertion,
} from "./tenantAssertion.ts";
import { tenantHeaders } from "./tenantProxy.ts";
import { TenantLookupUnavailable, type TenantSite } from "./tenantSite.ts";

// The cross-language vector: AA-TripPlanner-Web backend/tests/test_aa1038_tenant_assertion.py signs the same claims
// with the same edge value (built at run time there too) and pins this exact header (backend/shared/tenant_assertion).
const EDGE = ["aa1038", "vector", "edge"].join("-");
const TENANT_A = "4b1d7e57-0000-4000-8000-0000000000a1";
const NOW = 1762400000;
const VECTOR_HEADER =
  "v1.eyJleHAiOjE3NjI0MDAxMjAsImlhdCI6MTc2MjQwMDAwMCwic2x1ZyI6InF1aWxsbWVyZS12b3lhZ2VzIiwidGlkIjoiNGIxZDdlNTct" +
  "MDAwMC00MDAwLTgwMDAtMDAwMDAwMDAwMGExIn0.iPAGhLvUYeTsgBXKhSuQXE-d5hAAnICxwSr22wdzsS8";

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

function claims(value: string): Record<string, unknown> {
  const payload = value.split(".")[1];
  return JSON.parse(Buffer.from(payload.replace(/-/g, "+").replace(/_/g, "/"), "base64").toString("utf8"));
}

test("the header names and the label match the Lambda", () => {
  assert.equal(TENANT_ASSERTION_HEADER, "x-tp-tenant");
  assert.equal(KEY_LABEL, "aa-tripplanner tenant assertion v1");
  assert.ok(ASSERTION_LIFETIME_S > 0 && ASSERTION_LIFETIME_S <= 300);
});

test("the cross-language vector", () => {
  assert.equal(signTenantAssertion(EDGE, TENANT_A, "quillmere-voyages", NOW), VECTOR_HEADER);
});

test("the signature is HMAC-SHA256 of 'v1.' + payload with the derived key, never the edge value", () => {
  const value = signTenantAssertion(EDGE, TENANT_A, "quillmere-voyages", NOW);
  const [v, payload, sig] = value.split(".");
  assert.equal(v, "v1");
  const want = createHmac("sha256", deriveKey(EDGE)).update(`v1.${payload}`).digest("base64url");
  assert.equal(sig, want);
  const withEdge = createHmac("sha256", EDGE).update(`v1.${payload}`).digest("base64url");
  assert.notEqual(sig, withEdge);
  assert.deepEqual(claims(value), { exp: NOW + 120, iat: NOW, slug: "quillmere-voyages", tid: TENANT_A });
  assert.ok(!value.includes(EDGE));
});

test("no slug header: no tenant header, no lookup (the bare path keeps the demo tenant)", async () => {
  let calls = 0;
  const resolve = async () => {
    calls += 1;
    return SITE;
  };
  assert.deepEqual(await tenantHeaders(null, EDGE, resolve), { ok: true, headers: {} });
  assert.deepEqual(await tenantHeaders("", EDGE, resolve), { ok: true, headers: {} });
  assert.equal(calls, 0);
});

test("a served slug: a fresh assertion for the RESOLVED tenant id", async () => {
  const got = await tenantHeaders("quillmere-voyages", EDGE, async () => SITE, NOW);
  assert.deepEqual(got, { ok: true, headers: { "x-tp-tenant": VECTOR_HEADER } });
});

test("an unknown or unserved slug is a 404 and nothing is signed", async () => {
  const got = await tenantHeaders("nobody-here", EDGE, async () => null);
  assert.deepEqual(got, { ok: false, status: 404, error: "unknown_tenant" });
});

test("no edge key: 503 (the Lambda could not check it either)", async () => {
  const got = await tenantHeaders("quillmere-voyages", "", async () => SITE);
  assert.deepEqual(got, { ok: false, status: 503, error: "tenant_assertion_unavailable" });
});

test("an edge key with surrounding whitespace or a newline still signs the vector (the Lambda strips it too)", () => {
  for (const padded of [`${EDGE}\n`, ` ${EDGE}\r\n`, `\t${EDGE} `]) {
    assert.equal(normaliseEdgeKey(padded), EDGE);
    assert.deepEqual(deriveKey(padded), deriveKey(EDGE));
    assert.equal(signTenantAssertion(padded, TENANT_A, "quillmere-voyages", NOW), VECTOR_HEADER);
  }
});

test("a whitespace-only edge key is no key: 503", async () => {
  const got = await tenantHeaders("quillmere-voyages", " \n", async () => SITE);
  assert.deepEqual(got, { ok: false, status: 503, error: "tenant_assertion_unavailable" });
});

test("a lookup that is down is a retryable 503, not an unknown tenant", async () => {
  const got = await tenantHeaders("quillmere-voyages", EDGE, async () => {
    throw new TenantLookupUnavailable(502);
  });
  assert.deepEqual(got, { ok: false, status: 503, error: "tenant_lookup_unavailable" });
});

// --- AA-1092: the dedicated key (S278 audit M19) -----------------------------------------------------------------------

// The cross-language vector with the dedicated key: backend/tests/test_aa1092_tenant_assertion_key.py pins the same
// header (same claims as the AA-1038 vector, another key material).
const DEDICATED = ["aa1092", "vector", "dedicated", "assertion", "key", "material"].join("-");
const DEDICATED_VECTOR =
  "v1.eyJleHAiOjE3NjI0MDAxMjAsImlhdCI6MTc2MjQwMDAwMCwic2x1ZyI6InF1aWxsbWVyZS12b3lhZ2VzIiwidGlkIjoiNGIxZDdlNTct" +
  "MDAwMC00MDAwLTgwMDAtMDAwMDAwMDAwMGExIn0.i7y_-KKqk8Lg_ud0BU1z94x1kcy0_TbUGwM1OsUt1RM";

test("the dedicated cross-language vector", () => {
  assert.equal(signTenantAssertion(DEDICATED, TENANT_A, "quillmere-voyages", NOW), DEDICATED_VECTOR);
  assert.notEqual(DEDICATED_VECTOR, VECTOR_HEADER);
});

test("the env name and the floor match the Lambda and the task definition", () => {
  assert.equal(TENANT_ASSERTION_KEY_ENV, "TP_TENANT_ASSERTION_KEY");
  assert.equal(MIN_DEDICATED_KEY_LENGTH, 32);
});

test("a dedicated key wins over the edge key", () => {
  assert.equal(assertionKeyMaterial(DEDICATED, EDGE), DEDICATED);
  assert.equal(assertionKeyMaterial(`${DEDICATED}\n`, EDGE), DEDICATED);
  assert.equal(assertionKeyMaterialFromEnv({ TP_TENANT_ASSERTION_KEY: DEDICATED, TRIPPLANNER_API_KEY: EDGE }), DEDICATED);
});

test("no dedicated key (or the placeholder): the edge key, the cut-over fallback", () => {
  assert.equal(assertionKeyMaterial(undefined, EDGE), EDGE);
  assert.equal(assertionKeyMaterial("", EDGE), EDGE);
  assert.equal(assertionKeyMaterial(" \n", EDGE), EDGE);
  assert.equal(assertionKeyMaterial("REPLACE_ME", EDGE), EDGE);
  assert.equal(assertionKeyMaterialFromEnv({ TRIPPLANNER_API_KEY: EDGE }), EDGE);
  assert.equal(assertionKeyMaterialFromEnv({}), "");
});

test("a dedicated key shorter than the floor signs nothing (503), never falls back to the edge key", async () => {
  const short = "x".repeat(MIN_DEDICATED_KEY_LENGTH - 1);
  assert.equal(assertionKeyMaterial(short, EDGE), "");
  const got = await tenantHeaders("quillmere-voyages", assertionKeyMaterial(short, EDGE), async () => SITE);
  assert.deepEqual(got, { ok: false, status: 503, error: "tenant_assertion_unavailable" });
});

test("tenantHeaders signs with the dedicated key it is given", async () => {
  const got = await tenantHeaders("quillmere-voyages", assertionKeyMaterial(DEDICATED, EDGE), async () => SITE, NOW);
  assert.deepEqual(got, { ok: true, headers: { "x-tp-tenant": DEDICATED_VECTOR } });
});
