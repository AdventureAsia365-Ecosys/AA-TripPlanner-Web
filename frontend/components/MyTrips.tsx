"use client";
// AA-908 — My Trips: the signed-in traveller's trips (GET /api/me). "Open" puts the trip's ids into this browser's
// localStorage (lib/guestIds.ts) and opens the planner; the assembly Lambda lets the owning account read and edit it
// from any browser (the guest cookie of this browser is not needed). Below the trips: My Payments (AA-682 S5).
import { useCallback, useEffect, useState } from "react";
import { Badge, Button, ButtonLink, EmptyState, ErrorState } from "@/ui";
import { STORAGE_KEY } from "@/lib/guestIds";
import { signOut } from "@/lib/useAccount";
import MyPayments from "@/components/MyPayments";
import { meOutcome, tripFacts, tripStatusLabel, tripTitle, updatedLabel, type MyTrip } from "@/lib/myTrips";

type State =
  | { kind: "loading" }
  | { kind: "signed_out" }
  | { kind: "error" }
  | { kind: "ok"; email: string | null; trips: MyTrip[] };

function openTrip(t: MyTrip) {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ sessionId: t.session_id, tripId: t.trip_id }));
  } catch {
    /* ignore: the planner then starts a new trip */
  }
  window.location.assign("/plan");
}

export default function MyTrips() {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);
  const retry = useCallback(() => {
    setState({ kind: "loading" });
    setAttempt((n) => n + 1);
  }, []);

  useEffect(() => {
    let live = true;
    fetch("/api/me", { cache: "no-store" })
      .then(async (r) => {
        if (!live) return;
        const outcome = meOutcome(r.status);
        if (outcome !== "ok") return setState({ kind: outcome });
        const b = await r.json();
        if (live) setState({ kind: "ok", email: b?.email ?? null, trips: Array.isArray(b?.trips) ? b.trips : [] });
      })
      .catch(() => live && setState({ kind: "error" }));
    return () => {
      live = false;
    };
  }, [attempt]);

  if (state.kind === "loading") {
    return <p className="text-sm text-k-muted" role="status">Loading your trips…</p>;
  }
  if (state.kind === "signed_out") {
    return (
      <EmptyState title="Sign in to see your trips" icon="route">
        Trips you plan while signed in, and the trip in this browser when you sign in, are kept here.
        <div className="mt-4 flex justify-center">
          <ButtonLink href="/sign-in?next=/trips" variant="primary">Sign in</ButtonLink>
        </div>
      </EmptyState>
    );
  }
  if (state.kind === "error") {
    // The traveller may still be signed in (the list failed, not the session): keep a way out (S251).
    return (
      <ErrorState title="We could not load your trips">
        Please try again in a moment. You can also sign out of this browser.
        <div className="mt-4 flex flex-wrap justify-center gap-2" data-testid="my-trips-error-actions">
          <Button variant="primary" size="sm" onClick={retry}>Try again</Button>
          <Button variant="ghost" size="sm" onClick={() => signOut("/")} data-testid="sign-out">Sign out</Button>
        </div>
      </ErrorState>
    );
  }

  return (
    <section aria-labelledby="my-trips-title">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 id="my-trips-title" className="text-2xl font-semibold text-k-ink">My trips</h1>
          {state.email && <p className="mt-1 text-[13px] text-k-muted">Signed in as {state.email}</p>}
        </div>
        <div className="flex gap-2">
          <ButtonLink href="/plan" variant="secondary" size="sm">Plan a trip</ButtonLink>
          <Button variant="ghost" size="sm" onClick={() => signOut("/")} data-testid="sign-out">Sign out</Button>
        </div>
      </div>

      {state.trips.length === 0 ? (
        <div className="mt-6">
          <EmptyState title="No trips yet" icon="map">
            Start a trip on the map; it is saved here while you are signed in.
          </EmptyState>
        </div>
      ) : (
        <ul className="mt-6 space-y-3" data-testid="my-trips">
          {state.trips.map((t) => (
            <li key={t.trip_id}
              className="flex flex-col gap-3 rounded-2xl border border-k-line bg-k-card p-4 shadow-aa-sm sm:flex-row sm:items-center">
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={t.status === "sent" ? "success" : "neutral"}>{tripStatusLabel(t.status)}</Badge>
                  <span className="text-[11px] text-k-muted">{updatedLabel(t.updated_at)}</span>
                </div>
                <p className="mt-1.5 truncate text-sm font-semibold text-k-ink">{tripTitle(t)}</p>
                <p className="text-xs text-k-muted">{tripFacts(t)}</p>
              </div>
              <Button variant="primary" size="sm" onClick={() => openTrip(t)}>Open</Button>
            </li>
          ))}
        </ul>
      )}

      {/* AA-682 S5: payment requests from AA-Booking, signed-in only (this branch renders for a session). */}
      <MyPayments trips={state.trips} />
    </section>
  );
}
