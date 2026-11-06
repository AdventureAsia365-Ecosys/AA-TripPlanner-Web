// AA-1038 R5 slice 2 (root ADR 0019 d10) — the signed tenant assertion the BFF sends the assembly Lambda with a
// /{slug} request (header x-tp-tenant). The browser never names a tenant to the Lambdas: the BFF resolves the page's
// slug (lib/tenantSite.ts, GET /browse/tenant/{slug}: served tenants only), then signs {exp, iat, slug, tid} per
// request. The Lambda checks the signature and the expiry before it binds the tenant
// (backend/shared/tenant_assertion.py, the twin of this file; a shared vector pins both).
//
// Format: v1.<payload>.<signature>, base64url without padding; payload = compact JSON, keys sorted; signature =
// HMAC-SHA256 over "v1." + payload with the assertion key = HMAC-SHA256(edge key, KEY_LABEL). The edge key is the
// X-TripPlanner-Key value the BFF already holds (env TRIPPLANNER_API_KEY): no new secret. Server-only; never logged.
// No next/* import, so `npm test` runs it.

import { createHmac } from "node:crypto";

export const TENANT_ASSERTION_HEADER = "x-tp-tenant"; // backend config.TENANT_ASSERTION_HEADER
export const KEY_LABEL = "aa-tripplanner tenant assertion v1";
export const ASSERTION_LIFETIME_S = 120; // the Lambda refuses more than 300 (TENANT_ASSERTION_MAX_LIFETIME_S)

function b64url(buf: Buffer): string {
  return buf.toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function deriveKey(edgeKey: string): Buffer {
  return createHmac("sha256", edgeKey).update(KEY_LABEL).digest();
}

export function signTenantAssertion(
  edgeKey: string,
  tenantId: string,
  slug: string,
  nowS: number = Math.floor(Date.now() / 1000),
  lifetimeS: number = ASSERTION_LIFETIME_S,
): string {
  // Keys in sorted order (exp, iat, slug, tid), compact separators: byte-equal to the Python json.dumps.
  const payload = b64url(
    Buffer.from(JSON.stringify({ exp: nowS + lifetimeS, iat: nowS, slug, tid: tenantId.toLowerCase() }), "utf8"),
  );
  const signed = `v1.${payload}`;
  return `${signed}.${b64url(createHmac("sha256", deriveKey(edgeKey)).update(signed).digest())}`;
}
