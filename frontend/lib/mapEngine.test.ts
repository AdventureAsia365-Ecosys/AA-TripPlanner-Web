// AA-948 — the shared map engine (lib/map-engine: the canonical copy; other apps hold byte copies checked against its
// MANIFEST.json). Geometry, camera framing under the reduce-motion gate, markers, layers and teardown run against a
// fake mapbox-gl module; the guard script is run on temp trees. Run: npm test (node --test with type stripping).
import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { cpSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const CORE = new URL("./map-engine/", import.meta.url);
const CHECK = new URL("./map-engine/check-map-engine.mjs", import.meta.url);

/** The core, loaded from a temp copy whose relative imports carry the .ts extension (type stripping needs it; the
 *  core itself stays extensionless so every consumer's bundler resolves it). */
async function loadCore(): Promise<any> {
  const dir = mkdtempSync(join(tmpdir(), "tp-map-engine-"));
  for (const f of readdirSync(CORE)) {
    if (!f.endsWith(".ts")) continue;
    const text = readFileSync(new URL(f, CORE), "utf8").replace(/from "\.\/([a-z-]+)"/g, 'from "./$1.ts"');
    writeFileSync(join(dir, f), text);
  }
  try {
    return await import(pathToFileURL(join(dir, "index.ts")).href);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}
const core = await loadCore();

// --- a fake mapbox-gl module: records what the engine asks of it ---
type Handler = (...a: unknown[]) => void;
class FakeMap {
  static last: FakeMap | null = null;
  opts: Record<string, unknown>;
  handlers: { ev: string; layer?: string; fn: Handler }[] = [];
  sources = new Map<string, { data?: unknown; setData: (d: unknown) => void }>();
  layers: Record<string, unknown>[] = [];
  fits: unknown[][] = [];
  eases: Record<string, unknown>[] = [];
  resized = 0;
  removed = 0;
  canvas = { style: { cursor: "" } };
  constructor(opts: Record<string, unknown>) {
    this.opts = opts;
    FakeMap.last = this;
  }
  on(ev: string, a: unknown, b?: unknown) {
    if (typeof a === "function") this.handlers.push({ ev, fn: a as Handler });
    else this.handlers.push({ ev, layer: a as string, fn: b as Handler });
  }
  fire(ev: string, layer?: string, arg?: unknown) {
    for (const h of this.handlers) if (h.ev === ev && h.layer === layer) h.fn(arg);
  }
  resize() {
    this.resized++;
  }
  remove() {
    this.removed++;
  }
  getSource(id: string) {
    return this.sources.get(id);
  }
  addSource(id: string, spec: { data?: unknown }) {
    const src = { data: spec.data, setData: (d: unknown) => (src.data = d) };
    this.sources.set(id, src);
  }
  addLayer(spec: Record<string, unknown>) {
    this.layers.push(spec);
  }
  fitBounds(...a: unknown[]) {
    this.fits.push(a);
  }
  easeTo(o: Record<string, unknown>) {
    this.eases.push(o);
  }
  getCanvas() {
    return this.canvas;
  }
}
const markersAlive = new Set<FakeMarker>();
class FakeMarker {
  element: unknown;
  at: unknown = null;
  constructor(o: { element: unknown }) {
    this.element = o.element;
  }
  setLngLat(c: unknown) {
    this.at = c;
    return this;
  }
  addTo() {
    markersAlive.add(this);
    return this;
  }
  remove() {
    markersAlive.delete(this);
  }
}
let popupsOpen = 0;
class FakePopup {
  setLngLat() {
    return this;
  }
  setDOMContent() {
    return this;
  }
  addTo() {
    popupsOpen++;
    return this;
  }
  remove() {
    popupsOpen--;
  }
}
function fakeGl() {
  return { accessToken: "", Map: FakeMap, Marker: FakeMarker, Popup: FakePopup };
}
const container = {} as unknown as HTMLElement;
const TOKEN = "pk.test-token-not-real";

function setMotion(value: string | null) {
  (globalThis as { document?: unknown }).document =
    value === null ? undefined : { documentElement: { getAttribute: (n: string) => (n === "data-motion" ? value : null) } };
}

// --- geometry ---

test("isLngLat / lngLat accept only finite, in-range pairs (a day without a valid point has no pin)", () => {
  assert.equal(core.isLngLat([105.8, 21.0]), true);
  assert.equal(core.isLngLat([181, 0]), false);
  assert.equal(core.isLngLat([0, -91]), false);
  assert.equal(core.isLngLat([Number.NaN, 0]), false);
  assert.equal(core.isLngLat(["1", 2]), false);
  assert.deepEqual(core.lngLat(100.5, 13.7), [100.5, 13.7]);
  assert.equal(core.lngLat(null, 13.7), null);
  assert.equal(core.lngLat(undefined, undefined), null);
});

test("greatCircleSegment starts and ends at the two points; a zero-length leg is the two points", () => {
  const seg = core.greatCircleSegment([100, 10], [110, 20], 10);
  assert.equal(seg.length, 11);
  assert.ok(Math.abs(seg[0][0] - 100) < 1e-9 && Math.abs(seg[0][1] - 10) < 1e-9);
  assert.ok(Math.abs(seg[10][0] - 110) < 1e-9 && Math.abs(seg[10][1] - 20) < 1e-9);
  assert.deepEqual(core.greatCircleSegment([1, 1], [1, 1]), [[1, 1], [1, 1]]);
});

test("boundsOf frames points and boxes, ignores broken points, null when nothing is valid", () => {
  assert.deepEqual(core.boundsOf([[100, 10], [104, 12, 106, 14], [999, 0]]), [100, 10, 106, 14]);
  assert.equal(core.boundsOf([]), null);
  assert.equal(core.boundsOf([[Number.NaN, 1]]), null);
});

test("routeStops: one stop per place visit; a day without a point is skipped", () => {
  const stops = core.routeStops([
    { label: "1", coord: [100, 10] },
    { label: "2", coord: [100, 10] }, // same place: day 1's stop
    { label: "3", coord: null }, // no location: no pin
    { label: "4", coord: [101, 11] },
  ]);
  assert.deepEqual(stops, [
    { label: "1", coord: [100, 10] },
    { label: "4", coord: [101, 11] },
  ]);
});

test("routeCollection: a line once there are two stops, and one labelled point per stop", () => {
  const one = core.routeCollection([{ label: "1", coord: [100, 10] }]);
  assert.deepEqual(one.features.map((f: { geometry: { type: string } }) => f.geometry.type), ["Point"]);
  const two = core.routeCollection(
    [
      { label: "1", coord: [100, 10] },
      { label: "2", coord: [102, 12] },
    ],
    8,
  );
  assert.deepEqual(two.features.map((f: { geometry: { type: string } }) => f.geometry.type), ["LineString", "Point", "Point"]);
  assert.equal(two.features[0].geometry.coordinates.length, 9);
  assert.equal(two.features[2].properties.label, "2");
  assert.deepEqual(core.emptyCollection(), { type: "FeatureCollection", features: [] });
});

test("motion: camera moves are instant only under <html data-motion=reduce>", () => {
  setMotion(null);
  assert.equal(core.camMs(600), 600);
  setMotion("reduce");
  assert.equal(core.camMs(600), 0);
  assert.deepEqual(core.camReduced(), { duration: 0 });
  setMotion("ok");
  assert.deepEqual(core.camReduced(), {});
  setMotion(null);
});

// --- engine ---

test("no token: the engine refuses (the app shows its empty state instead)", () => {
  assert.equal(core.hasToken(""), false);
  assert.equal(core.hasToken("   "), false);
  assert.equal(core.hasToken(null), false);
  assert.throws(() => core.createMapEngine(fakeGl(), { container, accessToken: "" }), /no access token/);
});

test("createMapEngine: token, default style and camera, error events reported, ready after load", () => {
  const gl = fakeGl();
  const errors: string[] = [];
  const engine = core.createMapEngine(gl, { container, accessToken: TOKEN, onError: (m: string) => errors.push(m) });
  const map = FakeMap.last!;
  assert.equal(gl.accessToken, TOKEN);
  assert.equal(map.opts.style, "mapbox://styles/mapbox/outdoors-v12");
  assert.deepEqual(map.opts.center, [105, 15]);
  assert.equal(map.opts.zoom, 3);
  map.fire("error", undefined, { error: { message: "bad token" } });
  assert.deepEqual(errors, ["bad token"]);
  let ready = 0;
  engine.onReady(() => ready++);
  assert.equal(ready, 0);
  map.fire("load");
  assert.equal(ready, 1);
  assert.ok(map.resized >= 1, "the canvas is resized when the style loads");
  engine.onReady(() => ready++); // already loaded: at once
  assert.equal(ready, 2);
});

test("setData writes an existing source only; fitTo frames and follows the motion gate", () => {
  const engine = core.createMapEngine(fakeGl(), { container, accessToken: TOKEN });
  const map = FakeMap.last!;
  assert.equal(engine.setData("nope", core.emptyCollection()), false);
  map.addSource("s", { data: null });
  const fc = core.routeCollection([{ label: "1", coord: [100, 10] }]);
  assert.equal(engine.setData("s", fc), true);
  assert.equal(map.sources.get("s")!.data, fc);

  assert.equal(engine.fitTo([]), false);
  assert.equal(map.fits.length, 0);
  setMotion("reduce");
  assert.equal(engine.fitTo([[100, 10], [102, 12]], { padding: 40, maxZoom: 8, durationMs: 700 }), true);
  assert.deepEqual(map.fits[0], [[[100, 10], [102, 12]], { padding: 40, maxZoom: 8, duration: 0 }]);
  setMotion(null);
  engine.fitTo([[100, 10]]);
  assert.deepEqual(map.fits[1][1], { padding: 80, maxZoom: 9, duration: 600 });
  engine.easeTo([100, 10], 8);
  assert.deepEqual(map.eases[0], { center: [100, 10], zoom: 8 });
});

test("setMarkers keeps, moves, replaces and removes markers by key; destroy removes everything once", () => {
  markersAlive.clear();
  const engine = core.createMapEngine(fakeGl(), { container, accessToken: TOKEN });
  const map = FakeMap.last!;
  const a = { id: "a" };
  const b = { id: "b" };
  engine.setMarkers([
    { key: "1", coord: [100, 10], element: a },
    { key: "2", coord: [101, 11], element: b },
    { key: "bad", coord: [500, 0], element: {} }, // not a valid point: no marker
  ]);
  assert.equal(markersAlive.size, 2);
  engine.setMarkers([{ key: "1", coord: [100.5, 10], element: a }]);
  assert.equal(markersAlive.size, 1);
  const [kept] = [...markersAlive];
  assert.equal(kept.element, a);
  assert.deepEqual(kept.at, [100.5, 10]);
  engine.setMarkers([{ key: "1", coord: [100.5, 10], element: { id: "new" } }]); // a new element replaces the marker
  assert.equal(markersAlive.size, 1);
  assert.deepEqual(([...markersAlive][0].element as { id: string }).id, "new");

  engine.showPopup([100, 10], {});
  engine.showPopup([101, 10], {}); // replaces the open one
  assert.equal(popupsOpen, 1);
  engine.destroy();
  engine.destroy();
  assert.equal(markersAlive.size, 0);
  assert.equal(popupsOpen, 0);
  assert.equal(map.removed, 1);
});

test("onLayerClick passes the feature's properties and point; the cursor shows a pointer over the layer", () => {
  const engine = core.createMapEngine(fakeGl(), { container, accessToken: TOKEN });
  const map = FakeMap.last!;
  const got: unknown[] = [];
  engine.onLayerClick(["pins"], (props: Record<string, unknown>, coord: unknown) => got.push([props.id, coord]));
  map.fire("click", "pins", { features: [{ properties: { id: "x" }, geometry: { type: "Point", coordinates: [100, 10] } }] });
  map.fire("click", "pins", { features: [] });
  assert.deepEqual(got, [["x", [100, 10]], [undefined, null]]);
  map.fire("mouseenter", "pins");
  assert.equal(map.canvas.style.cursor, "pointer");
  map.fire("mouseleave", "pins");
  assert.equal(map.canvas.style.cursor, "");
});

test("addRouteLayers: one source, line + stops + labels with the caller's colours (no app colour inside)", () => {
  core.createMapEngine(fakeGl(), { container, accessToken: TOKEN });
  const map = FakeMap.last!;
  core.addRouteLayers(map, "route", "route-preview", { lineColor: "#123456", stopColor: "#654321", labelColor: "#fefefe" });
  assert.ok(map.sources.has("route"));
  assert.deepEqual(
    map.layers.map((l) => l.id),
    ["route-preview-line", "route-preview-stops", "route-preview-labels"],
  );
  const [line, stops, labels] = map.layers as { paint: Record<string, unknown> }[];
  assert.equal(line.paint["line-color"], "#123456");
  assert.deepEqual(line.paint["line-dasharray"], [1.5, 1.5]);
  assert.equal(stops.paint["circle-color"], "#654321");
  assert.equal(labels.paint["text-color"], "#fefefe");
  core.addRouteLayers(map, "route2", "solid", { lineColor: "#000001", stopColor: "#000002", labelColor: "#000003", dashed: false });
  assert.equal((map.layers[3] as { paint: Record<string, unknown> }).paint["line-dasharray"], undefined);
});

// --- the core stays framework-free and checked ---

test("the core imports no React outside react.ts, no Next, no app alias and no parent folder", () => {
  for (const f of readdirSync(CORE).filter((x) => x.endsWith(".ts"))) {
    const text = readFileSync(new URL(f, CORE), "utf8");
    assert.doesNotMatch(text, /from "next/, f);
    assert.doesNotMatch(text, /from "@\//, f);
    assert.doesNotMatch(text, /from "\.\.\//, f);
    if (f !== "react.ts") assert.doesNotMatch(text, /from "react"/, f);
  }
});

test("MapView draws through the engine: it creates no Mapbox map and keeps no copy of the engine's helpers", () => {
  const src = readFileSync(new URL("../components/MapView.tsx", import.meta.url), "utf8");
  assert.match(src, /from "@\/lib\/map-engine"/);
  assert.match(src, /createMapEngine\(mapboxgl,/);
  assert.doesNotMatch(src, /new mapboxgl\.(Map|Marker|LngLatBounds)\(/);
  assert.doesNotMatch(src, /function (greatCircleSegment|motionReduced|camMs|camReduced)\(/);
});

function runCheck(args: string[], cwd?: string): { code: number; out: string } {
  try {
    const out = execFileSync(process.execPath, [CHECK.pathname, ...args], { cwd, encoding: "utf8", stdio: "pipe" });
    return { code: 0, out };
  } catch (err) {
    const e = err as { status: number; stdout: string; stderr: string };
    return { code: e.status, out: `${e.stdout}${e.stderr}` };
  }
}

test("check-map-engine.mjs: the canonical core matches its MANIFEST.json", () => {
  const r = runCheck([]);
  assert.equal(r.code, 0, r.out);
});

test("check-map-engine.mjs: a synced copy passes; an edited or extra file fails", () => {
  const repo = mkdtempSync(join(tmpdir(), "tp-map-sync-"));
  try {
    const r = runCheck(["--sync", repo, "--dest", "web/lib/map-engine", "--lock", "map-engine.lock.json", "--ref", "a".repeat(40)]);
    assert.equal(r.code, 0, r.out);
    const lock = JSON.parse(readFileSync(join(repo, "map-engine.lock.json"), "utf8"));
    assert.equal(lock.canonical.repo, "AdventureAsia365-Ecosys/AA-TripPlanner-Web");
    assert.equal(lock.dest, "web/lib/map-engine");
    const copyCheck = join(repo, "web/lib/map-engine/check-map-engine.mjs");
    const ok = execFileSync(process.execPath, [copyCheck], { encoding: "utf8" });
    assert.match(ok, /intact/);
    writeFileSync(join(repo, "web/lib/map-engine/geo.ts"), "// edited in the copy\n", { flag: "a" });
    writeFileSync(join(repo, "web/lib/map-engine/extra.ts"), "export {};\n");
    let failed = "";
    try {
      execFileSync(process.execPath, [copyCheck], { encoding: "utf8", stdio: "pipe" });
    } catch (err) {
      failed = String((err as { stderr: string }).stderr);
    }
    assert.match(failed, /geo\.ts differs/);
    assert.match(failed, /extra\.ts is not in MANIFEST\.json/);
    assert.equal(runCheck(["--sync", repo, "--dest", "x", "--lock", "l.json", "--ref", "main"]).code, 1);
  } finally {
    rmSync(repo, { recursive: true, force: true });
  }
});

test("check-map-engine.mjs --imports: TripPlanner app imports and direct mapbox-gl imports fail; the copy passes", () => {
  const root = mkdtempSync(join(tmpdir(), "tp-map-guard-"));
  try {
    const web = join(root, "web");
    cpSync(new URL("./map-engine/", import.meta.url), join(web, "lib/map-engine"), { recursive: true });
    mkdirSync(join(web, "components"), { recursive: true });
    writeFileSync(join(web, "components/Good.tsx"), 'import { useMapEngine } from "@/lib/map-engine/react";\nimport "mapbox-gl/dist/mapbox-gl.css";\n');
    assert.equal(runCheck(["--imports", web, "--engine", "lib/map-engine"]).code, 0);
    writeFileSync(join(web, "components/Bad1.tsx"), 'import MapView from "../../AA-TripPlanner-Web/frontend/components/MapView";\n');
    writeFileSync(join(web, "components/Bad4.ts"), 'import { x } from "../../../sibling-repo/lib/x";\n');
    mkdirSync(join(root, "e2e"), { recursive: true });
    writeFileSync(join(root, ".git"), "gitdir: elsewhere\n"); // the consumer repo root: its own e2e folder is fine
    writeFileSync(join(web, "components/OwnE2e.ts"), 'import { pages } from "../../e2e/pages";\n');
    writeFileSync(join(web, "components/Bad2.ts"), 'export const m = () => import("mapbox-gl");\n');
    writeFileSync(join(web, "components/Bad3.ts"), 'import { x } from "aa-tripplanner-frontend/lib/lines";\n');
    const r = runCheck(["--imports", web, "--engine", "lib/map-engine"]);
    assert.equal(r.code, 1);
    assert.match(r.out, /Bad1\.tsx: imports "\.\.\/\.\.\/AA-TripPlanner-Web/);
    assert.match(r.out, /Bad2\.ts: imports "mapbox-gl"/);
    assert.match(r.out, /Bad3\.ts: imports "aa-tripplanner-frontend/);
    assert.match(r.out, /Bad4\.ts: imports "\.\.\/\.\.\/\.\.\/sibling-repo\/lib\/x", outside this repository/);
    assert.doesNotMatch(r.out, /OwnE2e/);
    assert.doesNotMatch(r.out, /Good\.tsx/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
