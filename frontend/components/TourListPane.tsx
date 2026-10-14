"use client";

// AA-812 — the planner's LIST view: AA tours (CIS catalog) for the country chosen on the map,
// sortable by duration. Each tour opens its page or is pinned as a whole route (same apply-route
// flow as the route finder). A trip that already has stops is asked first, like PlanPrefill
// (apply-route merges). Pages of PAGE_SIZE with "Show more". Styled with the planner's aa-* palette.

import Link from "next/link";
import { useEffect, useState } from "react";
import { useTrip } from "@/lib/useTrip";
import { fetchCatalogTours, fetchTourRoute } from "@/lib/api";
import type { TourCard, TourSort } from "@/lib/catalogTypes";
import type { RouteProposal } from "@/lib/types";

const PAGE_SIZE = 24;

type Load = { state: "loading" } | { state: "error" } | { state: "done"; tours: TourCard[]; total: number };

export default function TourListPane() {
  const { filters, applyRoute, itinerary } = useTrip();
  const [sort, setSort] = useState<Extract<TourSort, "duration_asc" | "duration_desc">>("duration_asc");
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<Record<string, string>>({});
  const [attempt, setAttempt] = useState(0);
  const [confirm, setConfirm] = useState<{ tourId: string; p: RouteProposal } | null>(null);
  const [more, setMore] = useState<"idle" | "loading" | "error">("idle");

  useEffect(() => {
    let active = true;
    setLoad({ state: "loading" });
    setConfirm(null);
    fetchCatalogTours({ country: filters.country, sort, limit: PAGE_SIZE }).then((r) => {
      if (!active) return;
      setLoad(r ? { state: "done", tours: r.tours, total: r.total } : { state: "error" });
    });
    return () => {
      active = false;
    };
  }, [filters.country, sort, attempt]);

  const showMore = async () => {
    if (load.state !== "done") return;
    setMore("loading");
    const r = await fetchCatalogTours({ country: filters.country, sort, limit: PAGE_SIZE, offset: load.tours.length });
    if (!r) {
      setMore("error");
      return;
    }
    const seen = new Set(load.tours.map((t) => t.tour_id));
    setLoad({ state: "done", tours: [...load.tours, ...r.tours.filter((t) => !seen.has(t.tour_id))], total: r.total });
    setMore("idle");
  };

  const say = (id: string, text: string) => setNote((n) => ({ ...n, [id]: text }));

  const add = async (tourId: string, p: RouteProposal) => {
    setConfirm(null);
    setBusy(tourId);
    try {
      say(tourId, (await applyRoute(p)) ? "Added to your trip." : "Couldn't add this route. Try again.");
    } finally {
      setBusy(null);
    }
  };

  const plan = async (t: TourCard) => {
    setBusy(t.tour_id);
    let p: Awaited<ReturnType<typeof fetchTourRoute>>;
    try {
      p = await fetchTourRoute(t.tour_id, t.source_id);
    } finally {
      setBusy(null);
    }
    if (p === "error") say(t.tour_id, "Couldn't load this route. Try again.");
    else if ("missing" in p) say(t.tour_id, "Not on the planner map yet — ask an advisor.");
    // A trip with stops is asked first: apply-route merges into it (never deletes).
    else if (itinerary.length > 0) setConfirm({ tourId: t.tour_id, p });
    else await add(t.tour_id, p);
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
                {confirm?.tourId === t.tour_id ? (
                  <div className="mt-1.5 text-[11px] text-aa-ink" role="group" aria-label="Add to your current trip?">
                    <p>Add its route ({confirm.p.total_days} days) to your current trip?</p>
                    <div className="mt-1 flex gap-2">
                      <button
                        onClick={() => add(t.tour_id, confirm.p)}
                        className="aa-focus rounded-md bg-aa-gold px-2 py-0.5 font-semibold text-white"
                      >
                        Add its route
                      </button>
                      <button onClick={() => setConfirm(null)} className="aa-focus rounded-md border border-aa-line px-2 py-0.5 text-aa-muted">
                        Not now
                      </button>
                    </div>
                  </div>
                ) : (
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
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
      {load.state === "done" && load.tours.length < load.total && (
        <div className="mt-4 text-center">
          <button
            onClick={showMore}
            disabled={more === "loading"}
            className="aa-focus rounded-full border border-aa-line bg-white px-4 py-1.5 text-xs font-semibold text-aa-ink transition hover:bg-aa-gold-soft disabled:opacity-50"
          >
            {more === "loading" ? "Loading…" : `Show more (${load.tours.length} of ${load.total})`}
          </button>
          {more === "error" && <p className="mt-1 text-[11px] text-aa-muted">Couldn&apos;t load more tours. Try again.</p>}
        </div>
      )}
    </div>
  );
}
