// AA-994 S5b (plan section 4.2) — the visual run of the public TripPlanner pages. For every page of pages.ts, in
// the project's viewport (1440 / 390) and both themes:
//   - no page-level horizontal overflow (the five widest offenders are named);
//   - no console error and no uncaught page error;
//   - no failed same-origin request (status >= 400 or a network failure), except the document's own expected status;
//   - a full-page PNG after the fonts are ready, the page has been scrolled through (lazy images) and every
//     content-visibility section is painted (a full-page shot of a skipped section is a blank band; lessons, S255).
// Each check result goes to results/report/<page>__<viewport>__<theme>.json; summary.mjs merges them into the job
// summary and report.json. Read-only: the run only navigates (no click that changes data).
// Preview deployments sit behind Vercel protection: with VERCEL_AUTOMATION_BYPASS_SECRET set, the bypass header is
// sent to the BASE_URL origin only.
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { expect, test, type Page } from "@playwright/test";
import { PAGES, type VisualPage } from "./pages.ts";

const THEMES = ["light", "dark"] as const;
const REPORT_DIR = "results/report";
const SHOT_DIR = "results/shots";
const ALLOW = JSON.parse(readFileSync(new URL("./allowlist.json", import.meta.url), "utf8")) as {
  console: { match: string; why: string }[];
  requests: { match: string; why: string }[];
};
const allowed = (list: { match: string }[], text: string) => list.some((a) => text.includes(a.match));

async function bypass(page: Page, origin: string) {
  const secret = process.env.VERCEL_AUTOMATION_BYPASS_SECRET;
  if (!secret) return;
  await page.route(`${origin}/**`, (route) =>
    route.continue({ headers: { ...route.request().headers(), "x-vercel-protection-bypass": secret } }),
  );
}

// A dynamic route's real path, read on its list page in a page of its own (closed after), so none of the list page's
// requests can be cut off by the capture's navigation.
async function resolvePath(page: Page, p: VisualPage, origin: string): Promise<string> {
  if (p.path) return p.path;
  const r = p.resolve!;
  const list = await page.context().newPage();
  try {
    await bypass(list, origin);
    await list.goto(r.from, { waitUntil: "domcontentloaded" });
    const hrefs = await list.$$eval("a[href]", (as) => as.map((a) => a.getAttribute("href") ?? ""));
    const hit = hrefs.find((h) => h.startsWith(r.hrefPrefix) && h.length > r.hrefPrefix.length);
    if (!hit) throw new Error(`no link starting with ${r.hrefPrefix} on ${r.from}`);
    return hit;
  } finally {
    await list.close();
  }
}

// Scroll to the bottom in viewport steps, so every lazy image and content-visibility section renders, then back up.
async function renderWholePage(page: Page) {
  await page.evaluate(async () => {
    const step = Math.max(200, Math.floor(window.innerHeight * 0.8));
    for (let y = 0; y < document.documentElement.scrollHeight; y += step) {
      window.scrollTo(0, y);
      await new Promise((r) => setTimeout(r, 120));
    }
    window.scrollTo(0, 0);
  });
}

for (const p of PAGES) {
  for (const theme of THEMES) {
    test(`${p.slug} ${theme}`, async ({ page }, info) => {
      const viewport = info.project.name;
      const origin = new URL(info.project.use.baseURL ?? process.env.BASE_URL ?? "").origin;
      const consoleErrors: string[] = [];
      const failed: string[] = [];
      await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
      await page.addInitScript((t) => {
        try {
          window.localStorage.setItem("tp_theme", t);
        } catch {
          /* storage blocked: the OS colour scheme (emulated above) gives the same theme */
        }
      }, theme);
      await bypass(page, origin);
      const path = await resolvePath(page, p, origin);
      const docUrl = new URL(path, origin).toString();

      page.on("console", (m) => {
        if (m.type() !== "error" || allowed(ALLOW.console, m.text())) return;
        // Chromium logs the document's own non-200 answer ("Failed to load resource: … 404"): expected on the 404 page.
        if (p.status && m.location().url === docUrl && m.text().startsWith("Failed to load resource")) return;
        consoleErrors.push(m.text().slice(0, 300));
      });
      page.on("pageerror", (e) => consoleErrors.push(`pageerror: ${e.message.slice(0, 300)}`));
      page.on("requestfailed", (r) => {
        // net::ERR_ABORTED is a request the page itself cancelled (a router prefetch, an image replaced), not a failure.
        if (r.failure()?.errorText === "net::ERR_ABORTED") return;
        if (r.url().startsWith(origin) && !allowed(ALLOW.requests, r.url())) {
          failed.push(`${r.failure()?.errorText ?? "failed"} ${r.url()}`);
        }
      });
      page.on("response", (r) => {
        const url = r.url();
        if (!url.startsWith(origin) || allowed(ALLOW.requests, url)) return;
        const expected = url === docUrl ? (p.status ?? 200) : null;
        if (expected !== null ? r.status() !== expected : r.status() >= 400) failed.push(`${r.status()} ${url}`);
      });

      const res = await page.goto(path, { waitUntil: "load" });
      await page.waitForLoadState("networkidle", { timeout: 15_000 }).catch(() => {});
      await page.evaluate(() => document.fonts.ready.then(() => true));
      await renderWholePage(page);
      // A full-page shot does not scroll, so a content-visibility section away from the viewport would be a blank band:
      // paint every one for the capture (and the overflow probe). Capture-only; the page itself is unchanged.
      await page.addStyleTag({ content: ".tp-cv { content-visibility: visible !important; }" });
      await page.evaluate(() => document.querySelectorAll<HTMLImageElement>('img[loading="lazy"]').forEach((i) => (i.loading = "eager")));
      await page.waitForLoadState("networkidle", { timeout: 10_000 }).catch(() => {});
      // Lazy images far from the viewport start now (eager for the capture); they get a bounded wait to finish.
      await page
        .waitForFunction(() => [...document.images].every((i) => !i.getAttribute("src") || i.complete), null, { timeout: 10_000 })
        .catch(() => {});

      const appliedTheme = await page.evaluate(() => document.documentElement.getAttribute("data-theme"));
      const overflow = await page.evaluate(() => {
        const vw = window.innerWidth;
        const doc = Math.max(document.documentElement.scrollWidth, document.body.scrollWidth);
        const offenders = [...document.querySelectorAll<HTMLElement>("body *")]
          .filter((el) => {
            if (el.closest("[data-kit-scroll], .tp-snap-row, [data-ribbon]")) return false; // own horizontal scrollers
            const r = el.getBoundingClientRect();
            return r.width > 0 && r.right > vw + 1;
          })
          .slice(0, 5)
          .map((el) => el.tagName.toLowerCase() + (el.id ? `#${el.id}` : "") + (el.className ? `.${String(el.className).split(" ").slice(0, 3).join(".")}` : ""));
        return { doc, vw, offenders };
      });

      const name = `${p.slug}__${viewport}__${theme}`;
      mkdirSync(SHOT_DIR, { recursive: true });
      await page.screenshot({ path: `${SHOT_DIR}/${name}.png`, fullPage: true });

      const result = {
        page: p.slug,
        path,
        viewport,
        theme,
        status: res?.status() ?? null,
        appliedTheme,
        overflow,
        consoleErrors,
        failedRequests: failed,
      };
      mkdirSync(REPORT_DIR, { recursive: true });
      writeFileSync(`${REPORT_DIR}/${name}.json`, JSON.stringify(result, null, 1));

      expect.soft(res?.status(), "document status").toBe(p.status ?? 200);
      expect.soft(appliedTheme, "data-theme on <html>").toBe(theme);
      expect.soft(overflow.doc, `page scrolls sideways: ${overflow.offenders.join(", ")}`).toBeLessThanOrEqual(overflow.vw + 1);
      expect.soft(consoleErrors, "console errors").toEqual([]);
      expect.soft(failed, "failed same-origin requests").toEqual([]);
    });
  }
}
