// AA-1038 R5 slice 2 — which first path segments can name a tenant (/{slug}). Shared by the server resolver
// (lib/tenantSite.ts) and the browser (lib/plannerTenant.ts). No next/* import, so `npm test` runs it.
//
// The slug shape is the one of shared.tenants (backend tenant_assertion.SLUG_RE). A slug equal to a static top-level
// path of this app can never be served (the static route wins over app/[tenant]), so those are refused here without
// a lookup: every top-level route folder of app/ and every top-level entry of public/ (lib/tenantSlug.test.ts keeps
// the list equal to the folders). CIS tenant creation should refuse the same list later (follow-up recorded in
// docs/implementation-notes/AA-1038.md); until then a tenant with such a slug simply has no TripPlanner site.

export const TENANT_SLUG_RE = /^[a-z0-9][a-z0-9_-]{0,99}$/;

export const RESERVED_TENANT_SLUGS: ReadonlySet<string> = new Set([
  // app/ route folders
  "api",
  "destinations",
  "plan",
  "privacy",
  "sign-in",
  "terms",
  "tours",
  "trips",
  // public/ folders (served as /brand/..., /hero/...)
  "brand",
  "hero",
]);

export function isTenantSlug(value: unknown): value is string {
  return typeof value === "string" && TENANT_SLUG_RE.test(value) && !RESERVED_TENANT_SLUGS.has(value);
}
