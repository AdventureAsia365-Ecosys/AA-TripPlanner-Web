"use client";

// AA-674 / Jira PR-11 point 1: "recommend trip routes that exist in AA data".
// The traveller says where they're heading and for how many days, and gets whole routes built
// from real Adventure Asia tours (one tour, or two chained at a junction). Hovering a route
// previews it on the map; "Start with this route" pins it, then the traveller edits it.

import { useState } from "react";
import { useTrip } from "@/lib/useTrip";
import { fetchRoutes } from "@/lib/api";
import type { RouteProposal } from "@/lib/types";

function placesLine(p: RouteProposal): string {
  const names: string[] = [];
  for (const d of p.days) {
    if (d.name && names[names.length - 1] !== d.name) names.push(d.name);
  }
  return names.join(" › ");
}

export default function RoutePlanner({ compact = false }: { compact?: boolean }) {
  const { countries, filters, applyRoute, setRoutePreview } = useTrip();
  const [country, setCountry] = useState<string>(filters.country ?? "");
  const [days, setDays] = useState<number>(7);
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
        className="aa-focus mb-3 w-full rounded-xl border border-dashed border-aa-gold/60 px-3 py-2 text-xs font-semibold text-aa-gold-dark transition hover:bg-aa-gold-soft"
      >
        🧭 Start from an Adventure Asia route
      </button>
    );
  }

  return (
    <div className="mb-3 rounded-xl border border-aa-line bg-white p-3" aria-label="Plan from an AA route">
      <div className="flex items-center justify-between">
        <p className="text-xs font-semibold text-aa-ink">Plan from an Adventure Asia route</p>
        {compact && (
          <button onClick={() => { setOpen(false); setRoutePreview(null); }} className="aa-focus text-[11px] text-aa-muted">
            Close
          </button>
        )}
      </div>
      <p className="mt-0.5 text-[11px] text-aa-muted">
        Routes built only from our real tours — pick one, then make it yours.
      </p>
      <div className="mt-2 flex gap-2">
        <select
          aria-label="Heading to"
          value={country}
          onChange={(e) => setCountry(e.target.value)}
          className="aa-focus min-w-0 flex-1 rounded-lg border border-aa-line px-2 py-1.5 text-xs text-aa-ink"
        >
          <option value="">Heading to…</option>
          {countries.map((c) => (
            <option key={c.country} value={c.country}>
              {c.country}
            </option>
          ))}
        </select>
        <input
          aria-label="Days"
          type="number"
          min={2}
          max={30}
          value={days}
          onChange={(e) => setDays(Math.max(2, Math.min(30, Number(e.target.value) || 2)))}
          className="aa-focus w-16 rounded-lg border border-aa-line px-2 py-1.5 text-xs text-aa-ink"
        />
        <button
          onClick={find}
          disabled={!country || loading}
          className="aa-focus shrink-0 rounded-lg bg-aa-gold px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-aa-gold-dark disabled:opacity-50"
        >
          {loading ? "Finding…" : "Find routes"}
        </button>
      </div>

      {proposals && proposals.length === 0 && (
        <p className="mt-2 text-[11px] text-aa-muted">
          No Adventure Asia route of about {days} days in {country} yet — try another length.
        </p>
      )}

      {proposals && proposals.length > 0 && (
        <ul className="mt-2 space-y-2" onMouseLeave={() => setRoutePreview(null)}>
          {proposals.map((p, i) => (
            <li
              key={i}
              onMouseEnter={() => setRoutePreview(p)}
              onFocus={() => setRoutePreview(p)}
              className="rounded-lg border border-aa-line p-2.5 transition hover:border-aa-gold/60"
            >
              <div className="flex items-center justify-between gap-2">
                <p className="text-xs font-semibold text-aa-ink">
                  {p.total_days} days
                  {p.whole_tour && (
                    <span className="ml-1.5 rounded-full bg-aa-gold-soft px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide text-aa-gold-dark">
                      Whole tour
                    </span>
                  )}
                  {p.transfers > 0 && (
                    <span className="ml-1.5 text-[10px] font-normal text-aa-muted">
                      2 tours · {Math.round(p.transfer_km)} km transfer
                    </span>
                  )}
                </p>
                <button
                  onClick={() => start(p, i)}
                  disabled={applying !== null}
                  className="aa-focus shrink-0 rounded-md border border-aa-gold px-2 py-0.5 text-[11px] font-semibold text-aa-gold-dark transition hover:bg-aa-gold-soft disabled:opacity-50"
                >
                  {applying === i ? "Adding…" : "Start with this route"}
                </button>
              </div>
              <ul className="mt-1 space-y-0.5">
                {p.segments.map((s) => (
                  <li key={`${s.tour_id}-${s.day_from}`} className="text-[11px] text-aa-ink-soft">
                    {s.tour_name ?? "AA tour"} · day {s.day_from}–{s.day_to}
                  </li>
                ))}
              </ul>
              <p className="mt-1 text-[11px] leading-relaxed text-aa-muted">{placesLine(p)}</p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
