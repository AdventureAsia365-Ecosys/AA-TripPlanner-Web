// AA-1038 R5 hosting (S272) — every way into the planner is a full document load behind CloudFront.
//
// The web ACL issues the aws-waf-token on a document load of a planner page (rule TripPlannerChallengePlanner: GET +
// `sec-fetch-dest: document` on a path that ends with /plan or contains /plan/, AA-CIS-Infra modules/waf). A
// client-side Next navigation (an RSC fetch, `sec-fetch-dest: empty`) is never challenged, so a planner opened that
// way would have no token and its first search or narration would get the 202. So:
//   1. in-site links to the planner render a plain <a> (ui/SiteLink.tsx, ui/Button.tsx ButtonLink) and the home
//      search uses window.location.assign: the browser loads the planner as a document;
//   2. the planner reloads itself once into a document of the same URL when it was reached by a client-side
//      navigation anyway (a server redirect followed in the client, back / forward): reloadIntoPlannerDocument().
// Off the edge the extra load is harmless (and rare: step 1 covers the links).
//
// No next/* import: `npm test` runs it.

const SITE_ORIGIN = "https://site.invalid";

// The same test as the WAF rule (path ends with /plan or contains /plan/): /plan, /plan/..., /{tenant}/plan.
export function isPlannerPath(pathname: string): boolean {
  return pathname.endsWith("/plan") || pathname.includes("/plan/");
}

// A same-site href (path, with an optional query or hash) whose page is a planner page. External or
// protocol-relative hrefs are never planner links.
export function isPlannerHref(href: string): boolean {
  if (!href.startsWith("/") || href.startsWith("//")) return false;
  let u: URL;
  try {
    u = new URL(href, SITE_ORIGIN);
  } catch {
    return false;
  }
  return u.origin === SITE_ORIGIN && isPlannerPath(u.pathname);
}

// True when the page shows a planner URL but the document was loaded for another page (the planner came in by a
// client-side navigation, so the web ACL never challenged it). documentUrl: the URL of the document load
// (performance navigation entry); null when unknown (then no reload).
export function plannerNeedsDocumentLoad(currentPathname: string, documentUrl: string | null): boolean {
  if (!isPlannerPath(currentPathname) || !documentUrl) return false;
  let doc: URL;
  try {
    doc = new URL(documentUrl);
  } catch {
    return false;
  }
  return !isPlannerPath(doc.pathname);
}

function documentUrl(): string | null {
  try {
    const entry = performance.getEntriesByType("navigation")[0] as PerformanceNavigationTiming | undefined;
    return entry?.name || null;
  } catch {
    return null;
  }
}

// Step 2 above. Replaces the history entry (no extra back step). Returns whether it reloaded.
export function reloadIntoPlannerDocument(
  location: Pick<Location, "pathname" | "href" | "replace"> | null = typeof window === "undefined" ? null : window.location,
  docUrl: () => string | null = documentUrl,
): boolean {
  if (!location || !plannerNeedsDocumentLoad(location.pathname, docUrl())) return false;
  location.replace(location.href);
  return true;
}
