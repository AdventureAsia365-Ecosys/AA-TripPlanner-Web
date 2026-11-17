// AA-1115 follow-up — the rendered guard: an agency's pages, as the server sends them, never carry Adventure Asia.
//
// lib/agencyBrand.test.ts reads the source an agency page imports. It missed what Next adds to every page: the root
// app/not-found.tsx is rendered into the RSC payload of each page (the not-found boundary of the root layout), so AA's
// 404 (its bar, AI band and footer) rode along, unseen, inside the HTML of /{slug}/plan, /privacy and /terms. This
// script checks the real output instead: it runs the production build (`next start`, after `npm run build`) against a
// fake browse API that serves one invented agency, fetches the agency's pages on our host and
// on its own domain, and fails when the HTML names Adventure Asia (or "AA AI"). Two controls keep the check honest:
// AA's own pages (and AA's 404 for an unknown slug) still say Adventure Asia, and every agency page names the agency.
//
// Run (CI "Frontend (lint + build)", after Build): npm run build && node scripts/check-agency-render.mjs
// No network beyond localhost, no key: the browse API is the fake below.
import { spawn } from "node:child_process";
import { readFileSync } from "node:fs";
import { createServer, request } from "node:http";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const FRONTEND = join(dirname(fileURLToPath(import.meta.url)), "..");
const BRAND_RE = /adventure[\s\u00a0]+asia|adventure\.asia|\bAA AI\b/i;
const AGENCY = { slug: "quillmere-voyages", name: "Quillmere Voyages", host: "trip.quillmere.example" };

const tenant = (slug, name, kind) => ({
  tenant: {
    tenant_id: "0b6c3f1e-7d2a-4c59-9e1b-5a8f2d4c6e01",
    slug,
    name,
    kind,
    launcher: false,
    branding_badge: false,
    theme: {},
    locale: "en",
  },
});

// The browse API: the tenant lookups answer, everything else is a 404 (a page that needs more renders its own state).
function fakeBrowse() {
  const server = createServer((req, res) => {
    const path = decodeURIComponent((req.url ?? "").split("?")[0]);
    let body = null;
    if (path === `/browse/tenant/${AGENCY.slug}` || path === `/browse/tenant-host/${AGENCY.host}`) {
      body = tenant(AGENCY.slug, AGENCY.name, "agency");
    }
    res.writeHead(body ? 200 : 404, { "content-type": "application/json" });
    res.end(JSON.stringify(body ?? { error: "not_found" }));
  });
  return new Promise((ok) => server.listen(0, "127.0.0.1", () => ok(server)));
}

function get(port, path, host) {
  return new Promise((ok, fail) => {
    const req = request({ host: "127.0.0.1", port, path, headers: host ? { host } : {} }, (res) => {
      let html = "";
      res.setEncoding("utf8");
      res.on("data", (c) => (html += c));
      res.on("end", () => ok({ status: res.statusCode ?? 0, html, location: res.headers.location ?? "" }));
    });
    req.on("error", fail);
    req.setTimeout(30_000, () => req.destroy(new Error(`timeout ${path}`)));
    req.end();
  });
}

async function startNext(port, browsePort) {
  const env = {
    ...process.env,
    NODE_ENV: "production",
    BROWSE_API_URL: `http://127.0.0.1:${browsePort}`,
    TRIPPLANNER_API_KEY: "render-check-placeholder",
    NEXT_TELEMETRY_DISABLED: "1",
  };
  const next = spawn(process.execPath, [join(FRONTEND, "node_modules/next/dist/bin/next"), "start", "-p", String(port)], {
    cwd: FRONTEND,
    env,
    stdio: ["ignore", "pipe", "pipe"],
  });
  let log = "";
  next.stdout.on("data", (c) => (log += c));
  next.stderr.on("data", (c) => (log += c));
  for (let i = 0; i < 120; i++) {
    if (next.exitCode !== null) throw new Error(`next start exited (${next.exitCode}):\n${log}`);
    try {
      const r = await get(port, "/privacy");
      if (r.status === 200) return next;
    } catch {
      // not listening yet
    }
    await new Promise((ok) => setTimeout(ok, 500));
  }
  next.kill();
  throw new Error(`next start did not answer within 60 s:\n${log}`);
}

// [label, path, Host header (an agency's own domain) or null, expected status]
const AGENCY_PAGES = [
  ["planner", `/${AGENCY.slug}/plan`, null, 200],
  ["shared trip view", `/${AGENCY.slug}/plan/shared`, null, 200],
  ["privacy", `/${AGENCY.slug}/privacy`, null, 200],
  ["terms", `/${AGENCY.slug}/terms`, null, 200],
  ["unknown sub-path (agency 404)", `/${AGENCY.slug}/no-such-page`, null, 404],
  ["unknown tour (agency 404)", `/${AGENCY.slug}/tours/no-such-tour`, null, 404],
  ["own domain: planner", "/plan", AGENCY.host, 200],
  ["own domain: privacy", "/privacy", AGENCY.host, 200],
  ["own domain: terms", "/terms", AGENCY.host, 200],
];

// AA's own site keeps its brand (the controls: the check would pass vacuously if the brand never rendered at all).
const AA_PAGES = [
  ["AA privacy", "/privacy", 200],
  ["AA 404 (unknown slug)", "/no-such-agency-xyz/plan", 404],
  ["AA planner", "/plan", 200],
];

// AA's 404 renders behind a client boundary (components/site/AaNotFound.tsx, next/dynamic). Its prerendered page must
// still carry the bar, the "Off the trail" band and the footer as server-rendered HTML (a 404 under a dynamic route is
// rendered in the browser by Next, as before this change; the static one is not).
const AA_STATIC_404 = ".next/server/app/_not-found.html";
const AA_404_MARKUP = /<header[^>]*class="tp-header[\s\S]*>Off the trail<[\s\S]*<footer[^>]*>[\s\S]*© Adventure Asia<\/p>/;

async function main() {
  const port = Number(process.env.RENDER_CHECK_PORT || 3917);
  const browse = await fakeBrowse();
  const next = await startNext(port, browse.address().port);
  const failures = [];
  try {
    for (const [label, path, host, status] of AGENCY_PAGES) {
      const r = await get(port, path, host);
      const where = `${label} (${host ? `${host}` : ""}${path})`;
      if (r.status !== status) failures.push(`${where}: status ${r.status}, expected ${status}`);
      const hit = r.html.match(BRAND_RE);
      if (hit) {
        const i = hit.index ?? 0;
        failures.push(`${where}: names Adventure Asia: …${r.html.slice(Math.max(0, i - 120), i + 80)}…`);
      }
      if (status === 200 && !r.html.includes(AGENCY.name)) failures.push(`${where}: the agency's name is missing`);
      console.log(`${hit || r.status !== status ? "FAIL" : "ok  "} ${where}: ${r.status}, ${r.html.length} bytes`);
    }
    for (const [label, path, status] of AA_PAGES) {
      const r = await get(port, path, null);
      const ok = r.status === status && /Adventure Asia/.test(r.html);
      if (!ok) failures.push(`${label} (${path}): status ${r.status} (expected ${status}), AA brand ${ok ? "" : "missing"}`);
      console.log(`${ok ? "ok  " : "FAIL"} ${label} (${path}): ${r.status}, keeps the AA brand`);
    }
  } finally {
    next.kill();
    browse.close();
  }
  const static404 = readFileSync(join(FRONTEND, AA_STATIC_404), "utf8");
  const ssr = AA_404_MARKUP.test(static404) && !static404.includes("__next_error__");
  if (!ssr) failures.push(`${AA_STATIC_404}: AA's bar, band or footer is not server-rendered`);
  console.log(`${ssr ? "ok  " : "FAIL"} AA 404 prerendered with its bar, band and footer (${AA_STATIC_404})`);
  if (failures.length) {
    console.error(`\n${failures.length} rendered-brand failure(s):\n- ${failures.join("\n- ")}`);
    process.exit(1);
  }
  console.log("\nAgency pages render the agency's brand only; AA's own pages keep theirs.");
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
