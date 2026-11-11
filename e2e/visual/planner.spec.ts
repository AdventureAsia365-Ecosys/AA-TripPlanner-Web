// AA-1041 (R7 slice 1) — planner interactions at 1440 x 900 and 390 x 844 (touch), on any deployment (BASE_URL):
//   - reorder: keyboard on a stop handle (Home), a touch drag of a stop handle (390), a tap on the handle and a menu
//     item (390, no drag), undo; each saved as ONE reorder op carrying every component id (the way the planner saves
//     an order); two quick key presses with a slow first answer are saved in order and never jump back;
//   - map / list sync: hover of a stop row highlights its map marker, hover / click of a marker highlights the row,
//     "Show on map" on a phone switches to the map with the stop highlighted;
//   - photos and icons: a stop with a CIS photo shows it, one without shows its icon tile;
//   - kit states: an error with a retry when the saved trip cannot be read; the read-only share view, which prints in
//     the light theme for a viewer on the dark theme.
// Every /api/* call is answered by the test (page.route): no trip is written, no catalog row is read, the fixture
// names are invented. The map itself is real (Mapbox, the deployment's token), so the sync tests need a build with
// NEXT_PUBLIC_MAPBOX_TOKEN. Run: BASE_URL=<host> npx playwright test -c planner.config.ts
import { expect, test, type Page, type Route } from "@playwright/test";

const IDS = { sessionId: "6f1c2a3b-4d5e-4f60-8a7b-9c0d1e2f3a4b", tripId: "7a2b3c4d-5e6f-4a70-8b8c-9d0e1f2a3b4c" };
const A = "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d";
const B = "1b2c3d4e-5f6a-4b7c-9d8e-0f1a2b3c4d5e";
const C = "2c3d4e5f-6a7b-4c8d-8e9f-1a2b3c4d5e6f";
const D = "3d4e5f6a-7b8c-4d9e-9f0a-2b3c4d5e6f70";
const PHOTO = "https://photos.invalid/tp-test/pell.png";

// A and B/C (one shared tour day) and D (no tour day). Invented places; plausible inland points for the map.
const STOPS = [
  { component_id: A, name: "Morning market walk", destination_name: "Dravin", activity: "local_immersion",
    source_tour_id: "src-test", source_day_index: 1, lat: 19.88, lng: 102.14, destination_id: "dd000001-0000-4000-8000-000000000001" },
  { component_id: B, name: "Old town temple visit", destination_name: "Pell", activity: "cultural_heritage",
    source_tour_id: "src-test", source_day_index: 2, lat: 18.93, lng: 102.45, destination_id: "dd000001-0000-4000-8000-000000000002" },
  { component_id: C, name: "Cooking class with a family", destination_name: "Pell", activity: "culinary",
    source_tour_id: "src-test", source_day_index: 2, lat: 18.93, lng: 102.45, destination_id: "dd000001-0000-4000-8000-000000000002" },
  { component_id: D, name: "Kayaking on Lake Miren", destination_name: "Miren", activity: "water_activities",
    source_tour_id: null, source_day_index: null, lat: 17.97, lng: 102.6, destination_id: "dd000001-0000-4000-8000-000000000003" },
];

function withDays(ids: string[]) {
  let day = 0;
  let prev: string | null = null;
  return ids.map((id) => {
    const s = STOPS.find((x) => x.component_id === id)!;
    const key = s.source_tour_id && s.source_day_index ? `${s.source_tour_id}|${s.source_day_index}` : null;
    if (!(key && key === prev)) day += 1;
    prev = key;
    return { ...s, day, rationale: null, precision: "point", located_by: "manual" };
  });
}

// A 1 x 1 PNG for the stop photo.
const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
  "base64",
);

interface Mock {
  reorders: string[][];
  // When each reorder answer left the mock, in the order the requests arrived.
  answered: number[];
  tripStatus: number;
}

async function mockApis(page: Page, opts: { tripStatus?: number; reorderDelayMs?: number[] } = {}): Promise<Mock> {
  const mock: Mock = { reorders: [], answered: [], tripStatus: opts.tripStatus ?? 200 };
  let order = STOPS.map((s) => s.component_id);
  await page.addInitScript((ids) => {
    try {
      window.localStorage.setItem("aa-tripplanner:ids", JSON.stringify(ids));
    } catch {
      /* storage blocked: the test fails on the missing trip */
    }
  }, IDS);
  await page.route(PHOTO, (r) => r.fulfill({ status: 200, contentType: "image/png", body: PNG }));
  await page.route("**/api/trip**", async (route: Route) => {
    const req = route.request();
    const url = new URL(req.url());
    if (req.method() === "GET") {
      if (url.searchParams.get("resource") === "coverage") return route.fulfill({ status: 404, json: { error: "not found" } });
      if (url.searchParams.get("resource")) return route.fulfill({ json: { suggestions: [], activities: [] } });
      if (mock.tripStatus !== 200) return route.fulfill({ status: mock.tripStatus, json: { error: "unavailable" } });
      return route.fulfill({ json: { trip_id: IDS.tripId, itinerary: withDays(order), custom_days: [], unavailable_stops: [] } });
    }
    const body = req.postDataJSON() as { op: string; ordered_component_ids?: string[] };
    if (body.op === "reorder" && body.ordered_component_ids) {
      const n = mock.reorders.push(body.ordered_component_ids) - 1;
      const mine = body.ordered_component_ids;
      const delay = opts.reorderDelayMs?.[n] ?? 0;
      if (delay) await new Promise((r) => setTimeout(r, delay));
      order = mine;
      mock.answered[n] = Date.now();
      return route.fulfill({ json: { trip_id: IDS.tripId, itinerary: withDays(mine) } });
    }
    return route.fulfill({ status: 400, json: { error: "not mocked" } });
  });
  await page.route("**/api/browse**", async (route: Route) => {
    const url = new URL(route.request().url());
    const resource = url.searchParams.get("resource");
    if (resource === "stops") {
      const ids = (url.searchParams.get("ids") ?? "").split(",");
      return route.fulfill({
        json: {
          stops: STOPS.filter((s) => ids.includes(s.component_id)).map((s) => ({
            id: s.component_id, name: s.name, activity: s.activity, duration_hint: null,
            source_tour_id: s.source_tour_id, source_day_index: s.source_day_index, destination_id: s.destination_id,
            destination_name: s.destination_name, country: "Laos", precision: "point",
            // Pell has a CIS photo; the other places do not (their rows show the icon tile).
            photo_url: s.destination_name === "Pell" ? PHOTO : null,
            photo_url_small: s.destination_name === "Pell" ? PHOTO : null,
          })),
        },
      });
    }
    if (resource === "countries") return route.fulfill({ json: { countries: [] } });
    if (resource === "tiles" || resource === "by-country") return route.fulfill({ json: { destinations: [] } });
    if (resource === "shapes") return route.fulfill({ json: { level: "lo", shapes: [] } });
    return route.fulfill({ json: {} });
  });
  return mock;
}

async function openPlanner(page: Page, mobile: boolean) {
  const res = await page.goto("/plan", { waitUntil: "load" });
  if (res?.status() === 202) {
    // The web ACL's Challenge on the first document load: the browser solves it and reloads the page.
    await page.waitForResponse((r) => new URL(r.url()).pathname === "/plan" && r.status() !== 202, { timeout: 30_000 });
    await page.waitForLoadState("load");
  }
  if (mobile) await page.getByRole("button", { name: /Your trip/ }).click();
  await expect(page.getByTestId("day-list")).toBeVisible({ timeout: 20_000 });
}

const isMobile = (name: string) => name === "390";
const stopNames = (page: Page) => page.getByTestId("day-list").locator("[data-stop] p.truncate").allInnerTexts();

test("reorder by keyboard is saved as one reorder op, and undo puts it back", async ({ page }, info) => {
  const mock = await mockApis(page);
  await openPlanner(page, isMobile(info.project.name));
  const handle = page.locator(`[data-handle="${D}"]`);
  await handle.focus();
  await page.keyboard.press("Home");
  await expect.poll(() => mock.reorders.length).toBe(1);
  expect(mock.reorders[0]).toEqual([D, A, B, C]);
  await expect.poll(() => stopNames(page)).toEqual([
    "Kayaking on Lake Miren", "Morning market walk", "Old town temple visit", "Cooking class with a family"]);
  await expect(page.getByTestId("reorder-announce")).toHaveText("Moved Day 3: Kayaking on Lake Miren to position 1 of 4.");
  await expect(page.locator(`[data-handle="${D}"]`)).toBeFocused();
  await page.getByTestId("reorder-undo").click();
  await expect.poll(() => mock.reorders.length).toBe(2);
  expect(mock.reorders[1]).toEqual([A, B, C, D]);
  await expect(page.getByTestId("reorder-undo")).toHaveCount(0);
});

test("two quick key presses with a slow first answer: saved in order, the list never jumps back", async ({ page }, info) => {
  const mock = await mockApis(page, { reorderDelayMs: [1500, 0] });
  await openPlanner(page, isMobile(info.project.name));
  await page.locator(`[data-handle="${D}"]`).focus();
  await page.keyboard.press("ArrowUp");
  await page.keyboard.press("ArrowUp");
  const target = ["Morning market walk", "Kayaking on Lake Miren", "Old town temple visit", "Cooking class with a family"];
  // The second move shows at once, while the first save is still waiting for its answer.
  await expect.poll(() => stopNames(page)).toEqual(target);
  expect(mock.reorders.length).toBeLessThanOrEqual(1);
  // The second save goes out only after the first answer, and the first answer does not draw its older order.
  await expect.poll(() => mock.reorders.length, { timeout: 10_000 }).toBe(2);
  expect(mock.reorders).toEqual([[A, B, D, C], [A, D, B, C]]);
  expect(await stopNames(page)).toEqual(target);
  await expect.poll(() => mock.answered.length).toBe(2);
  expect(await stopNames(page)).toEqual(target);
  await expect(page.locator(`[data-handle="${D}"]`)).toBeFocused();
});

test("reorder by tap only at 390: the handle opens a move menu", async ({ page }, info) => {
  test.skip(!isMobile(info.project.name), "tap runs on the 390 phone project");
  const mock = await mockApis(page);
  await openPlanner(page, true);
  await page.locator(`[data-handle="${D}"]`).tap();
  const menu = page.getByTestId("reorder-menu");
  await expect(menu).toBeVisible();
  await expect(menu.getByTestId("move-down")).toBeDisabled();
  await menu.getByTestId("move-up").tap();
  await expect.poll(() => mock.reorders.length).toBe(1);
  expect(mock.reorders[0]).toEqual([A, B, D, C]);
  await expect(menu).toHaveCount(0);
  await page.locator(`[data-handle="${D}"]`).tap();
  await page.getByTestId("reorder-menu").getByTestId("move-first").tap();
  await expect.poll(() => mock.reorders.length).toBe(2);
  expect(mock.reorders[1]).toEqual([D, A, B, C]);
  await expect(page.getByTestId("reorder-announce")).toHaveText(/to position 1 of 4/);
});

test("a day with two stops moves as a whole (day handle, arrow key)", async ({ page }, info) => {
  const mock = await mockApis(page);
  await openPlanner(page, isMobile(info.project.name));
  await page.locator(`[data-handle="day:${B}"]`).focus();
  await page.keyboard.press("ArrowDown");
  await expect.poll(() => mock.reorders.length).toBe(1);
  expect(mock.reorders[0]).toEqual([A, D, B, C]);
});

test("reorder by a touch drag of the handle at 390", async ({ page }, info) => {
  test.skip(!isMobile(info.project.name), "touch drag runs on the 390 phone project");
  const mock = await mockApis(page);
  await openPlanner(page, true);
  const from = await page.locator(`[data-handle="${A}"]`).boundingBox();
  const to = await page.locator(`[data-handle="${D}"]`).boundingBox();
  expect(from && to).toBeTruthy();
  const x = from!.x + from!.width / 2;
  const y0 = from!.y + from!.height / 2;
  const y1 = to!.y + to!.height / 2 + 12;
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x, y: y0 }] });
  for (let i = 1; i <= 8; i++) {
    await cdp.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x, y: y0 + ((y1 - y0) * i) / 8 }] });
  }
  await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  await expect.poll(() => mock.reorders.length).toBe(1);
  expect(mock.reorders[0]).toEqual([B, C, D, A]);
  await expect.poll(() => stopNames(page)).toEqual([
    "Old town temple visit", "Cooking class with a family", "Kayaking on Lake Miren", "Morning market walk"]);
  // A drag does not open the move menu, and the next tap still does.
  await expect(page.getByTestId("reorder-menu")).toHaveCount(0);
  await page.locator(`[data-handle="${B}"]`).tap();
  await expect(page.getByTestId("reorder-menu")).toBeVisible();
});

test("stop photos and icons: a CIS photo where there is one, the icon tile otherwise", async ({ page }, info) => {
  await mockApis(page);
  await openPlanner(page, isMobile(info.project.name));
  const list = page.getByTestId("day-list");
  await expect(list.getByTestId("stop-photo").first()).toHaveAttribute("src", PHOTO);
  await expect(list.getByTestId("stop-photo-fallback").first()).toBeVisible();
  // The day of Pell (two stops) shows its photo once, and each of its stops an icon or a small photo.
  await expect(list.getByTestId("day")).toHaveCount(3);
});

test("map / list sync: row hover lights the marker, marker hover and click light the row", async ({ page }, info) => {
  test.skip(isMobile(info.project.name), "hover sync runs on the desktop project; the phone test is below");
  await mockApis(page);
  await openPlanner(page, false);
  const marker = page.locator(`[data-testid="trip-stop-marker"][data-component-ids~="${D}"]`);
  await expect(marker, "needs a build with the Mapbox token").toHaveCount(1, { timeout: 30_000 });
  await page.locator(`[data-stop="${D}"]`).hover();
  await expect(marker).toHaveAttribute("data-active", "true");
  await page.getByTestId("day-list").locator(`[data-stop="${A}"]`).hover();
  await expect(marker).toHaveAttribute("data-active", "false");
  const markerA = page.locator(`[data-testid="trip-stop-marker"][data-component-ids~="${A}"]`);
  await markerA.hover();
  await expect(page.locator(`[data-stop="${A}"]`)).toHaveAttribute("data-active", "true");
  await marker.click();
  await page.mouse.move(5, 300);
  await expect(page.locator(`[data-stop="${D}"]`)).toHaveAttribute("data-active", "true");
  await expect(marker).toHaveAccessibleName("Day 3: Kayaking on Lake Miren");
});

test("map / list sync on a phone: Show on map switches to the map with the stop lit", async ({ page }, info) => {
  test.skip(!isMobile(info.project.name), "the phone layout only");
  await mockApis(page);
  await openPlanner(page, true);
  await page.locator(`[data-stop="${D}"]`).getByTestId("show-on-map").click();
  const marker = page.locator(`[data-testid="trip-stop-marker"][data-component-ids~="${D}"]`);
  await expect(marker, "needs a build with the Mapbox token").toBeVisible({ timeout: 30_000 });
  await expect(marker).toHaveAttribute("data-active", "true");
});

test("the saved trip cannot be read: kit error state with a working retry", async ({ page }, info) => {
  const mock = await mockApis(page, { tripStatus: 503 });
  const res = await page.goto("/plan", { waitUntil: "load" });
  if (res?.status() === 202) await page.waitForLoadState("load");
  if (isMobile(info.project.name)) await page.getByRole("button", { name: /Your trip/ }).click();
  await expect(page.getByTestId("trip-load-error")).toBeVisible({ timeout: 20_000 });
  mock.tripStatus = 200;
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByTestId("day-list")).toBeVisible();
});

test("the share view shows the shared stops read-only, with print and calendar", async ({ page }) => {
  await mockApis(page);
  const frag = new URLSearchParams({ v: "1", s: [D, A, B, C].join("."), d: "n0,f" }).toString();
  const res = await page.goto(`/plan/shared#${frag}`, { waitUntil: "load" });
  if (res?.status() === 202) await page.waitForLoadState("load");
  const days = page.getByTestId("shared-days");
  await expect(days).toBeVisible({ timeout: 20_000 });
  await expect(days.locator("> li")).toHaveCount(3);
  await expect(days.locator("> li").first()).toContainText("Kayaking on Lake Miren");
  await expect(page.getByText("Extra night after Kayaking on Lake Miren")).toBeVisible();
  await expect(page.getByRole("button", { name: "Print or save as PDF" })).toBeEnabled();
  await expect(page.getByRole("button", { name: "Add to calendar (.ics)" })).toBeDisabled();
  await page.getByLabel("First day").fill("2027-03-14");
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Add to calendar (.ics)" }).click();
  expect((await download).suggestedFilename()).toBe("trip.ics");
});

test("the share view prints in the light theme for a viewer on the dark theme", async ({ page }) => {
  await mockApis(page);
  await page.emulateMedia({ colorScheme: "dark" });
  const frag = new URLSearchParams({ v: "1", s: [A, B].join(".") }).toString();
  const res = await page.goto(`/plan/shared#${frag}`, { waitUntil: "load" });
  if (res?.status() === 202) await page.waitForLoadState("load");
  await expect(page.getByTestId("shared-days")).toBeVisible({ timeout: 20_000 });
  const html = page.locator("html");
  await expect(html).toHaveAttribute("data-theme", "dark");
  const inkOf = () => page.getByTestId("shared-days").getByText("Morning market walk").evaluate((el) => getComputedStyle(el).color);
  await page.emulateMedia({ media: "print", colorScheme: "dark" });
  await expect(html).toHaveAttribute("data-theme", "light");
  // --aa-ink of the light theme (#1F2933): dark text on white paper.
  await expect.poll(inkOf).toBe("rgb(31, 41, 51)");
  await page.emulateMedia({ media: "screen", colorScheme: "dark" });
  await expect(html).toHaveAttribute("data-theme", "dark");
});

test("a share link without a trip shows the kit empty state", async ({ page }) => {
  await mockApis(page);
  const res = await page.goto("/plan/shared", { waitUntil: "load" });
  if (res?.status() === 202) await page.waitForLoadState("load");
  await expect(page.getByText("This link has no trip in it")).toBeVisible({ timeout: 20_000 });
});
