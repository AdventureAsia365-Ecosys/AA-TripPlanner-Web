#!/usr/bin/env node
// Map engine core (AA-948, the root ADR 0016 pattern). Canonical: AA-TripPlanner-Web frontend/lib/map-engine. Edit only there; copies are checked byte for byte (map-engine.lock.json).
//
// The guards of the shared map engine, dependency-free (Node built-ins only), shipped with the core like the UI kit's
// check-kit-copy.mjs. Every mode works on the folder this script sits in.
//
//   node check-map-engine.mjs                 integrity: every file has the sha256 of MANIFEST.json, none is missing or
//                                             extra, and the core imports nothing outside itself (only ./siblings,
//                                             a type import of mapbox-gl, a dynamic import("mapbox-gl") in engine.ts,
//                                             and react in react.ts; never next, "@/" or "../")
//   node check-map-engine.mjs --write         canonical only: rewrite MANIFEST.json after an edit of the core
//   node check-map-engine.mjs --sync <repo> --dest <dir> --lock <file> --ref <sha>
//                                             copy the core into another repo (<repo>/<dir>, replaced) and write its lock
//                                             (<repo>/<file>: canonical, ref, dest, manifest_sha256). Commit it there.
//   node check-map-engine.mjs --imports <src-root> --engine <dir under src-root>
//                                             the consumer import guard (CI): no source file under <src-root> imports the
//                                             TripPlanner app (its package or repo name, or a relative path that leaves
//                                             the consumer's own git repository), and mapbox-gl is imported only inside
//                                             the engine copy (its stylesheet mapbox-gl/dist/mapbox-gl.css may be
//                                             imported anywhere)
//
// Exit codes: 0 fine, 1 a check failed, 2 usage error. Fix for a failed copy check: edit the canonical folder in
// AA-TripPlanner-Web, run --write there, merge it, then --sync into the consumer; never edit a copy.
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

export const CANONICAL = { repo: "AdventureAsia365-Ecosys/AA-TripPlanner-Web", path: "frontend/lib/map-engine" };
export const MANIFEST = "MANIFEST.json";
const HERE = dirname(fileURLToPath(import.meta.url));
const FIX = "edit AA-TripPlanner-Web frontend/lib/map-engine, run --write there, then --sync; never edit a copy";
// Package or repo names of the TripPlanner app: a consumer reaches its code only through the engine copy.
const TP_NAMES = [/(^|\/)AA-TripPlanner-Web(\/|$)/i, /^aa-tripplanner-frontend(\/|$)/i];
const SOURCE_EXT = /\.(ts|tsx|mts|cts|js|jsx|mjs|cjs)$/;
const SKIP_DIRS = new Set(["node_modules", ".next", "out", "build", "coverage", ".git"]);

const sha256 = (buf) => createHash("sha256").update(buf).digest("hex");

/** The files of the core (sorted), MANIFEST.json excluded. */
export function coreFiles(dir) {
  return readdirSync(dir)
    .filter((f) => f !== MANIFEST && statSync(join(dir, f)).isFile())
    .sort();
}

/** The manifest text of a folder: canonical source and the sha256 of each file, stable JSON. */
export function manifestText(dir) {
  const files = {};
  for (const f of coreFiles(dir)) files[f] = sha256(readFileSync(join(dir, f)));
  return JSON.stringify({ canonical: CANONICAL, files }, null, 2) + "\n";
}

/** Every import specifier of a source text (static, re-export, side-effect, dynamic, require), with its kind. */
export function importsOf(text) {
  const out = [];
  const code = text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'`])\/\/.*$/gm, "$1");
  const patterns = [
    [/\bimport\s+type\s+[^'";]*?\bfrom\s*['"]([^'"]+)['"]/g, "type"],
    [/\b(?:import|export)\s+(?!type\s)[^'";]*?\bfrom\s*['"]([^'"]+)['"]/g, "static"],
    [/\bimport\s*['"]([^'"]+)['"]/g, "side-effect"],
    [/\bimport\s*\(\s*['"]([^'"]+)['"]\s*\)/g, "dynamic"],
    [/\brequire\s*\(\s*['"]([^'"]+)['"]\s*\)/g, "require"],
  ];
  for (const [re, kind] of patterns) for (const m of code.matchAll(re)) out.push({ spec: m[1], kind });
  return out;
}

/** Whether the core file `f` may import `spec` (of `kind`): ./siblings, mapbox-gl types (and its dynamic import in
 *  engine.ts), react in react.ts. */
function allowedCoreImport(f, spec, kind) {
  if (spec.startsWith("./")) return !spec.slice(2).includes("/");
  if (spec === "mapbox-gl") return kind === "type" || (kind === "dynamic" && f === "engine.ts");
  return spec === "react" && f === "react.ts";
}

/** Problems of the core's own imports (the closed set). */
export function coreImportProblems(dir) {
  const problems = [];
  for (const f of coreFiles(dir).filter((x) => /\.(ts|tsx)$/.test(x))) {
    for (const { spec, kind } of importsOf(readFileSync(join(dir, f), "utf8"))) {
      if (allowedCoreImport(f, spec, kind)) continue;
      problems.push(`${f}: imports "${spec}" (${kind}); the core imports only ./siblings, mapbox-gl types and react (react.ts)`);
    }
  }
  return problems;
}

/** A folder's MANIFEST.json as { manifest } or { problem }. */
function readManifest(dir) {
  const path = join(dir, MANIFEST);
  if (!existsSync(path)) return { problem: `${MANIFEST} is missing; ${FIX}` };
  try {
    return { manifest: JSON.parse(readFileSync(path, "utf8")) };
  } catch (err) {
    return { problem: `${MANIFEST} is not JSON (${err.message}); ${FIX}` };
  }
}

/** Missing, extra and changed files of a folder against the manifest's `files`. */
function fileProblems(dir, want) {
  const have = coreFiles(dir);
  const missing = Object.keys(want).filter((f) => !have.includes(f)).map((f) => `${f} is missing; ${FIX}`);
  const wrong = have.map((f) => {
    if (!(f in want)) return `${f} is not in ${MANIFEST}; ${FIX}`;
    return sha256(readFileSync(join(dir, f))) === want[f] ? null : `${f} differs from ${MANIFEST}; ${FIX}`;
  });
  return [...missing, ...wrong.filter(Boolean)];
}

/** Integrity of a folder against its MANIFEST.json. */
export function integrityProblems(dir) {
  const { manifest, problem } = readManifest(dir);
  if (problem) return [problem];
  const source = JSON.stringify(manifest?.canonical) === JSON.stringify(CANONICAL) ? [] : [`${MANIFEST} names another canonical source; ${FIX}`];
  return [...source, ...fileProblems(dir, manifest?.files ?? {}), ...coreImportProblems(dir)];
}

function* sourceFiles(root) {
  for (const name of readdirSync(root)) {
    const p = join(root, name);
    const st = statSync(p);
    if (st.isDirectory()) {
      if (!SKIP_DIRS.has(name)) yield* sourceFiles(p);
    } else if (SOURCE_EXT.test(name) && !name.endsWith(".d.ts")) {
      yield p;
    }
  }
}

/** The git repository a folder belongs to (the nearest parent with a .git entry), or the folder itself. */
export function repoRootOf(start) {
  for (let dir = resolve(start); ; dir = dirname(dir)) {
    if (existsSync(join(dir, ".git"))) return dir;
    if (dirname(dir) === dir) return resolve(start);
  }
}

/** The guard's verdict on one import of a consumer file (null = fine). */
function specProblem(spec, { file, repo, inEngine, engineDir }) {
  if (TP_NAMES.some((re) => re.test(spec))) {
    return `imports "${spec}": TripPlanner code is reached only through the map engine copy (${engineDir})`;
  }
  if (spec.startsWith(".")) {
    const target = resolve(dirname(file), spec);
    const inside = target === repo || target.startsWith(repo + sep);
    return inside ? null : `imports "${spec}", outside this repository: use the map engine copy (${engineDir})`;
  }
  const mapbox = spec === "mapbox-gl" || spec.startsWith("mapbox-gl/");
  if (mapbox && !inEngine && spec !== "mapbox-gl/dist/mapbox-gl.css") {
    return `imports "${spec}": draw maps through the map engine (${engineDir}), not mapbox-gl directly`;
  }
  return null;
}

/** The consumer import guard: problems under `srcRoot`, the engine copy being `engineDir` (relative to it). A relative
 *  import may reach the rest of the consumer's own repository (its e2e helpers), never a folder outside it. */
export function importGuardProblems(srcRoot, engineDir) {
  const root = resolve(srcRoot);
  const repo = repoRootOf(root);
  const engine = resolve(root, engineDir);
  const problems = [];
  for (const file of sourceFiles(root)) {
    const where = { file, repo, inEngine: file.startsWith(engine + sep), engineDir };
    for (const { spec } of importsOf(readFileSync(file, "utf8"))) {
      const problem = specProblem(spec, where);
      if (problem) problems.push(`${relative(root, file)}: ${problem}`);
    }
  }
  return problems;
}

/** Copy the core into a consumer folder (replaced) and write its lock; returns the lock object. */
export function syncInto(repoRoot, dest, lockFile, ref) {
  if (!/^[0-9a-f]{40}$/.test(ref ?? "")) throw new Error("--ref must be the full commit sha of AA-TripPlanner-Web the core is copied from");
  const problems = integrityProblems(HERE);
  if (problems.length) throw new Error(`the canonical core is not clean: ${problems.join("; ")}`);
  const target = resolve(repoRoot, dest);
  rmSync(target, { recursive: true, force: true });
  mkdirSync(target, { recursive: true });
  for (const f of [...coreFiles(HERE), MANIFEST]) writeFileSync(join(target, f), readFileSync(join(HERE, f)));
  const lock = {
    adr: "root ADR 0016 (the guarded byte copy pattern)",
    canonical: CANONICAL,
    ref,
    dest,
    manifest_sha256: sha256(readFileSync(join(target, MANIFEST))),
  };
  writeFileSync(resolve(repoRoot, lockFile), JSON.stringify(lock, null, 2) + "\n");
  return lock;
}

function arg(argv, name) {
  const i = argv.indexOf(name);
  return i >= 0 ? argv[i + 1] : undefined;
}

function report(problems, okText) {
  for (const p of problems) console.error(`FAIL  ${p}`);
  if (!problems.length) console.log(`ok    ${okText}`);
  return problems.length ? 1 : 0;
}

function runWrite() {
  const problems = coreImportProblems(HERE);
  if (problems.length) return report(problems, "");
  writeFileSync(join(HERE, MANIFEST), manifestText(HERE));
  console.log(`wrote ${MANIFEST} (${coreFiles(HERE).length} files)`);
  return 0;
}

function runSync(argv) {
  const [repo, dest, lockFile] = [arg(argv, "--sync"), arg(argv, "--dest"), arg(argv, "--lock")];
  if (!repo || !dest || !lockFile) {
    console.error("usage: --sync <repo> --dest <dir> --lock <file> --ref <sha>");
    return 2;
  }
  try {
    const lock = syncInto(repo, dest, lockFile, arg(argv, "--ref"));
    console.log(`synced ${coreFiles(HERE).length} files into ${join(repo, dest)} (ref ${lock.ref.slice(0, 12)})`);
    return 0;
  } catch (err) {
    console.error(`FAIL  ${err.message}`);
    return 1;
  }
}

function runImports(argv) {
  const [src, engine] = [arg(argv, "--imports"), arg(argv, "--engine")];
  if (!src || !engine) {
    console.error("usage: --imports <src-root> --engine <dir under src-root>");
    return 2;
  }
  return report(importGuardProblems(src, engine), `no TripPlanner import and no direct mapbox-gl import outside ${engine}`);
}

function main(argv) {
  if (argv.includes("--write")) return runWrite();
  if (argv.includes("--sync")) return runSync(argv);
  if (argv.includes("--imports")) return runImports(argv);
  if (argv.length) {
    console.error(`unknown arguments: ${argv.join(" ")}`);
    return 2;
  }
  return report(integrityProblems(HERE), `map engine core intact (${coreFiles(HERE).length} files match ${MANIFEST})`);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exit(main(process.argv.slice(2)));
}
