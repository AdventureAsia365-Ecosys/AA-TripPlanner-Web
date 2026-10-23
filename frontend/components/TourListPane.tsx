"use client";

// AA-812 — the planner's LIST view: AA tours (CIS catalog) for the country chosen on the map,
// filterable by travel month (the tour's "best time to go"; a tour that names no month stays in)
// and sortable by duration. Each tour opens its page or is pinned as a whole route (same apply-route
// flow as the route finder). A trip that already has stops is asked first, like PlanPrefill
// (apply-route merges). Pages of PAGE_SIZE with "Show more". Styled with the planner's aa-* palette.

import Link from "next/link";
import { useEffect, useState } from "react";
import { useTrip } from "@/lib/useTrip";
import { fetchCatalogTours, fetchTourRoute } from "@/lib/api";
import type { TourCard, TourSort } from "@/lib/catalogTypes";
import type { RouteProposal } from "@/lib/types";
import { coverAlt, daysText, formatDays, MONTH_NAMES } from "@/lib/text";

const PAGE_SIZE = 24;

type Load = { state: "loading" } | { state: "error" } | { state: "done"; tours: TourCard[]; total: number };

export default function TourListPane() {
  const { filters, applyRoute, itinerary } = useTrip();
  const [sort, setSort] = useState<Extract<TourSort, "duration_asc" | "duration_desc">>("duration_asc");
  const [month, setMonth] = useState(0); // 0 = any month
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<Record<string, string>>({});
  const [attempt, setAttempt] = useState(0);
  const [more, setMore] = useState<"idle" | "loading" | "error">("idle");

  // AA-906 (React Compiler lint, no setState in an effect body): the fetched page and the open
  // "add to your trip?" question are both tagged with the query they belong to. A new query
  // (country, month, sort, retry) shows "loading" and drops the question by derivation, instead of
  // resetting them from the effect; a page that lands for an older query is simply not shown.
  const queryKey = `${filters.country ?? ""}|${month}|${sort}|${attempt}`;
  const [loaded, setLoaded] = useState<{ key: string; load: Load } | null>(null);
  const load: Load = loaded?.key === queryKey ? loaded.load : { state: "loading" };
  const [confirmState, setConfirmState] = useState<{ key: string; tourId: string; p: RouteProposal } | null>(null);
  const confirm = confirmState?.key === queryKey ? confirmState : null;
  const setConfirm = (c: { tourId: string; p: RouteProposal } | null) =>
    setConfirmState(c ? { key: queryKey, ...c } : null);

  useEffect(() => {
    let active = true;
    fetchCatalogTours({ country: filters.country, month: month || undefined, sort, limit: PAGE_SIZE }).then((r) => {
      if (!active) return;
      setLoaded({ key: queryKey, load: r ? { state: "done", tours: r.tours, total: r.total } : { state: "error" } });
    });
    return () => {
      active = false;
    };
  }, [filters.country, month, sort, queryKey]);

  const showMore = async () => {
    if (load.state !== "done") return;
    const key = queryKey;
    setMore("loading");
    const r = await fetchCatalogTours({
      country: filters.country,
      month: month || undefined,
      sort,
      limit: PAGE_SIZE,
      offset: load.tours.length,
    });
    if (!r) {
      setMore("error");
      return;
    }
    const seen = new Set(load.tours.map((t) => t.tour_id));
    const tours = [...load.tours, ...r.tours.filter((t) => !seen.has(t.tour_id))];
    // Only onto the page it extends: a query changed meanwhile (key differs) keeps its own page.
    setLoaded((cur) => (cur?.key === key ? { key, load: { state: "done", tours, total: r.total } } : cur));
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
    <div className="absolute inset-0 z-[15] overflow-y-auto bg-k-bg px-4 pb-20 pt-4" aria-label="Tours list">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-semibold text-k-ink">
          {filters.country ? `Tours in ${filters.country}` : "All Adventure Asia tours"}
          {load.state === "done" && <span className="ml-1.5 font-normal text-k-muted">({load.total})</span>}
          <span className="block text-[11px] font-normal text-k-muted">Change the country on the Map view.</span>
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-1.5 text-xs text-k-muted">
            Travel in
            <select
              value={month}
              onChange={(e) => setMonth(Number(e.target.value))}
              className="aa-focus rounded-lg border border-k-line bg-k-card px-2 py-1 text-xs text-k-ink"
            >
              <option value={0}>Any month</option>
              {MONTH_NAMES.map((m, i) => (
                <option key={m} value={i + 1}>{m}</option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-1.5 text-xs text-k-muted">
            Sort
            <select
              value={sort}
              onChange={(e) => setSort(e.target.value as typeof sort)}
              className="aa-focus rounded-lg border border-k-line bg-k-card px-2 py-1 text-xs text-k-ink"
            >
              <option value="duration_asc">Shortest first</option>
              <option value="duration_desc">Longest first</option>
            </select>
          </label>
        </div>
      </div>

      {load.state === "loading" && <p className="mt-6 text-center text-xs text-k-muted">Loading tours…</p>}
      {load.state === "error" && (
        <div role="alert" className="mt-6 rounded-xl border border-k-line bg-k-card p-4 text-center text-xs text-k-muted">
          We couldn&apos;t load tours right now.{" "}
          <button onClick={() => setAttempt((a) => a + 1)} className="aa-focus font-semibold text-k-accent-deep underline">
            Try again
          </button>
        </div>
      )}
      {load.state === "done" && load.tours.length === 0 && (
        <p className="mt-6 rounded-xl border border-dashed border-k-line bg-k-card p-4 text-center text-xs text-k-muted">
          {month
            ? `No tours ${filters.country ? `in ${filters.country} ` : ""}list ${MONTH_NAMES[month - 1]} as a good time to go. Try another month.`
            : `No Adventure Asia tours ${filters.country ? `in ${filters.country} ` : ""}yet. Try another country.`}
        </p>
      )}
      {load.state === "done" && load.tours.length > 0 && (
        <ul className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {load.tours.map((t) => (
            <li key={t.tour_id} className="flex gap-3 rounded-xl border border-k-line bg-k-card p-2.5">
              {t.cover_url_small ? (
                // A fallback (no tour photo) is a destination photo: named so in alt and title.
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={t.cover_url_small}
                  alt={t.cover_source === "tour" ? "" : coverAlt(t)}
                  title={t.cover_source === "tour" ? undefined : coverAlt(t)}
                  loading="lazy"
                  className="h-20 w-24 shrink-0 rounded-lg object-cover"
                />
              ) : (
                <div aria-hidden className="h-20 w-24 shrink-0 rounded-lg bg-k-accent-tint" />
              )}
              <div className="min-w-0 flex-1">
                <p className="text-[11px] font-semibold uppercase tracking-wide text-k-accent-deep">
                  {daysText(t.duration_days, t.duration_raw) ?? ""}
                  {t.country && !filters.country ? ` · ${t.country}` : ""}
                </p>
                {/* AA-833: no prefetch; the list shows many tours at once (see TourCard). */}
                <Link href={`/tours/${t.slug}`} prefetch={false} className="aa-focus line-clamp-2 text-sm font-semibold text-k-ink hover:underline">
                  {t.title}
                </Link>
                {confirm?.tourId === t.tour_id ? (
                  <div className="mt-1.5 text-[11px] text-k-ink" role="group" aria-label="Add to your current trip?">
                    <p>Add its route ({formatDays(confirm.p.total_days)}) to your current trip?</p>
                    <div className="mt-1 flex gap-2">
                      <button
                        onClick={() => add(t.tour_id, confirm.p)}
                        className="aa-focus rounded-md bg-k-accent px-2 py-0.5 font-semibold text-k-on-accent"
                      >
                        Add its route
                      </button>
                      <button onClick={() => setConfirm(null)} className="aa-focus rounded-md border border-k-line px-2 py-0.5 text-k-muted">
                        Not now
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="mt-1.5 flex items-center gap-2">
                    <button
                      onClick={() => plan(t)}
                      disabled={busy !== null}
                      className="aa-focus rounded-md border border-k-accent px-2 py-0.5 text-[11px] font-semibold text-k-accent-deep transition hover:bg-k-accent-tint disabled:opacity-50"
                    >
                      {busy === t.tour_id ? "Adding…" : "Plan this route"}
                    </button>
                    {note[t.tour_id] && <span className="text-[11px] text-k-muted">{note[t.tour_id]}</span>}
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
            className="aa-focus rounded-full border border-k-line bg-k-card px-4 py-1.5 text-xs font-semibold text-k-ink transition hover:bg-k-accent-tint disabled:opacity-50"
          >
            {more === "loading" ? "Loading…" : `Show more (${load.tours.length} of ${load.total})`}
          </button>
          {more === "error" && <p className="mt-1 text-[11px] text-k-muted">Couldn&apos;t load more tours. Try again.</p>}
        </div>
      )}
    </div>
  );
}
