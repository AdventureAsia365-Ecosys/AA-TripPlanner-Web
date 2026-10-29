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
  { slug: "plan", route: "/plan", path: "/plan" },
  { slug: "trips", route: "/trips", path: "/trips" },
  { slug: "sign-in", route: "/sign-in", path: "/sign-in" },
  { slug: "privacy", route: "/privacy", path: "/privacy" },
  { slug: "terms", route: "/terms", path: "/terms" },
  // app/not-found.tsx (no page.tsx of its own): the 404 page of an unknown path.
  { slug: "not-found", route: "(not-found)", path: "/this-page-does-not-exist", status: 404 },
];

/** Routes with a page.tsx that the run does not open, each with its reason. */
export const EXCLUDED: Record<string, string> = {};
