// AA-871: the BFF request id. Run: npm test.
import { test } from "node:test";
import assert from "node:assert/strict";
import { REQUEST_ID_HEADER, acceptRequestId, requestIdFor, upstreamLogLine } from "./requestId.ts";
import { tripHeaders } from "./tripProxy.ts";

function headers(h: Record<string, string>) {
  const m = new Headers(h);
  return { get: (k: string) => m.get(k) };
}

test("a well-formed incoming id is kept", () => {
  assert.equal(requestIdFor(headers({ "X-Request-ID": "curl-check-1" })), "curl-check-1");
});

test("a missing or malformed id is replaced by a UUID", () => {
  for (const bad of ["", "a b", "x".repeat(129), "<script>", "idé"]) {
    assert.equal(acceptRequestId(bad), null);
  }
  const id = requestIdFor(headers({}));
  assert.match(id, /^[0-9a-f-]{36}$/);
  assert.notEqual(requestIdFor(headers({})), id);
});

test("the Lambda headers carry the request id", () => {
  const h = tripHeaders("key-1", null, "rid-1");
  assert.equal(h[REQUEST_ID_HEADER], "rid-1");
  assert.equal(REQUEST_ID_HEADER in tripHeaders("key-1", null), false);
});

test("the upstream log line has ids and names only", () => {
  const line = JSON.parse(upstreamLogLine({ route: "trip", op: "send", status: 200, ms: 12, request_id: "r" }));
  assert.deepEqual(line, { evt: "bff_upstream", route: "trip", op: "send", status: 200, ms: 12, request_id: "r" });
});
