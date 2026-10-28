// AA-995 S256 (mobile LCP) guard: an LCP-style image (fetchPriority="high", loading="eager") or an image
// `<link rel="preload">` rendered by a Server Component becomes a React Flight image preload hint in the RSC payload,
// and every prefetch of that route then downloads the image on the page the visitor is on. Such elements must render
// through a Client Component (components/site/LcpImage.tsx, components/site/HomeHeroPicture.tsx).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

const ROOT = new URL("..", import.meta.url).pathname;

function tsxFiles(dir: string): string[] {
  return (readdirSync(join(ROOT, dir), { recursive: true }) as string[])
    .filter((f) => f.endsWith(".tsx"))
    .map((f) => join(dir, f));
}

function isClientModule(src: string): boolean {
  // The directive must be the first statement; comments may precede it.
  const body = src.replace(/^(?:\s*(?:\/\/[^\n]*\n|\/\*[\s\S]*?\*\/))*\s*/, "");
  return /^["']use client["']/.test(body);
}

// Raw <img …> / <link …> elements (JSX, up to the closing "/>" or ">").
function elements(src: string, tag: "img" | "link"): string[] {
  return src.match(new RegExp(`<${tag}\\b[\\s\\S]*?\\/?>`, "g")) ?? [];
}

test("server components render no high-priority / eager <img> and no image preload <link>", () => {
  const offenders: string[] = [];
  for (const file of [...tsxFiles("app"), ...tsxFiles("components")]) {
    const src = readFileSync(join(ROOT, file), "utf8");
    if (isClientModule(src)) continue;
    for (const el of elements(src, "img")) {
      if (/fetchPriority=\{?["']high["']|loading=\{?["']eager["']/.test(el)) offenders.push(`${file}: ${el.slice(0, 80)}`);
    }
    for (const el of elements(src, "link")) {
      if (/rel=["']preload["']/.test(el) && /as=["']image["']/.test(el)) offenders.push(`${file}: ${el.slice(0, 80)}`);
    }
  }
  assert.deepEqual(offenders, [], "render these through components/site/LcpImage.tsx (a Client Component)");
});

test("LcpImage and HomeHeroPicture stay Client Components", () => {
  for (const file of ["components/site/LcpImage.tsx", "components/site/HomeHeroPicture.tsx"]) {
    assert.ok(isClientModule(readFileSync(join(ROOT, file), "utf8")), `${file} lost its "use client" directive`);
  }
});
