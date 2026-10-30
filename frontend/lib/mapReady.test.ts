// S260 UAT finding — guards for how the planner map (components/MapView.tsx) waits for Mapbox, a bug that tsc, eslint
// and the build cannot see:
//   /plan?country=Bhutan (the home page search, a destination tile) never flew the camera to the country, and a
//   country picked while tiles were still loading could be dropped too. The draw / fly effects returned early on
//   `!map.isStyleLoaded()`, which Mapbox answers false while ANY tile is loading (Style.loaded() walks every source
//   cache), and nothing re-ran them afterwards: the country effect only re-runs when the country changes.
// The fix gates them on a `mapReady` state set at the end of the "load" handler, listed in each effect's deps, so the
// effects run once more when the map is ready.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

/** MapView source without comments, so a comment that names isStyleLoaded never trips the guard. */
const src = readFileSync(new URL("../components/MapView.tsx", import.meta.url), "utf8")
  .replace(/\/\*[\s\S]*?\*\//g, "")
  .replace(/^\s*\/\/.*$/gm, "")
  .replace(/(^|[^:"'`])\/\/.*$/gm, "$1");

/** Each `useEffect(() => { … }, [deps])` as { body, deps } (brace-matched, so nested blocks are fine). */
function effects(code: string): { body: string; deps: string[] }[] {
  const out: { body: string; deps: string[] }[] = [];
  for (const m of code.matchAll(/useEffect\(\(\) => \{/g)) {
    let depth = 1;
    let i = (m.index ?? 0) + m[0].length;
    while (depth > 0 && i < code.length) {
      if (code[i] === "{") depth++;
      else if (code[i] === "}") depth--;
      i++;
    }
    const body = code.slice((m.index ?? 0) + m[0].length, i - 1);
    const deps = /^\s*,\s*\[([^\]]*)\]/.exec(code.slice(i));
    out.push({ body, deps: deps ? deps[1].split(",").map((d) => d.trim()).filter(Boolean) : [] });
  }
  return out;
}

test("no map effect gates on isStyleLoaded() (false while any tile loads, and never re-run)", () => {
  assert.doesNotMatch(src, /isStyleLoaded\(/);
});

test("the map is marked ready at the end of its load handler, and not ready once removed", () => {
  const load = src.indexOf('map.on("load"');
  assert.ok(load > 0, 'map.on("load") not found');
  const ready = src.indexOf("setMapReady(true)", load);
  const remove = src.indexOf("map.remove()", load);
  assert.ok(ready > load && ready < remove, "setMapReady(true) must sit inside the load handler");
  assert.ok(src.indexOf("setMapReady(false)", remove) > remove, "the cleanup must reset mapReady");
});

test("every effect that waits for the map re-runs when it becomes ready", () => {
  const waiting = effects(src).filter((e) => /\bmapReady\b/.test(e.body));
  assert.ok(waiting.length >= 5, `expected the browse, highlight, country, search and trip effects, found ${waiting.length}`);
  for (const e of waiting) assert.ok(e.deps.includes("mapReady"), `deps [${e.deps.join(", ")}] miss mapReady`);
});

test("the country fly effect is one of them (?country= on first load flies the camera)", () => {
  const fly = effects(src).find((e) => e.body.includes("fetchByCountry("));
  assert.ok(fly, "the country effect (fetchByCountry) not found");
  assert.match(fly.body, /if \(!map \|\| !mapReady\) return;/);
  assert.ok(fly.deps.includes("filters.country") && fly.deps.includes("mapReady"));
  assert.match(fly.body, /map\.fitBounds\(/);
});
