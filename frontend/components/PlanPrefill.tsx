"use client";

// AA-812 — arriving from a tour page ("Customise this trip" / "Ask an advisor"): load that tour's
// whole route and pin it with the existing apply-route flow. An empty trip gets it at once; a trip
// that already has stops asks first (apply-route merges, it never deletes). If the tour is not on
// the planner's Tour Graph yet, the route finder stays pre-filled with its country and length.

import { useEffect, useRef, useState } from "react";
import { useTrip } from "@/lib/useTrip";
import { fetchTourRoute } from "@/lib/api";
import type { RouteProposal } from "@/lib/types";

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
  | { s: "applied"; name: string }
  | { s: "missing" }
  | { s: "error" };

export default function PlanPrefill({ onApplied }: { onApplied?: () => void }) {
  const { intent, ready, itinerary, applyRoute } = useTrip();
  const [st, setSt] = useState<State>({ s: "idle" });
  const started = useRef(false);

  const apply = async (p: RouteProposal) => {
    if (await applyRoute(p)) {
      forgetTourParams();
      setSt({ s: "applied", name: p.segments[0]?.tour_name ?? "This tour" });
      onApplied?.();
    } else {
      setSt({ s: "error" });
    }
  };

  useEffect(() => {
    if ((!intent.tourId && !intent.sourceId) || !ready || started.current) return;
    started.current = true;
    fetchTourRoute(intent.tourId, intent.sourceId).then((p) => {
      if (p === "missing") setSt({ s: "missing" });
      else if (p === "error") setSt({ s: "error" });
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
  const box = "pointer-events-auto mx-auto max-w-md rounded-xl border border-aa-line bg-white px-3 py-2.5 text-xs text-aa-ink shadow-aa";

  return (
    <div className="pointer-events-none absolute inset-x-3 bottom-16 z-20" role="status">
      <div className={box}>
        {st.s === "confirm" && (
          <>
            <p>
              Add <strong>{st.p.segments[0]?.tour_name ?? "this tour"}</strong> ({st.p.total_days} days) to your
              current trip?
            </p>
            <div className="mt-2 flex gap-2">
              <button onClick={() => apply(st.p)} className="aa-focus rounded-md bg-aa-gold px-2.5 py-1 font-semibold text-white">
                Add its route
              </button>
              <button onClick={close} className="aa-focus rounded-md border border-aa-line px-2.5 py-1 text-aa-muted">
                Not now
              </button>
            </div>
          </>
        )}
        {st.s === "applied" && (
          <p>
            <strong>{st.name}</strong> is on your trip. Make it yours, then send it to an advisor.{" "}
            <button onClick={close} className="aa-focus text-aa-muted underline">OK</button>
          </p>
        )}
        {st.s === "missing" && (
          <p>
            This tour isn&apos;t on the planner map yet — pick a route below or send your ideas to an advisor.{" "}
            <button onClick={close} className="aa-focus text-aa-muted underline">OK</button>
          </p>
        )}
        {st.s === "error" && (
          <p>
            We couldn&apos;t load this tour&apos;s route. Try the route finder instead.{" "}
            <button onClick={close} className="aa-focus text-aa-muted underline">OK</button>
          </p>
        )}
      </div>
    </div>
  );
}
