// AA-994 S5b (plan section 4, guard G16) — the pages of the visual run. Pure data (no Playwright import), so the
// frontend unit test frontend/lib/visualPages.test.ts can check that every app/**/page.tsx route is listed here or
// excluded with a reason.
//
// TripPlanner has no signed-in visual run: every page is public (a guest sees /trips and /plan too).
// A dynamic route names the list page and the link pattern to resolve a real id from (the first match), so the run
// never depends on a hard-coded catalog slug.

export interface VisualPage {
  /** File-name-safe id, used in the screenshot names: `<slug>__<1440|390>__<light|dark>.png`. */
  slug: string;
  /** The app route (`app/<route>/page.tsx`), `/` for the home page; dynamic segments in brackets. */
  route: string;
  /** A fixed path to open; or `resolve` for a dynamic route. */
  path?: string;
  resolve?: { from: string; hrefPrefix: string };
  /** The document's expected HTTP status (default 200). */
  status?: number;
  /**
   * AA-1038 (S272): the web ACL challenges the first document load of the planner without a token (HTTP 202 +
   * x-amzn-waf-action: challenge); the browser solves it and reloads the page. The run accepts that one 202 and checks
   * the reloaded document instead.
   */
  challenged?: boolean;
}

export const PAGES: VisualPage[] = [
  { slug: "home", route: "/", path: "/" },
  { slug: "destinations", route: "/destinations", path: "/destinations" },
  {
    slug: "destination",
    route: "/destinations/[country]",
    resolve: { from: "/destinations", hrefPrefix: "/destinations/" },
  },
  { slug: "tour", route: "/tours/[slug]", resolve: { from: "/", hrefPrefix: "/tours/" } },
  { slug: "plan", route: "/plan", path: "/plan", challenged: true },
  // AA-1038 R5 slice 2: the demo tenant's planner on its slug path (404 page until CIS 379 is applied on Dev).
  { slug: "tenant-plan", route: "/[tenant]/plan", path: "/aa-tripplanner/plan", challenged: true },
  // AA-1041 (R7 item 6): the read-only share / print view. A link without a trip in it shows the kit empty state (the
  // run reads no catalog id it could put in a fragment).
  { slug: "plan-shared", route: "/plan/shared", path: "/plan/shared", challenged: true },
  { slug: "tenant-plan-shared", route: "/[tenant]/plan/shared", path: "/aa-tripplanner/plan/shared", challenged: true },
  { slug: "trips", route: "/trips", path: "/trips" },
  { slug: "sign-in", route: "/sign-in", path: "/sign-in" },
  { slug: "privacy", route: "/privacy", path: "/privacy" },
  { slug: "terms", route: "/terms", path: "/terms" },
  // app/not-found.tsx (no page.tsx of its own): the 404 page of an unknown path.
  { slug: "not-found", route: "(not-found)", path: "/this-page-does-not-exist", status: 404 },
];

/** Routes with a page.tsx that the run does not open, each with its reason. */
export const EXCLUDED: Record<string, string> = {
  "/[tenant]": "AA-1038: a tenant's bare slug only redirects to /{slug}/plan (captured as tenant-plan) or is the 404 page",
  "/[tenant]/tours/[tour]":
    "AA-1094: an agency's own tour page needs a served agency tenant with a finished T2 version; the demo tenant has " +
    "none (404), so the run cannot resolve one. Captured by the live check of the synthetic agency (1440 / 390, light / dark)",
};
