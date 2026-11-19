// AA-1130 (S283 audit T1, root ADR 0019 S278) — the route finder ("Plan from a tour route") of a planner.
//
// * An agency's planner (/{slug}/plan) asks the browse Lambda for its own countries, routes and tour route
//   (`tenant=<slug>`): the Lambda narrows them to the tours the agency sells (catalog.v_tenant_tour_v1) and names each
//   with the agency's title, so no Adventure Asia tour name shows under the agency's brand. The bare /plan sends none.
// * "Heading to" lists the countries that have a tour (`tour_count`), not every country with experiences; an agency
//   page whose agency sells no tour has no route finder at all.
// * A proposal from a tour's first to last stop day that is shorter than the tour says "Full route · days 1–7 of 8",
//   not "Whole tour".
// No next/* import, so `npm test` runs it.

import { isTenantSlug } from "./tenantSlug.ts";
import type { CountryOption, RouteProposal } from "./types.ts";

/** The browse resources the route finder reads, which carry the planner's tenant. */
export const SCOPED_RESOURCES: ReadonlySet<string> = new Set(["countries", "routes", "tour-route"]);

/** Client: the query of a route finder read with the page's tenant added (no tenant = AA's planner, unchanged). */
export function withTenant(params: URLSearchParams, tenant: string | null | undefined): URLSearchParams {
  if (isTenantSlug(tenant)) params.set("tenant", tenant);
  return params;
}

/**
 * BFF: the upstream path of a route finder read with the browser's tenant passed on (`?tenant=` / `&tenant=`).
 * null = a tenant was given that cannot be a slug (the BFF answers 400, nothing reaches the Lambda). Only the scoped
 * resources take it; any other path comes back unchanged (a null path, already refused, stays null).
 */
export function scopedUpstreamPath(resource: string | null, path: string | null, tenant: string | null): string | null {
  if (path === null) return null;
  if (!resource || !SCOPED_RESOURCES.has(resource) || tenant === null || tenant === "") return path;
  if (!isTenantSlug(tenant)) return null;
  return `${path}${path.includes("?") ? "&" : "?"}tenant=${encodeURIComponent(tenant)}`;
}

/** "Heading to": the countries with at least one tour. An answer without counts (an older Lambda) keeps every one. */
export function headingCountries(countries: readonly CountryOption[]): CountryOption[] {
  return countries.filter((c) => c.tour_count === undefined || c.tour_count > 0);
}

/**
 * Whether the planner shows the route finder. AA's planner always does. An agency's planner hides it once its
 * countries have loaded with none that has a tour: the agency sells no tour, so there is no route of its own to
 * propose (and never an AA one).
 */
export function showRouteFinder(tenant: string | null, countriesLoaded: boolean, countries: readonly CountryOption[]): boolean {
  if (!tenant) return true;
  return !countriesLoaded || headingCountries(countries).length > 0;
}

export type RouteBadge = { kind: "whole"; label: string } | { kind: "span"; label: string } | null;

/**
 * The badge of a one-tour proposal. "Whole tour" only when the route covers the tour's every day; a route from its
 * first to last stop that is shorter than the tour (a stop-less last day) is "Full route · days 1–7 of 8". A chain
 * of two tours, or a cut of a tour, has no badge.
 */
export function routeBadge(p: Pick<RouteProposal, "whole_tour" | "total_days" | "tour_days" | "segments">): RouteBadge {
  if (!p.whole_tour || p.segments.length !== 1) return null;
  const seg = p.segments[0];
  if (p.tour_days && p.tour_days > p.total_days) {
    return { kind: "span", label: `Full route · days ${seg.day_from}–${seg.day_to} of ${p.tour_days}` };
  }
  return { kind: "whole", label: "Whole tour" };
}
