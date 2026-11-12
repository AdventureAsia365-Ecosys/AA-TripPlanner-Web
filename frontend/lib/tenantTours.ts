// AA-1094 (root ADR 0019 d10, S278 audit M20 / M21) — the tours of an agency tenant's own pages.
//
// On /{slug}/plan the planner's tour list reads the tenant's tours (browse GET /browse/tenant/{slug}/tours, CIS view
// catalog.v_tenant_tour_v1: the live AA tours the agency made its own, with its own text) and each tour opens the
// tenant's tour page /{slug}/tours/{tour}, under the agency's brand. The AA-hosted demo tenant answers the AA
// catalogue (`source: "aa"`), so its list keeps linking to the AA tour pages /tours/{tour}. The bare /plan never names
// a tenant and keeps the AA catalogue. No next/* import, so `npm test` runs it.

import { isTenantSlug } from "./tenantSlug.ts";

export type TourListSource = "aa" | "tenant";

// The tour slug as the browse Lambda accepts it (backend/browse/handler.py _TENANT_TOUR_RE).
export const TOUR_SLUG_RE = /^[a-z0-9-]{1,120}$/;

/** The query keys the planner list passes through (backend/browse/catalog.py parse_list_query). */
export const LIST_KEYS = ["country", "min_days", "max_days", "month", "sort", "limit", "offset"] as const;

/** The browse path of a tenant's tour list, or null for a slug that cannot be a tenant (the BFF answers 400). */
export function tenantToursPath(tenant: string | null | undefined, query: URLSearchParams): string | null {
  if (!isTenantSlug(tenant)) return null;
  const q = new URLSearchParams();
  for (const k of LIST_KEYS) {
    const v = query.get(k);
    if (v) q.set(k, v);
  }
  const qs = q.toString();
  return `/browse/tenant/${tenant}/tours${qs ? `?${qs}` : ""}`;
}

/** The browse path of one tour page of a tenant, or null when either slug is malformed. */
export function tenantTourPath(tenant: string | null | undefined, tour: string | null | undefined): string | null {
  if (!isTenantSlug(tenant) || typeof tour !== "string" || !TOUR_SLUG_RE.test(tour)) return null;
  return `/browse/tenant/${tenant}/tours/${tour}`;
}

/** Where a tour of the planner's list opens: the tenant's own tour page when the list is the tenant's, else the AA
 * tour page (the bare planner and the demo tenant). */
export function tourHref(tenant: string | null | undefined, source: TourListSource | undefined, tourSlug: string): string {
  if (source === "tenant" && isTenantSlug(tenant)) return `/${tenant}/tours/${tourSlug}`;
  return `/tours/${tourSlug}`;
}

/** The tenant's planner with this tour pre-filled ("Plan this trip" on the tenant's tour page). */
export function tenantPlanHref(
  tenant: string,
  t: { tour_id: string; source_id: string; country: string | null; duration_days: number | null },
): string {
  const p = new URLSearchParams({ tour: t.tour_id, source: t.source_id });
  if (t.country) p.set("country", t.country);
  if (t.duration_days) p.set("days", String(Math.max(2, Math.min(30, t.duration_days))));
  return `/${tenant}/plan?${p}`;
}

/** The tenant's itinerary text as paragraphs (blank lines split them; single newlines stay inside one). */
export function itineraryParagraphs(text: string | null | undefined): string[] {
  if (!text) return [];
  return text
    .split(/\n\s*\n/)
    .map((p) => p.trim())
    .filter(Boolean);
}

// AA-1094 (EU AI Act Art. 50(2)): the machine-readable mark on a tenant's AI-written text blocks (summary, highlights,
// itinerary), from the browse answer's `ai` (backend tenant_ai_mark): an AI rewrite made at `generated_at`, or a
// version the agency edited (`human_edited`, no generation time of its own).
export type TenantTextMarkAttrs = {
  "data-ai-generated": "true";
  "data-ai-source": "tenant";
  "data-ai-human-edited": "true" | "false";
  "data-ai-generated-at"?: string;
};

export function tenantTextMark(ai: { generated_at: string | null; human_edited: boolean | null } | null | undefined): TenantTextMarkAttrs {
  const base: TenantTextMarkAttrs = {
    "data-ai-generated": "true",
    "data-ai-source": "tenant",
    "data-ai-human-edited": ai?.human_edited ? "true" : "false",
  };
  return ai?.generated_at ? { ...base, "data-ai-generated-at": ai.generated_at } : base;
}
