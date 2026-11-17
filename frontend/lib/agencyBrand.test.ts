// AA-1115 (root ADR 0019 d10, S282 audit 6.3) — guard: no page an agency's traveller can reach renders "Adventure Asia".
//
// Every page under app/[tenant]/ (an agency's /{slug} site; its own domain is rewritten onto these paths by proxy.ts) is
// the agency's. The guard has three parts:
//   1. the source: every file such a page reaches through its imports (and the root layout, which wraps every page)
//      may spell the brand ("Adventure Asia", "adventure.asia") only in lib/brand.ts, and may import Adventure Asia's
//      own words (AA_* of lib/brand.ts) or ask brandCopy(null) only where an allow-list below says why;
//   2. the words: brandCopy(agency) and tenantMetadata(site) never return the brand (nor "AA" as a word), and the
//      metadata replaces every brand-bearing key of the root layout's defaults;
//   3. the wiring: the /{slug} layout sets that metadata and the agency's language, and the planner passes the agency
//      to the brand context.
// The BO-8 badge (root ADR 0019 b8, "Powered by Adventure Asia" when the agency switched tp.branding on) is the one
// exception, and it comes from brandCopy(...).poweredBy(true) only. Run: npm test.
import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { AA_SITE_METADATA, brandCopy, tenantMetadata, type NarrationSource } from "./brand.ts";
import { tenantLang, tenantLangScript } from "./tenantLang.ts";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const BRAND_RE = /adventure[\s\u00a0]+asia|adventure\.asia/i;
const AA_WORD_RE = /\bAA\b/;
const BRAND_FILE = "lib/brand.ts";

// Files an agency page reaches that may use Adventure Asia's own words, and why it never shows them on an agency page.
const AA_WORDS_ALLOWED: Record<string, string> = {
  "components/Header.tsx": "the AA bar; Header returns the agency bar first whenever the page passes an agency",
  "components/BrandContext.tsx": "the default for AA's own planner; an agency's planner always passes its BrandProvider",
};

const read = (rel: string) => readFileSync(join(ROOT, rel), "utf8");

// Comments say why code is the way it is (and name the brand freely); only code and copy count.
function code(src: string): string {
  return src
    .replace(/\{\/\*[\s\S]*?\*\/\}/g, "")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|[^:"'`\\])\/\/.*$/gm, "$1");
}

function resolveImport(from: string, spec: string): string | null {
  let base: string;
  if (spec.startsWith("@/")) base = join(ROOT, spec.slice(2));
  else if (spec.startsWith(".")) base = resolve(dirname(join(ROOT, from)), spec);
  else return null;
  for (const c of [base, `${base}.ts`, `${base}.tsx`, join(base, "index.ts"), join(base, "index.tsx")]) {
    if (existsSync(c) && statSync(c).isFile() && /\.(ts|tsx)$/.test(c)) return relative(ROOT, c);
  }
  return null;
}

const IMPORT_RE = /(?:import|export)\s[^;]*?from\s*["']([^"']+)["']|import\(\s*["']([^"']+)["']\s*\)/g;

function reached(entries: string[]): Set<string> {
  const seen = new Set<string>();
  const queue = [...entries];
  while (queue.length) {
    const f = queue.shift() as string;
    if (seen.has(f)) continue;
    seen.add(f);
    for (const m of read(f).matchAll(IMPORT_RE)) {
      const r = resolveImport(f, m[1] ?? m[2]);
      if (r) queue.push(r);
    }
  }
  return seen;
}

// Every file of the /{slug} site: its pages, layouts, error and not-found files.
function tenantEntries(): string[] {
  return (readdirSync(join(ROOT, "app/[tenant]"), { recursive: true }) as string[])
    .filter((f) => /\.(ts|tsx)$/.test(f))
    .map((f) => `app/[tenant]/${f.split("\\").join("/")}`)
    .sort();
}

test("the /{slug} site covers the pages an agency's traveller reaches", () => {
  const entries = tenantEntries();
  for (const page of ["page.tsx", "layout.tsx", "plan/page.tsx", "plan/shared/page.tsx", "tours/[tour]/page.tsx",
    "privacy/page.tsx", "terms/page.tsx"]) {
    assert.ok(entries.includes(`app/[tenant]/${page}`), page);
  }
});

test("no file an agency page reaches spells Adventure Asia (lib/brand.ts only)", () => {
  const files = reached([...tenantEntries(), "app/layout.tsx"]);
  assert.ok(files.has("components/TripPanel.tsx") && files.has("components/LeadForm.tsx"), "the walk reaches the planner");
  const offenders = [...files].filter((f) => f !== BRAND_FILE && BRAND_RE.test(code(read(f))));
  assert.deepEqual(offenders, [], "move the words to lib/brand.ts (brandCopy for shared components)");
});

// "AA" as a word in the copy of a file an agency page reaches ("no AA link", "on 3 AA tours") names Adventure Asia too.
// Identifiers such as AA_BAR or aa-site are not words here; string keys and test ids that are never shown are listed.
function aaWords(src: string): string[] {
  return [...src.matchAll(/[^\n]*\bAA\b[^\n]*/g)].map((m) => m[0].trim());
}

test("no file an agency page reaches shows \"AA\" as a word outside lib/brand.ts and the allow-list", () => {
  const files = reached([...tenantEntries(), "app/layout.tsx"]);
  const offenders = [...files]
    .filter((f) => f !== BRAND_FILE && !(f in AA_WORDS_ALLOWED))
    .flatMap((f) => aaWords(code(read(f))).map((line) => `${f}: ${line}`));
  assert.deepEqual(offenders, [], "route the words through brandCopy (lib/brand.ts)");
});

// The agency 404 (components/TenantNotFound.tsx): a page under /{slug} that can call notFound() must have a not-found
// file in its own segment or a parent segment below app/[tenant], else Next shows the root app/not-found.tsx, which is
// Adventure Asia's 404 (its bar, footer and catalogue links). The bare /{slug} is the one exception: an unknown slug
// there is no agency's site, and AA's own 404 for unknown top-level paths stays.
const NOT_FOUND_EXCEPTIONS: Record<string, string> = {
  "app/[tenant]/page.tsx": "the bare /{slug}: a served slug only redirects to its planner; an unknown one is AA's 404",
};

test("every /{slug} page that can be a 404 has the brand-free agency 404, and unknown sub-paths reach it", () => {
  const pages = tenantEntries().filter((f) => f.endsWith("/page.tsx"));
  for (const page of pages) {
    if (!/\bnotFound\(\)/.test(code(read(page)))) continue;
    if (page in NOT_FOUND_EXCEPTIONS) continue;
    let dir = dirname(page);
    let found = false;
    while (dir.startsWith("app/[tenant]/")) {
      if (existsSync(join(ROOT, dir, "not-found.tsx"))) {
        found = true;
        break;
      }
      dir = dirname(dir);
    }
    assert.ok(found, `${page}: add a not-found.tsx (components/TenantNotFound) in its segment`);
  }
  const notFounds = tenantEntries().filter((f) => f.endsWith("/not-found.tsx"));
  assert.ok(notFounds.length >= 5, notFounds.join(", "));
  for (const f of notFounds) assert.match(read(f), /<TenantNotFound\b/, `${f}: use components/TenantNotFound`);
  // An unknown /{slug}/... path has a page (a catch-all that is always a 404), so it never falls to the root 404.
  assert.match(code(read("app/[tenant]/[...rest]/page.tsx")), /notFound\(\);/);
  assert.ok(existsSync(join(ROOT, "app/[tenant]/[...rest]/not-found.tsx")));
  for (const [f, why] of Object.entries(NOT_FOUND_EXCEPTIONS)) assert.ok(existsSync(join(ROOT, f)) && why.length > 20, f);
  // A slug that is no served tenant is no agency's site: the layout sends it to the root 404 (AA's own host only), so
  // the agency 404 and its "Plan a trip" link are never shown for a planner that does not exist.
  const layout = code(read("app/[tenant]/layout.tsx"));
  assert.match(layout, /if \(!isTenantSlug\(tenant\)\) notFound\(\);/);
  assert.match(layout, /if \(site === null\) notFound\(\);/);
});

test("only the allow-listed files an agency page reaches use Adventure Asia's own words", () => {
  const files = reached(tenantEntries());
  for (const f of files) {
    if (f === BRAND_FILE) continue;
    const src = code(read(f));
    const aaImport = /import\s*\{[^}]*\bAA_[A-Z_]+[^}]*\}\s*from\s*["']@\/lib\/brand["']/.test(src);
    const aaCopy = /brandCopy\(\s*null\s*\)/.test(src);
    if (aaImport || aaCopy) assert.ok(f in AA_WORDS_ALLOWED, `${f} uses Adventure Asia's words on an agency page`);
  }
  for (const [f, why] of Object.entries(AA_WORDS_ALLOWED)) {
    assert.ok(files.has(f), `${f}: no longer reached, drop it from the allow-list`);
    assert.ok(why.length > 20, f);
  }
});

function strings(value: unknown, out: string[] = []): string[] {
  if (typeof value === "string") out.push(value);
  else if (typeof value === "function") {
    for (const arg of [0, 1, 2, 7]) strings((value as (n: number) => unknown)(arg), out);
  } else if (value && typeof value === "object") for (const v of Object.values(value)) strings(v, out);
  return out;
}

test("an agency's words never name Adventure Asia; the badge only when the agency switched it on", () => {
  for (const name of ["Quillmere Voyages", "  Ottervale Journeys  "]) {
    const copy = brandCopy(name);
    assert.equal(copy.agency, name.trim());
    const words = strings({ ...copy, poweredBy: null });
    assert.ok(words.length > 10);
    for (const w of words) {
      assert.doesNotMatch(w, BRAND_RE, w);
      assert.doesNotMatch(w, AA_WORD_RE, w);
    }
    assert.equal(copy.poweredBy(false), null);
    assert.deepEqual(copy.poweredBy(true), { name: "Adventure Asia", href: "https://adventure.asia/" });
    for (const source of ["master", "master_basic", "mixed", "llm"] as NarrationSource[]) {
      assert.ok(copy.narration[source].label && copy.narration[source].shortLabel && copy.narration[source].explanation);
    }
    assert.match(copy.consent, /^(Quillmere Voyages|Ottervale Journeys) may contact me/);
  }
  // Adventure Asia's own site (and an empty name) keeps its words; it never shows the BO-8 badge.
  for (const none of [null, "", "   "]) {
    const aa = brandCopy(none);
    assert.equal(aa.agency, null);
    assert.match(aa.coverage.built(2), /Adventure Asia tours/);
    assert.equal(aa.poweredBy(true), null);
  }
});

test("an agency's metadata replaces every brand-bearing key of the root layout's defaults", () => {
  const brandKeys = (Object.keys(AA_SITE_METADATA) as (keyof typeof AA_SITE_METADATA)[])
    .filter((k) => BRAND_RE.test(JSON.stringify(AA_SITE_METADATA[k])));
  assert.deepEqual(brandKeys.sort(), ["description", "openGraph", "title"]);
  for (const site of [{ name: "Quillmere Voyages", locale: "vi" }, { name: "Ottervale Journeys", locale: "en" }, null]) {
    const meta = tenantMetadata(site) as Record<string, unknown>;
    for (const k of brandKeys) assert.ok(k in meta, `tenantMetadata must set ${k}`);
    assert.doesNotMatch(JSON.stringify(meta), BRAND_RE);
    assert.doesNotMatch(JSON.stringify(meta), AA_WORD_RE);
    // A page title "X" becomes "X · <agency>", never "X · Adventure Asia".
    const title = meta.title as { default: string; template: string };
    assert.ok(title.default && title.template.includes("%s"));
    const og = meta.openGraph as { siteName: string };
    if (site) {
      assert.equal(title.template, `%s · ${site.name}`);
      assert.equal(og.siteName, site.name);
    }
  }
  assert.equal((tenantMetadata({ name: "Quillmere Voyages", locale: "vi" }).openGraph as { locale?: string }).locale, "vi_VN");
});

test("the /{slug} layout sets the agency's metadata and language; pages add only their own title", () => {
  const layout = code(read("app/[tenant]/layout.tsx"));
  assert.match(layout, /export async function generateMetadata[\s\S]*?return tenantMetadata\(/);
  assert.match(layout, /<div lang=\{lang\}/);
  assert.match(layout, /tenantLangScript\(lang\)/);
  assert.match(layout, /<TenantLang lang=\{lang\} \/>/);
  assert.doesNotMatch(layout, /<html/, "one root layout: the /{slug} layout never renders <html>");
  for (const f of tenantEntries().filter((e) => e.endsWith("page.tsx"))) {
    const src = code(read(f));
    // The layout's template adds "· <agency>"; a page that adds the name itself would show it twice.
    assert.doesNotMatch(src, /title:\s*`[^`]*·\s*\$\{site\.name\}/, f);
    assert.doesNotMatch(src, /siteName|openGraph/, `${f}: the layout owns og:site_name`);
  }
  assert.equal(tenantLang("vi"), "vi");
  for (const other of ["en", "fr", "", null, undefined, '"></script>']) assert.equal(tenantLang(other), "en");
  assert.equal(tenantLangScript("vi"), 'document.documentElement.lang="vi";');
  assert.equal(tenantLangScript('"></script><script>x'), 'document.documentElement.lang="en";');
});

test("the agency's planner, share view and tour page use the agency bar, and the planner the agency's words", () => {
  for (const f of ["app/[tenant]/plan/shared/page.tsx", "app/[tenant]/tours/[tour]/page.tsx", "components/AgencyLegalPage.tsx"]) {
    assert.match(read(f), /<Header agency=\{\{ slug: site\.slug, name: site\.name \}\} \/>/, f);
  }
  const app = read("app/plan/PlannerApp.tsx");
  assert.match(app, /<BrandProvider agency=\{brandSlug \? agencyName : null\} agencySlug=\{brandSlug\}>/);
  assert.match(app, /const brandSlug = headerAgency && isAgencyPlanner\(tenantSlug\) \? tenantSlug : null;/);
  for (const page of ["privacy", "terms"]) {
    const src = read(`app/[tenant]/${page}/page.tsx`);
    assert.match(src, /<AgencyLegalPage\b/);
    assert.match(src, /robots:\s*\{\s*index:\s*false/);
    assert.match(src, /if \(!isAgencyPlanner\(site\.slug\)\) redirect\(/, "the AA-hosted demo tenant keeps AA's page");
  }
  assert.match(read("app/[tenant]/terms/page.tsx"), /<LegalSection id="ai"[\s\S]*href=\{ANTHROPIC_USAGE_POLICY_URL\}/);
});
