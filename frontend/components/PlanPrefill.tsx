"use client";

// AA-812 — arriving from a tour page ("Customise this trip" / "Ask an advisor"): load that tour's
// whole route and pin it with the existing apply-route flow. An empty trip gets it at once; a trip
// that already has stops asks first (apply-route merges, it never deletes). If the tour is not on
// the planner's Tour Graph yet, the Lambda builds it from the tour's places (match "places"); if the
// planner has nothing for the tour at all, the route finder stays pre-filled with its country and
// length and this box lists the tour's days (AA-812 hotfix D2), instead of a bare "not on the map".

import { useEffect, useRef, useState } from "react";
import { useTrip } from "@/lib/useTrip";
import { fetchTourRoute } from "@/lib/api";
import { formatDays } from "@/lib/text";
import type { RouteProposal, TourRouteFallback } from "@/lib/types";

// Once the prompt is answered (route added, "Not now", "OK"), drop the tour params from the URL so a
// reload of the restored trip does not ask again. Other params (country, days) stay.
function forgetTourParams() {
  const url = new URL(window.location.href);
  let changed = false;
  for (const k of ["tour", "source", "ask"]) {
    if (url.searchParams.has(k)) {
      url.searchParams.delete(k);
      changed = true;
    }
  }
  if (changed) window.history.replaceState(window.history.state, "", `${url.pathname}${url.search}${url.hash}`);
}

type State =
  | { s: "idle" }
  | { s: "confirm"; p: RouteProposal }
  | { s: "applied"; name: string; places: boolean }
  | { s: "missing"; tour: TourRouteFallback | null }
  | { s: "error" };

export default function PlanPrefill({ onApplied }: { onApplied?: () => void }) {
  const { intent, ready, itinerary, applyRoute } = useTrip();
  const [st, setSt] = useState<State>({ s: "idle" });
  const started = useRef(false);

  const apply = async (p: RouteProposal) => {
    if (await applyRoute(p)) {
      forgetTourParams();
      setSt({ s: "applied", name: p.segments[0]?.tour_name ?? "This tour", places: p.match === "places" });
      onApplied?.();
    } else {
      setSt({ s: "error" });
    }
  };

  useEffect(() => {
    if ((!intent.tourId && !intent.sourceId) || !ready || started.current) return;
    started.current = true;
    fetchTourRoute(intent.tourId, intent.sourceId).then((p) => {
      if (p === "error") setSt({ s: "error" });
      else if ("missing" in p) setSt({ s: "missing", tour: p.tour });
      else if (itinerary.length === 0) void apply(p);
      else setSt({ s: "confirm", p });
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intent.tourId, intent.sourceId, ready]);

  if (st.s === "idle") return null;
  const close = () => {
    forgetTourParams();
    setSt({ s: "idle" });
  };
  const box = "pointer-events-auto mx-auto max-w-md rounded-xl border border-k-line bg-k-card px-3 py-2.5 text-xs text-k-ink shadow-aa";

  return (
    <div className="pointer-events-none absolute inset-x-3 bottom-16 z-20" role="status">
      <div className={box}>
        {st.s === "confirm" && (
          <>
            <p>
              Add <strong>{st.p.segments[0]?.tour_name ?? "this tour"}</strong> ({formatDays(st.p.total_days)}) to your
              current trip?
            </p>
            <div className="mt-2 flex gap-2">
              <button onClick={() => apply(st.p)} className="aa-focus rounded-md bg-k-accent px-2.5 py-1 font-semibold text-k-on-accent">
                Add its route
              </button>
              <button onClick={close} className="aa-focus rounded-md border border-k-line px-2.5 py-1 text-k-muted">
                Not now
              </button>
            </div>
          </>
        )}
        {st.s === "applied" && (
          <p>
            <strong>{st.name}</strong> is on your trip
            {st.places ? ", built from its places and our experiences there" : ""}. Make it yours, then send it
            to an advisor.{" "}
            <button onClick={close} className="aa-focus text-k-muted underline">OK</button>
          </p>
        )}
        {st.s === "missing" && (
          <>
            <p>
              {st.tour?.title ? <strong>{st.tour.title}</strong> : "This tour"} isn&apos;t on the planner map yet.
              We&apos;ve set {st.tour?.country ?? intent.country ?? "its country"}
              {st.tour?.duration_days ?? intent.days ? ` and ${formatDays((st.tour?.duration_days ?? intent.days) as number)}` : ""} in the
              route finder — pick a route, or send this tour to an advisor to tailor it.
            </p>
            {st.tour && st.tour.days.length > 0 && (
              <ol className="mt-1.5 max-h-32 overflow-y-auto border-t border-k-line pt-1.5 text-[11px] text-k-muted">
                {st.tour.days.map((d) => (
                  <li key={d.day}>
                    <span className="font-semibold text-k-ink">Day {d.day}</span>
                    {d.title ? ` — ${d.title}` : ""}
                  </li>
                ))}
              </ol>
            )}
            <button onClick={close} className="aa-focus mt-1.5 text-k-muted underline">OK</button>
          </>
        )}
        {st.s === "error" && (
          <p>
            We couldn&apos;t load this tour&apos;s route. Try the route finder instead.{" "}
            <button onClick={close} className="aa-focus text-k-muted underline">OK</button>
          </p>
        )}
      </div>
    </div>
  );
}
