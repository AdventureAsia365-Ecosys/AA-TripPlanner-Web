// S294 hardening: JSON answers name their charset. Run: npm test.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { JSON_CONTENT_TYPE, jsonInit } from "./jsonResponse.ts";

const ROOT = join(import.meta.dirname, "..");

test("jsonInit sets the JSON content type with its charset and keeps the rest", () => {
  assert.equal(JSON_CONTENT_TYPE, "application/json; charset=utf-8");
  const init = jsonInit({ status: 403, headers: { "cache-control": "no-store", "content-type": "application/json" } });
  const headers = new Headers(init.headers);
  assert.equal(init.status, 403);
  assert.equal(headers.get("content-type"), JSON_CONTENT_TYPE);
  assert.equal(headers.get("cache-control"), "no-store");
  assert.equal(new Headers(jsonInit().headers).get("content-type"), JSON_CONTENT_TYPE);
});

test("a JSON Response built with jsonInit carries the charset", async () => {
  const res = Response.json({ ok: true }, jsonInit({ status: 200 }));
  assert.equal(res.headers.get("content-type"), JSON_CONTENT_TYPE);
  assert.deepEqual(await res.json(), { ok: true });
});

function sources(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) out.push(...sources(path));
    else if (/\.(ts|tsx)$/.test(name) && !name.endsWith(".test.ts")) out.push(path);
  }
  return out;
}

/** The argument text of each NextResponse.json( ... ) call (balanced parentheses; good enough for this code base). */
function jsonCalls(text: string): string[] {
  const calls: string[] = [];
  const marker = "NextResponse.json(";
  for (let at = text.indexOf(marker); at !== -1; at = text.indexOf(marker, at + 1)) {
    let depth = 0;
    let end = at + marker.length;
    for (; end < text.length; end++) {
      const c = text[end];
      if (c === "(") depth++;
      else if (c === ")") {
        if (depth === 0) break;
        depth--;
      }
    }
    calls.push(text.slice(at + marker.length, end));
  }
  return calls;
}

test("every JSON answer of the app routes and the proxy goes through jsonInit", () => {
  const files = [...sources(join(ROOT, "app")), join(ROOT, "proxy.ts")];
  const bare: string[] = [];
  for (const file of files) {
    const text = readFileSync(file, "utf8");
    for (const args of jsonCalls(text)) if (!args.includes("jsonInit(")) bare.push(`${file}: NextResponse.json(${args.slice(0, 60)}`);
    if (/["']content-type["']\s*:\s*["']application\/json["']/i.test(text)) bare.push(`${file}: a bare application/json content type`);
  }
  assert.deepEqual(bare, []);
});
