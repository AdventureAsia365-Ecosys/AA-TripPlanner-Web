// AA-1038 R5 slice 2 — which first path segments can name a tenant (/{slug}). Shared by the server resolver
// (lib/tenantSite.ts) and the browser (lib/plannerTenant.ts). No next/* import, so `npm test` runs it.
//
// The slug shape is the one of shared.tenants (backend tenant_assertion.SLUG_RE). A slug equal to a static top-level
// path of this app can never be served (the static route wins over app/[tenant]), so those are refused here without
// a lookup: every top-level route folder of app/ and every top-level entry of public/ (lib/tenantSlug.test.ts keeps
// the list equal to the folders). CIS tenant creation should refuse the same list later (follow-up recorded in
// docs/implementation-notes/AA-1038.md); until then a tenant with such a slug simply has no TripPlanner site.

export const TENANT_SLUG_RE = /^[a-z0-9][a-z0-9_-]{0,99}$/;

// AA-1115: top-level pages that were retired (the traveller sign-in and My Trips, B2C gone under root ADR 0019 d11).
// They stay reserved, so an old /sign-in or /trips link is the 404 page without a lookup, never a tenant's site.
export const RETIRED_PATH_SLUGS: readonly string[] = ["sign-in", "trips"];

export const RESERVED_TENANT_SLUGS: ReadonlySet<string> = new Set([
  // app/ route folders
  "api",
  "destinations",
  "plan",
  "privacy",
  "terms",
  "tours",
  // public/ folders (served as /brand/..., /hero/...)
  "brand",
  "hero",
  ...RETIRED_PATH_SLUGS,
]);

export function isTenantSlug(value: unknown): value is string {
  return typeof value === "string" && TENANT_SLUG_RE.test(value) && !RESERVED_TENANT_SLUGS.has(value);
}
