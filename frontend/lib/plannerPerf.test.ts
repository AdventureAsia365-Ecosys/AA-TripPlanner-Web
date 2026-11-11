// AA-1041 (first-load performance, S277 Lighthouse check) — guards for how the planner keeps its heavy parts off the
// first load and its hover sync off the whole tree. tsc, eslint and the build cannot see a regression here: a static
// import of the map component puts Mapbox (about 1.4 MB of script) back into the planner's first chunk, and a sync
// field back in the trip context re-renders every planner component on each hover. Run: npm test.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (p: string) => readFileSync(new URL(p, import.meta.url), "utf8");
/** Source without comments, so a comment naming a pattern never trips a guard. */
const code = (p: string) =>
  read(p)
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "");

test("the planner loads the map and the chat lazily, off its first load", () => {
  const src = code("../app/plan/PlannerApp.tsx");
  assert.doesNotMatch(src, /^import\s+MapView\b/m);
  assert.doesNotMatch(src, /^import\s+PlanChat\b/m);
  assert.match(src, /dynamic\(\(\) => import\("@\/components\/MapView"\)/);
  assert.match(src, /dynamic\(\(\) => import\("@\/components\/PlanChat"\)/);
  // The map is asked for only after an idle moment, and the chat only once opened.
  assert.match(src, /whenIdle\(\(\) => setMapWanted\(true\)\)/);
  assert.match(src, /mapWanted \? <MapView \/> : <MapPending \/>/);
  assert.equal(src.match(/chatOpened && <PlanChat \/>/g)?.length, 2, "desktop tab and phone view");
});

test("mapbox-gl is imported by the map component only", () => {
  for (const f of [
    "../app/plan/PlannerApp.tsx",
    "../components/TripPanel.tsx",
    "../components/DayList.tsx",
    "../components/FilterChips.tsx",
    "../components/PlanChat.tsx",
    "../lib/useTrip.tsx",
    "../lib/api.ts",
    "../lib/mapbox.ts",
  ]) {
    assert.doesNotMatch(code(f), /from "mapbox-gl"|import\("mapbox-gl"\)/, f);
  }
  assert.match(code("../components/MapView.tsx"), /from "mapbox-gl"/);
});

test("the hover / selection sync is its own context, read by the map and the day list only", () => {
  const trip = code("./useTrip.tsx");
  const state = trip.slice(trip.indexOf("interface TripState"), trip.indexOf("\n}\n", trip.indexOf("interface TripState")));
  for (const f of ["activeStopId", "selectedStop", "hoverStop", "selectStop"]) {
    assert.doesNotMatch(state, new RegExp(`\\b${f}\\b`), `${f} must not be in TripState`);
  }
  assert.match(trip, /const value = useMemo<TripState>\(/, "the trip context value is memoised");
  assert.match(trip, /<StopSyncProvider>\{children\}<\/StopSyncProvider>/);
  for (const f of ["../components/MapView.tsx", "../components/DayList.tsx"]) {
    assert.match(code(f), /useStopSync\(\)/, f);
  }
});

test("stop photos are asked for when the main thread is idle", () => {
  const trip = code("./useTrip.tsx");
  assert.match(trip, /whenIdle\(\(\) => \{\s*void api\.fetchStops\(/);
});
