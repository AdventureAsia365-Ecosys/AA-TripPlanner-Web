// AA-1111 (S294 ZAP "Big Redirect Detected") — the bare /{slug} of an agency's site goes to its planner, /{slug}/plan.
// proxy.ts answers that redirect itself, with no body: a redirect() in app/[tenant]/page.tsx is a 307 that Next
// sends only after streaming the page shell (about 10 KB of the agency's page). Pure, so `npm test` runs it.
import { isTenantSlug } from "./tenantSlug.ts";

/** The slug when the path is an agency's bare site ("/{slug}" or "/{slug}/"), else null. */
export function tenantRootSlug(pathname: string): string | null {
  const m = /^\/([^/]+)\/?$/.exec(pathname);
  return m && isTenantSlug(m[1]) ? m[1] : null;
}

/** Where the bare site of a served tenant goes (the same target as app/[tenant]/page.tsx). */
export function tenantPlannerPath(slug: string): string {
  return `/${slug}/plan`;
}
