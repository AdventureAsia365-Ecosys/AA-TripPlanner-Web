// AA-1041 (R7 slice 1) — Playwright config of the planner interaction specs (planner.spec.ts): the same two viewports
// as the visual run (1440 x 900 desktop, 390 x 844 touch phone). BASE_URL = the deployment to test (the public host,
// or a local `next start` of a build with the Mapbox token). Every /api/* answer is mocked by the spec.
import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: ".",
  testMatch: "planner.spec.ts",
  outputDir: "results/planner-output",
  fullyParallel: true,
  workers: 2,
  retries: 0,
  timeout: 90_000,
  reporter: [["list"]],
  use: {
    baseURL: process.env.BASE_URL,
    reducedMotion: "reduce",
    trace: "retain-on-failure",
    video: "off",
    acceptDownloads: true,
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
