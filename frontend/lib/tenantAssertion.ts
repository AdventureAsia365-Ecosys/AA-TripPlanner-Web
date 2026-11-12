// AA-1038 R5 slice 2 (root ADR 0019 d10) — the signed tenant assertion the BFF sends the assembly Lambda with a
// /{slug} request (header x-tp-tenant). The browser never names a tenant to the Lambdas: the BFF resolves the page's
// slug (lib/tenantSite.ts, GET /browse/tenant/{slug}: served tenants only), then signs {exp, iat, slug, tid} per
// request. The Lambda checks the signature and the expiry before it binds the tenant
// (backend/shared/tenant_assertion.py, the twin of this file; a shared vector pins both).
//
// Format: v1.<payload>.<signature>, base64url without padding; payload = compact JSON, keys sorted; signature =
// HMAC-SHA256 over "v1." + payload with the assertion key = HMAC-SHA256(key material, KEY_LABEL).
// AA-1092 (S278 audit M19): the key material is the DEDICATED secret tripplanner/<env>/tenant-assertion-key (its
// "current" value, web task env TP_TENANT_ASSERTION_KEY), no longer the edge key: the tenant binding has a key of its
// own, rotated on its own. Until the task env carries the key (the cut-over), the BFF falls back to the edge key,
// which the Lambda accepts only inside its cut-over window (backend/shared/tenant_assertion.py). Server-only; never
// logged. No next/* import, so `npm test` runs it.

import { createHmac } from "node:crypto";

export const TENANT_ASSERTION_HEADER = "x-tp-tenant"; // backend config.TENANT_ASSERTION_HEADER
export const KEY_LABEL = "aa-tripplanner tenant assertion v1";
export const ASSERTION_LIFETIME_S = 120; // the Lambda refuses more than 300 (TENANT_ASSERTION_MAX_LIFETIME_S)

// The whitespace stripped from the key material (dedicated or edge key) before deriving: the HTTP whitespace a
// fetch() header value loses anyway (so the X-TripPlanner-Key the Lambda checks and the key both sides derive from
// are the same value). The Lambda strips the same set (backend tenant_assertion.EDGE_KEY_WHITESPACE); an env value
// with a trailing newline still works.
export function normaliseEdgeKey(edgeKey: string): string {
  return edgeKey.replace(/^[ \t\r\n]+|[ \t\r\n]+$/g, "");
}

// The env name of the dedicated key (AA-CIS-Infra modules/environment/tripplanner_web.tf maps the secret's "current").
export const TENANT_ASSERTION_KEY_ENV = "TP_TENANT_ASSERTION_KEY";
// Same floor as the Lambda (config.TENANT_ASSERTION_KEY_MIN_LENGTH): a shorter dedicated key is a configuration error
// (no signing, 503), never a weak key in use.
export const MIN_DEDICATED_KEY_LENGTH = 32;
const PLACEHOLDER = "REPLACE_ME";

// The material the BFF signs with: the dedicated key when the env carries one, else the edge key (the cut-over
// fallback). "" = nothing usable (tenantHeaders answers 503).
export function assertionKeyMaterial(dedicatedKey: string | undefined, edgeKey: string): string {
  const dedicated = normaliseEdgeKey(dedicatedKey ?? "");
  if (dedicated && dedicated !== PLACEHOLDER) return dedicated.length >= MIN_DEDICATED_KEY_LENGTH ? dedicated : "";
  return normaliseEdgeKey(edgeKey);
}

// The routes' one call: the key material from the process env (TP_TENANT_ASSERTION_KEY, TRIPPLANNER_API_KEY).
export function assertionKeyMaterialFromEnv(env: Record<string, string | undefined>): string {
  return assertionKeyMaterial(env[TENANT_ASSERTION_KEY_ENV], env.TRIPPLANNER_API_KEY ?? "");
}

function b64url(buf: Buffer): string {
  return buf.toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

// keyMaterial: the dedicated key, or the edge key during the cut-over (assertionKeyMaterial).
export function deriveKey(keyMaterial: string): Buffer {
  return createHmac("sha256", normaliseEdgeKey(keyMaterial)).update(KEY_LABEL).digest();
}

export function signTenantAssertion(
  keyMaterial: string,
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
  return `${signed}.${b64url(createHmac("sha256", deriveKey(keyMaterial)).update(signed).digest())}`;
}
