"use client";
// AA-908 — My Trips: the signed-in traveller's trips (GET /api/me). "Open" puts the trip's ids into this browser's
// localStorage (lib/guestIds.ts) and opens the planner; the assembly Lambda lets the owning account read and edit it
// from any browser (the guest cookie of this browser is not needed). My Payments (AA-682 S5) was retired with the
// B2C half of AA-Booking (AA-1046, root ADR 0019: agencies are the only customers).
import { useCallback, useEffect, useState } from "react";
import { Badge, Button, ButtonLink, EmptyState, ErrorState } from "@/ui";
import { myTripsTargets, tenantOfPath, tenantRequestHeaders } from "@/lib/plannerTenant";
import { fetchAccountStatus, signOut } from "@/lib/useAccount";
import { meOutcome, signedOutWithoutMe, tripFacts, tripStatusLabel, tripTitle, updatedLabel, type MyTrip } from "@/lib/myTrips";

type State =
  | { kind: "loading" }
  | { kind: "signed_out" }
  | { kind: "error" }
  | { kind: "ok"; email: string | null; trips: MyTrip[] };

// AA-1038 slice 2: /trips?tenant={slug} (the "My trips" link of a tenant's planner) lists that tenant's trips and opens
// them in that tenant's planner with its own ids key; /trips alone is the bare (demo) planner, as before.
function openTrip(t: MyTrip, tenant: string | null) {
  const target = myTripsTargets(tenant);
  try {
    window.localStorage.setItem(target.idsKey, JSON.stringify({ sessionId: t.session_id, tripId: t.trip_id }));
  } catch {
    /* ignore: the planner then starts a new trip */
  }
  window.location.assign(target.planner);
}

export default function MyTrips() {
  const [state, setState] = useState<State>({ kind: "loading" });
  // Read in the browser only (the page shell is static). It never changes the first render (the loading line), so the
  // server render (null) and the hydration agree.
  const [tenant] = useState<string | null>(() =>
    typeof window === "undefined" ? null : tenantOfPath(`/trips${window.location.search}`),
  );
  const [attempt, setAttempt] = useState(0);
  const retry = useCallback(() => {
    setState({ kind: "loading" });
    setAttempt((n) => n + 1);
  }, []);

  useEffect(() => {
    let live = true;
    // The header already asked /api/auth/status (one shared request); a signed-out answer needs no /api/me call.
    fetchAccountStatus()
      .then((account) => {
        if (!live) return null;
        if (attempt === 0 && signedOutWithoutMe(account)) {
          setState({ kind: "signed_out" });
          return null;
        }
        return fetch("/api/me", { cache: "no-store", headers: tenantRequestHeaders(tenant) });
      })
      .then(async (r) => {
        if (!r || !live) return;
        const outcome = meOutcome(r.status);
        if (outcome !== "ok") return setState({ kind: outcome });
        const b = await r.json();
        if (live) setState({ kind: "ok", email: b?.email ?? null, trips: Array.isArray(b?.trips) ? b.trips : [] });
      })
      .catch(() => live && setState({ kind: "error" }));
    return () => {
      live = false;
    };
  }, [attempt, tenant]);

  if (state.kind === "loading") {
    return <p className="text-small text-k-muted" role="status">Loading your trips…</p>;
  }
  if (state.kind === "signed_out") {
    return (
      <EmptyState title="Sign in to see your trips" icon="route">
        Trips you plan while signed in, and the trip in this browser when you sign in, are kept here.
        <div className="mt-4 flex justify-center">
          <ButtonLink href={myTripsTargets(tenant).signIn} variant="primary">Sign in</ButtonLink>
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
      {/* AA-995: the page's h1 is the title band (app/trips/page.tsx); this is the list's heading. */}
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h2 id="my-trips-title" className="text-h3 text-k-ink">Saved trips</h2>
          {state.email && <p className="mt-1 truncate text-small text-k-muted">Signed in as {state.email}</p>}
        </div>
        <div className="flex gap-2">
          <ButtonLink href={myTripsTargets(tenant).planner} variant="secondary" size="sm">Plan a trip</ButtonLink>
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
              className="flex flex-col gap-4 rounded-card border border-k-line bg-k-card p-5 shadow-e1 sm:flex-row sm:items-center">
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={t.status === "sent" ? "success" : "neutral"}>{tripStatusLabel(t.status)}</Badge>
                  <span className="text-caption font-normal text-k-muted">{updatedLabel(t.updated_at)}</span>
                </div>
                <p className="mt-2 truncate text-body font-semibold text-k-ink">{tripTitle(t)}</p>
                <p className="text-small text-k-muted">{tripFacts(t)}</p>
              </div>
              <Button variant="primary" size="md" className="min-h-11 sm:min-w-28" onClick={() => openTrip(t, tenant)}>Open</Button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
