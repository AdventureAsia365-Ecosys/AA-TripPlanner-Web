// AA-994 S5b — Playwright config of the visual run (plan section 4.1): every page of pages.ts at 1440 x 900 and
// 390 x 844 (mobile, touch), light and dark (the spec loops the themes). BASE_URL is the deployment to capture: a
// per-PR preview or the production host. Captures run on GitHub's network (.github/workflows/visual.yml).
import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: ".",
  testMatch: "visual.spec.ts",
  outputDir: "results/test-output",
  fullyParallel: true,
  workers: 2,
  retries: 0,
  timeout: 120_000,
  reporter: [["list"]],
  use: {
    baseURL: process.env.BASE_URL,
    // Screenshots must not catch a reveal or the ribbon mid-move: under reduced motion the restore script sets
    // data-motion="reduce" and every page is static (app/globals.css).
    reducedMotion: "reduce",
    trace: "off",
    video: "off",
  },
  projects: [
    { name: "1440", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } },
    {
      name: "390",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 390, height: 844 },
        deviceScaleFactor: 2,
        isMobile: true,
        hasTouch: true,
      },
    },
  ],
});
