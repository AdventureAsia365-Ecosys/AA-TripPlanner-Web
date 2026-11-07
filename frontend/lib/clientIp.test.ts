// AA-1038 R5 hosting: the traveller's address behind Vercel and behind CloudFront (ECS). Run: npm test.
import { test } from "node:test";
import assert from "node:assert/strict";
import { CLOUDFRONT_XFF_HOPS, clientIp, clientIpSource, viewerAddressIp } from "./clientIp.ts";
import { travellerIp } from "./cognito.ts";

const h = (m: Record<string, string>) => ({ get: (n: string) => m[n.toLowerCase()] ?? null });

test("only the exact env value cloudfront switches the source", () => {
  assert.equal(clientIpSource({}), "forwarded");
  assert.equal(clientIpSource({ TP_CLIENT_IP_SOURCE: "forwarded" }), "forwarded");
  assert.equal(clientIpSource({ TP_CLIENT_IP_SOURCE: " CloudFront " }), "cloudfront");
  assert.equal(clientIpSource({ TP_CLIENT_IP_SOURCE: "cloudfront-ish" }), "forwarded");
});

test("forwarded (Vercel): the first x-forwarded-for entry, else x-real-ip; never CloudFront-Viewer-Address", () => {
  assert.equal(clientIp(h({ "x-forwarded-for": "198.51.100.4, 10.0.0.1" }), "forwarded"), "198.51.100.4");
  assert.equal(clientIp(h({ "x-real-ip": "2001:db8::1" }), "forwarded"), "2001:db8::1");
  // On Vercel a viewer can send any header: the CloudFront one is not trusted there.
  assert.equal(clientIp(h({ "cloudfront-viewer-address": "203.0.113.9:443" }), "forwarded"), null);
  assert.equal(clientIp(h({ "x-forwarded-for": "not-an-ip" }), "forwarded"), null);
});

test("cloudfront: CloudFront-Viewer-Address wins over a forged x-forwarded-for", () => {
  const headers = h({ "cloudfront-viewer-address": "203.0.113.9:51234", "x-forwarded-for": "192.0.2.1, 203.0.113.9, 10.0.1.5" });
  assert.equal(clientIp(headers, "cloudfront"), "203.0.113.9");
  assert.equal(clientIp(h({ "cloudfront-viewer-address": "2001:db8:aa:1::7:443" }), "cloudfront"), "2001:db8:aa:1::7");
});

test("cloudfront without the viewer header: the x-forwarded-for entry two from the right, never the leftmost", () => {
  assert.equal(CLOUDFRONT_XFF_HOPS, 2);
  // viewer-sent "192.0.2.1", CloudFront appended the viewer 198.51.100.7, the ALB appended the edge 10.0.1.5.
  assert.equal(clientIp(h({ "x-forwarded-for": "192.0.2.1, 198.51.100.7, 10.0.1.5" }), "cloudfront"), "198.51.100.7");
  assert.equal(clientIp(h({ "x-forwarded-for": "10.0.1.5" }), "cloudfront"), null);
  assert.equal(clientIp(h({}), "cloudfront"), null);
});

test("viewer address parsing refuses values without a port or with junk", () => {
  assert.equal(viewerAddressIp("198.51.100.7:40000"), "198.51.100.7");
  assert.equal(viewerAddressIp("198.51.100.7"), null);
  assert.equal(viewerAddressIp("evil:443"), null);
  assert.equal(viewerAddressIp(null), null);
  assert.equal(viewerAddressIp("[2001:db8::1]:443"), "2001:db8::1");
});

test("lib/cognito travellerIp follows the same source", () => {
  const headers = h({ "cloudfront-viewer-address": "203.0.113.9:443", "x-forwarded-for": "192.0.2.1, 10.0.1.5" });
  assert.equal(travellerIp(headers, "cloudfront"), "203.0.113.9");
  assert.equal(travellerIp(headers, "forwarded"), "192.0.2.1");
});
