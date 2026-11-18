// AA-1111 (S284, ZAP baseline) — the security headers of next.config.mjs: the full CSP (every fetch directive explicit,
// no bare wildcard, the Mapbox GL hosts and blob workers), COOP same-origin, HSTS without preload, on every path.
// Run: npm test (node --test with type stripping).
import { test } from "node:test";
import assert from "node:assert/strict";
import nextConfig, { SECURITY_HEADERS, contentSecurityPolicy } from "../next.config.mjs";

function directives(csp: string): Map<string, string[]> {
  return new Map(
    csp.split(";").map((d) => {
      const [name, ...src] = d.trim().split(/\s+/);
      return [name, src] as [string, string[]];
    }),
  );
}

test("every fetch directive is explicit and none is a bare wildcard", () => {
  const d = directives(contentSecurityPolicy());
  for (const name of ["default-src", "script-src", "style-src", "font-src", "img-src", "connect-src", "worker-src",
    "child-src", "frame-src", "object-src", "base-uri", "form-action", "frame-ancestors"]) {
    assert.ok(d.has(name), `missing ${name}`);
    assert.ok(!d.get(name)!.includes("*"), `${name} has a bare *`);
  }
  assert.deepEqual(d.get("default-src"), ["'self'"]);
  assert.deepEqual(d.get("frame-ancestors"), ["'none'"]);
  assert.deepEqual(d.get("object-src"), ["'none'"]);
  assert.deepEqual(d.get("form-action"), ["'self'"]);
  // frame-ancestors first: .github/scripts/check_security_headers.sh greps it on the deployed host.
  assert.ok(contentSecurityPolicy().startsWith("frame-ancestors 'none'; "));
});

test("the map can load: Mapbox API, tiles and telemetry, blob workers", () => {
  const d = directives(contentSecurityPolicy());
  for (const host of ["https://api.mapbox.com", "https://*.tiles.mapbox.com", "https://events.mapbox.com"]) {
    assert.ok(d.get("connect-src")!.includes(host), host);
  }
  assert.ok(d.get("worker-src")!.includes("blob:"));
  assert.ok(d.get("child-src")!.includes("blob:"));
  assert.ok(d.get("img-src")!.includes("blob:") && d.get("img-src")!.includes("data:"));
});

test("eval only under next dev", () => {
  assert.ok(!directives(contentSecurityPolicy()).get("script-src")!.includes("'unsafe-eval'"));
  assert.ok(directives(contentSecurityPolicy({ dev: true })).get("script-src")!.includes("'unsafe-eval'"));
});

test("the static set: HSTS without preload, nosniff, COOP, Permissions-Policy, the CSP", () => {
  const h = new Map(SECURITY_HEADERS.map(({ key, value }: { key: string; value: string }) => [key, value]));
  assert.equal(h.get("Strict-Transport-Security"), "max-age=63072000; includeSubDomains");
  assert.equal(h.get("X-Content-Type-Options"), "nosniff");
  assert.equal(h.get("Cross-Origin-Opener-Policy"), "same-origin");
  assert.equal(h.get("X-Frame-Options"), "DENY");
  assert.equal(h.get("Referrer-Policy"), "strict-origin-when-cross-origin");
  assert.match(h.get("Permissions-Policy")!, /camera=\(\)/);
  assert.equal(h.get("Content-Security-Policy"), contentSecurityPolicy());
});

test("next.config.mjs sends them on every path, without x-powered-by", async () => {
  assert.equal(nextConfig.poweredByHeader, false);
  const rules = (await nextConfig.headers?.()) ?? [];
  const all = rules.find((r: { source: string }) => r.source === "/(.*)");
  assert.ok(all, "a /(.*) rule");
  for (const { key } of SECURITY_HEADERS) {
    assert.ok(all.headers.some((h: { key: string }) => h.key === key), key);
  }
});
