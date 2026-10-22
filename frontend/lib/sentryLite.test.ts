// AA-871: the dependency-free Sentry reporter. Run: npm test.
import { test } from "node:test";
import assert from "node:assert/strict";
import { buildEvent, envelope, framesFrom, parseDsn, reportRequestError, scrubText } from "./sentryLite.ts";

const DSN = "https://pubkey@o1.ingest.example.io/42";
const REQ = { path: "/api/trip?trip_id=abc&q=jane", method: "POST", headers: { "x-request-id": "rid-1", cookie: "c=1" } };
const CTX = { routerKind: "App Router", routePath: "/api/trip", routeType: "route" };

test("parses a DSN into the envelope endpoint", () => {
  assert.deepEqual(parseDsn(DSN), {
    envelopeUrl: "https://o1.ingest.example.io/api/42/envelope/",
    publicKey: "pubkey",
    dsn: "https://pubkey@o1.ingest.example.io/42",
  });
  for (const bad of [undefined, "", "http://k@h/1", "https://h/1", "not a url"]) assert.equal(parseDsn(bad), null);
});

test("scrubs e-mails, phones, tokens and key-like params", () => {
  assert.equal(
    scrubText("mail jane@example.com +84 90 123 4567 Bearer abcdefghijkl ?api_key=SECRET eyJhbGciOi.eyJzdWIiOi.s"),
    "mail [email] [phone] Bearer [Filtered] ?api_key=*** [jwt]",
  );
});

test("the event has the request id, route and release, and no PII, headers or query", () => {
  const err = new Error("send failed for jane@example.com");
  const ev = buildEvent(err, REQ, CTX, { VERCEL_ENV: "production", VERCEL_GIT_COMMIT_SHA: "abc123" });
  const text = JSON.stringify(ev);
  assert.equal(text.includes("jane"), false);
  assert.equal(text.includes("c=1"), false);
  assert.deepEqual((ev.request as Record<string, string>), { method: "POST", url: "/api/trip" });
  const tags = ev.tags as Record<string, string>;
  assert.equal(tags.request_id, "rid-1");
  assert.equal(tags.route, "/api/trip");
  assert.equal(ev.release, "abc123");
  assert.equal(ev.environment, "production");
});

test("a malformed request id is not used as a tag", () => {
  const ev = buildEvent(new Error("x"), { ...REQ, headers: { "x-request-id": "a b<" } }, CTX, {});
  assert.equal("request_id" in (ev.tags as object), false);
});

test("V8 stack frames, oldest first", () => {
  const frames = framesFrom("Error: x\n    at inner (/var/task/.next/server/a.js:10:5)\n    at /var/task/node_modules/next/b.js:1:2");
  assert.equal(frames.length, 2);
  assert.equal(frames[0].in_app, false);
  assert.equal(frames[1].function, "inner");
  assert.equal(frames[1].lineno, 10);
});

test("the envelope is three JSON lines", () => {
  const ev = buildEvent(new Error("x"), REQ, CTX, {});
  const lines = envelope(ev, parseDsn(DSN)!).trim().split("\n").map((l) => JSON.parse(l));
  assert.equal(lines[0].event_id, ev.event_id);
  assert.deepEqual(lines[1], { type: "event" });
});

test("no DSN, nothing sent; a failing send never throws", async () => {
  let calls = 0;
  const send = (async () => {
    calls += 1;
    throw new Error("network");
  }) as unknown as typeof fetch;
  assert.equal(await reportRequestError(new Error("x"), REQ, CTX, {}, send), false);
  assert.equal(calls, 0);
  assert.equal(await reportRequestError(new Error("x"), REQ, CTX, { SENTRY_DSN: DSN }, send), false);
  assert.equal(calls, 1);
});

test("posts to the envelope endpoint with the public key", async () => {
  let seen: { url: string; init: RequestInit } | null = null;
  const send = (async (url: string, init: RequestInit) => {
    seen = { url, init };
    return new Response("{}", { status: 200 });
  }) as unknown as typeof fetch;
  assert.equal(await reportRequestError(new Error("boom"), REQ, CTX, { SENTRY_DSN: DSN }, send), true);
  assert.equal(seen!.url, "https://o1.ingest.example.io/api/42/envelope/");
  const auth = (seen!.init.headers as Record<string, string>)["x-sentry-auth"];
  assert.match(auth, /sentry_key=pubkey/);
});

test("SENTRY_ENVIRONMENT wins over the Vercel target", () => {
  const ev = buildEvent(new Error("x"), REQ, CTX, { SENTRY_ENVIRONMENT: "dev", VERCEL_ENV: "production" });
  assert.equal(ev.environment, "dev");
  assert.equal(buildEvent(new Error("x"), REQ, CTX, { VERCEL_ENV: "preview" }).environment, "preview");
});
