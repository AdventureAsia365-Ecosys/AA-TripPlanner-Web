"use client";

// AA-674 / Jira PR-11 point 1: "recommend trip routes that exist in AA data".
// The traveller says where they're heading and for how many days, and gets whole routes built
// from real Adventure Asia tours (one tour, or two chained at a junction). Hovering a route
// previews it on the map; "Start with this route" pins it, then the traveller edits it.
// AA-1130 (S283 audit T1): on an agency's planner it proposes only the tours the agency sells, under the agency's own
// titles, and is hidden when the agency sells none; "Heading to" lists only countries with a tour (lib/routeFinder.ts).

import { useEffect, useRef, useState } from "react";
import { useTrip } from "@/lib/useTrip";
import { useBrand } from "@/components/BrandContext";
import { fetchRoutes } from "@/lib/api";
import { plannerTenant } from "@/lib/plannerTenant";
import { headingCountries, routeBadge, showRouteFinder } from "@/lib/routeFinder";
import { usePlannerLang, usePlannerT } from "@/components/PlannerLang";
import { ROUTES, formatDaysIn } from "@/lib/planner.messages";
import { Icon } from "@/ui/Icon";
import type { RouteProposal } from "@/lib/types";

function placesLine(p: RouteProposal): string {
  const names: string[] = [];
  for (const d of p.days) {
    if (d.name && names[names.length - 1] !== d.name) names.push(d.name);
  }
  return names.join(" › ");
}

// AA-1130: "Whole tour", or "Full route · days 1–7 of 8" when the route is shorter than its tour.
function RouteBadgeChip({ p }: { p: RouteProposal }) {
  const badge = routeBadge(p);
  if (!badge) return null;
  return (
    <span
      data-testid="route-badge"
      className={`ml-1.5 inline-block whitespace-nowrap rounded-full bg-k-accent-tint px-1.5 py-0.5 text-[9px] font-bold tracking-wide text-k-accent-deep${badge.kind === "whole" ? " uppercase" : ""}`}
    >
      {badge.label}
    </span>
  );
}

// AA-1130: an agency page whose agency sells no tour has no route finder (lib/routeFinder.ts showRouteFinder).
export default function RouteFinderGate({ compact = false }: { compact?: boolean }) {
  const { countries, countriesLoaded } = useTrip();
  if (!showRouteFinder(plannerTenant(), countriesLoaded, countries)) return null;
  return <RoutePlanner compact={compact} />;
}

function RoutePlanner({ compact }: { compact: boolean }) {
  const { countries: allCountries, filters, applyRoute, setRoutePreview, intent } = useTrip();
  const t = usePlannerT(ROUTES);
  const lang = usePlannerLang();
  const formatDays = (n: number) => formatDaysIn(n, lang);
  const countries = headingCountries(allCountries);
  const { copy } = useBrand(); // AA-1115: an unnamed tour is "AA tour" on AA's planner only
  // AA-812: pre-filled from the home search / a tour page (/plan?country=&days=&travellers=).
  const [country, setCountry] = useState<string>(intent.country ?? filters.country ?? "");
  const [days, setDays] = useState<number>(Math.max(2, Math.min(30, intent.days ?? 7)));
  const [loading, setLoading] = useState(false);
  const [applying, setApplying] = useState<number | null>(null);
  const [proposals, setProposals] = useState<RouteProposal[] | null>(null);
  const [open, setOpen] = useState(!compact);

  const find = async () => {
    if (!country) return;
    setLoading(true);
    setRoutePreview(null);
    try {
      setProposals(await fetchRoutes(country, days));
    } finally {
      setLoading(false);
    }
  };

  // AA-812: arriving from the home search with a destination, show its routes at once (the full
  // finder only; a tour page's pre-fill pins that tour instead).
  const autoFound = useRef(false);
  useEffect(() => {
    if (compact || autoFound.current || !intent.country || intent.tourId || intent.sourceId) return;
    autoFound.current = true;
    void find();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const start = async (p: RouteProposal, i: number) => {
    setApplying(i);
    try {
      if (await applyRoute(p)) setProposals(null);
    } finally {
      setApplying(null);
    }
  };

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="aa-focus mb-3 w-full rounded-xl border border-dashed border-k-accent-border px-3 py-2 text-xs font-semibold text-k-accent-deep transition hover:bg-k-accent-tint"
      >
        <Icon name="compass" className="mr-1 inline h-3.5 w-3.5 align-[-2px]" />
        {t("startFrom")}
      </button>
    );
  }

  return (
    <div className="mb-3 rounded-xl border border-k-line bg-k-card p-3" aria-label={t("planFromRoute")}>
      <div className="flex items-center justify-between">
        <p className="text-xs font-semibold text-k-ink">Plan from a tour route</p>
        {compact && (
          <button onClick={() => { setOpen(false); setRoutePreview(null); }} className="aa-focus text-[11px] text-k-muted">
            Close
          </button>
        )}
      </div>
      <p className="mt-0.5 text-[11px] text-k-muted">
        {t("intro")}
        {intent.travellers ? t("planningFor", intent.travellers) : ""}
      </p>
      <div className="mt-2 flex gap-2">
        <select
          aria-label={t("headingTo")}
          value={country}
          onChange={(e) => setCountry(e.target.value)}
          className="aa-focus min-w-0 flex-1 rounded-lg border border-k-line bg-k-card px-2 py-1.5 text-xs text-k-ink"
        >
          <option value="">{t("headingToOption")}</option>
          {/* AA-812: a country pre-filled from the home page that the planner list does not have yet */}
          {country && !countries.some((c) => c.country === country) && <option value={country}>{country}</option>}
          {countries.map((c) => (
            <option key={c.country} value={c.country}>
              {c.country}
            </option>
          ))}
        </select>
        <input
          aria-label={t("days")}
          type="number"
          min={2}
          max={30}
          value={days}
          onChange={(e) => setDays(Math.max(2, Math.min(30, Number(e.target.value) || 2)))}
          className="aa-focus w-16 rounded-lg border border-k-line bg-k-card px-2 py-1.5 text-xs text-k-ink"
        />
        <button
          onClick={find}
          disabled={!country || loading}
          className="aa-focus shrink-0 rounded-lg bg-k-accent px-3 py-1.5 text-xs font-semibold text-k-on-accent transition hover:brightness-95 disabled:opacity-50"
        >
          {loading ? t("finding") : t("find")}
        </button>
      </div>

      {proposals && proposals.length === 0 && (
        <p className="mt-2 text-[11px] text-k-muted">
          {t("none", formatDays(days), country)}
        </p>
      )}

      {proposals && proposals.length > 0 && (
        <ul className="mt-2 space-y-2" onMouseLeave={() => setRoutePreview(null)}>
          {proposals.map((p, i) => (
            <li
              key={i}
              onMouseEnter={() => setRoutePreview(p)}
              onFocus={() => setRoutePreview(p)}
              className="rounded-lg border border-k-line p-2.5 transition hover:border-k-accent-border"
            >
              <div className="flex items-center justify-between gap-2">
                <p className="text-xs font-semibold text-k-ink">
                  {formatDays(p.total_days)}
                  <RouteBadgeChip p={p} />
                  {p.transfers > 0 && (
                    <span className="ml-1.5 text-[10px] font-normal text-k-muted">
                      2 tours · {Math.round(p.transfer_km)} km transfer
                    </span>
                  )}
                </p>
                <button
                  onClick={() => start(p, i)}
                  disabled={applying !== null}
                  className="aa-focus shrink-0 rounded-md border border-k-accent px-2 py-0.5 text-[11px] font-semibold text-k-accent-deep transition hover:bg-k-accent-tint disabled:opacity-50"
                >
                  {applying === i ? t("adding") : t("startWith")}
                </button>
              </div>
              <ul className="mt-1 space-y-0.5">
                {p.segments.map((s) => (
                  <li key={`${s.tour_id}-${s.day_from}`} className="text-[11px] text-k-body">
                    {s.tour_name ?? copy.coverage.unnamedTour} · day {s.day_from}–{s.day_to}
                  </li>
                ))}
              </ul>
              <p className="mt-1 text-[11px] leading-relaxed text-k-muted">{placesLine(p)}</p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
