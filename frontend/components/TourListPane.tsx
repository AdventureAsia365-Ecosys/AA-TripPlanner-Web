"use client";

// AA-812 — the planner's LIST view: AA tours (CIS catalog) for the country chosen on the map,
// sortable by duration. Each tour opens its page or is pinned as a whole route (same apply-route
// flow as the route finder). Styled with the planner's own aa-* palette.

import Link from "next/link";
import { useEffect, useState } from "react";
import { useTrip } from "@/lib/useTrip";
import { fetchCatalogTours, fetchTourRoute } from "@/lib/api";
import type { TourCard, TourSort } from "@/lib/catalogTypes";

type Load = { state: "loading" } | { state: "error" } | { state: "done"; tours: TourCard[]; total: number };

export default function TourListPane() {
  const { filters, applyRoute } = useTrip();
  const [sort, setSort] = useState<Extract<TourSort, "duration_asc" | "duration_desc">>("duration_asc");
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<Record<string, string>>({});
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let active = true;
    setLoad({ state: "loading" });
    fetchCatalogTours({ country: filters.country, sort, limit: 60 }).then((r) => {
      if (!active) return;
      setLoad(r ? { state: "done", tours: r.tours, total: r.total } : { state: "error" });
    });
    return () => {
      active = false;
    };
  }, [filters.country, sort, attempt]);

  const plan = async (t: TourCard) => {
    setBusy(t.tour_id);
    try {
      const p = await fetchTourRoute(t.tour_id);
      if (p === "missing") setNote((n) => ({ ...n, [t.tour_id]: "Not on the planner map yet — ask an advisor." }));
      else if (p === "error") setNote((n) => ({ ...n, [t.tour_id]: "Couldn't load this route. Try again." }));
      else if (await applyRoute(p)) setNote((n) => ({ ...n, [t.tour_id]: "Added to your trip." }));
      else setNote((n) => ({ ...n, [t.tour_id]: "Couldn't add this route. Try again." }));
    } finally {
      setBusy(null);
    }
  };

  // Above the filter panel (z-15 < the Map/List toggle); the country follows the map filter.
  return (
    <div className="absolute inset-0 z-[15] overflow-y-auto bg-aa-offwhite px-4 pb-20 pt-4" aria-label="Tours list">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-semibold text-aa-ink">
          {filters.country ? `Tours in ${filters.country}` : "All Adventure Asia tours"}
          {load.state === "done" && <span className="ml-1.5 font-normal text-aa-muted">({load.total})</span>}
          <span className="block text-[11px] font-normal text-aa-muted">Change the country on the Map view.</span>
        </p>
        <label className="flex items-center gap-1.5 text-xs text-aa-muted">
          Sort
          <select
            value={sort}
            onChange={(e) => setSort(e.target.value as typeof sort)}
            className="aa-focus rounded-lg border border-aa-line bg-white px-2 py-1 text-xs text-aa-ink"
          >
            <option value="duration_asc">Shortest first</option>
            <option value="duration_desc">Longest first</option>
          </select>
        </label>
      </div>

      {load.state === "loading" && <p className="mt-6 text-center text-xs text-aa-muted">Loading tours…</p>}
      {load.state === "error" && (
        <div role="alert" className="mt-6 rounded-xl border border-aa-line bg-white p-4 text-center text-xs text-aa-muted">
          We couldn&apos;t load tours right now.{" "}
          <button onClick={() => setAttempt((a) => a + 1)} className="aa-focus font-semibold text-aa-gold-dark underline">
            Try again
          </button>
        </div>
      )}
      {load.state === "done" && load.tours.length === 0 && (
        <p className="mt-6 rounded-xl border border-dashed border-aa-line bg-white p-4 text-center text-xs text-aa-muted">
          No Adventure Asia tours {filters.country ? `in ${filters.country} ` : ""}yet. Try another country.
        </p>
      )}
      {load.state === "done" && load.tours.length > 0 && (
        <ul className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {load.tours.map((t) => (
            <li key={t.tour_id} className="flex gap-3 rounded-xl border border-aa-line bg-white p-2.5">
              {t.cover_url_small ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={t.cover_url_small} alt="" loading="lazy" className="h-20 w-24 shrink-0 rounded-lg object-cover" />
              ) : (
                <div aria-hidden className="h-20 w-24 shrink-0 rounded-lg bg-aa-gold-soft" />
              )}
              <div className="min-w-0 flex-1">
                <p className="text-[11px] font-semibold uppercase tracking-wide text-aa-gold-dark">
                  {t.duration_days ? `${t.duration_days} days` : t.duration_raw ?? ""}
                  {t.country && !filters.country ? ` · ${t.country}` : ""}
                </p>
                <Link href={`/tours/${t.slug}`} className="aa-focus line-clamp-2 text-sm font-semibold text-aa-ink hover:underline">
                  {t.title}
                </Link>
                <div className="mt-1.5 flex items-center gap-2">
                  <button
                    onClick={() => plan(t)}
                    disabled={busy !== null}
                    className="aa-focus rounded-md border border-aa-gold px-2 py-0.5 text-[11px] font-semibold text-aa-gold-dark transition hover:bg-aa-gold-soft disabled:opacity-50"
                  >
                    {busy === t.tour_id ? "Adding…" : "Plan this route"}
                  </button>
                  {note[t.tour_id] && <span className="text-[11px] text-aa-muted">{note[t.tour_id]}</span>}
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
