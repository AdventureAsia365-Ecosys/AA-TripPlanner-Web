"use client";

// AA-1041 (R7 item 6): the read-only share / print view of a trip (`/plan/shared#…`, `/{slug}/plan/shared#…`).
// The fragment carries component ids only (lib/shareTrip.ts); the stops, their places and photos come from the CIS
// catalog (GET /browse/stops). Nothing here writes: no trip call, no cookie read, nothing stored. Kit loading, empty
// and error states (item 7); the agency tenant's brand tokens colour it (the tenant page adds its <style>).
// "Print or save as PDF" is the browser's print (app/globals.css @media print hides the controls), and the
// calendar file (.ics) needs a start date the traveller picks.
import { useEffect, useMemo, useState } from "react";
import { fetchStops, type StopDetail } from "@/lib/api";
import { assignDays, groupByDay } from "@/lib/itineraryOrder";
import { kindLabel, placeKind } from "@/lib/placeIcon";
import { decodeShare, parseStartDate, tripIcs, type SharedTrip } from "@/lib/shareTrip";
import { formatDays } from "@/lib/text";
import { PlaceIcon } from "@/components/ActivityIcon";
import StopPhoto from "@/components/StopPhoto";
import { Button, ButtonLink } from "@/ui/Button";
import { Icon } from "@/ui/Icon";
import { EmptyState, ErrorState, LoadingState } from "@/ui/States";

const ADDED_LABEL = { extra_night: "Extra night", free_day: "Free day", extend_end: "Extra day at the end" } as const;

type Loaded = { key: string; stops: Map<string, StopDetail>; failed: boolean };

function readFragment(): SharedTrip {
  return typeof window === "undefined" ? { ids: [], added: [] } : decodeShare(window.location.hash);
}

export default function SharedTripView({ planHref, agencyName }: { planHref: string; agencyName: string | null }) {
  // The fragment is read once on the client (the server never sees it); a hash change reads it again.
  const [shared, setShared] = useState<SharedTrip | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [start, setStart] = useState("");

  useEffect(() => {
    const read = () => setShared(readFragment());
    const id = window.setTimeout(read, 0);
    window.addEventListener("hashchange", read);
    return () => {
      window.clearTimeout(id);
      window.removeEventListener("hashchange", read);
    };
  }, []);

  const key = shared ? `${shared.ids.join(",")}#${attempt}` : "";
  useEffect(() => {
    if (!shared || shared.ids.length === 0) return;
    let active = true;
    fetchStops(shared.ids).then(({ stops, failed }) => {
      if (active) setLoaded({ key, stops, failed });
    });
    return () => {
      active = false;
    };
  }, [shared, key]);

  // The served stops in the link's order, numbered into days like the planner (lib/itineraryOrder.ts assignDays).
  const days = useMemo(() => {
    if (!shared || !loaded || loaded.key !== key) return null;
    const ordered = shared.ids
      .map((id) => loaded.stops.get(id))
      .filter((s): s is StopDetail => s !== undefined)
      .map((s) => ({ ...s, component_id: s.id }));
    return groupByDay(assignDays(ordered));
  }, [shared, loaded, key]);

  const title = agencyName ? `Trip plan · ${agencyName}` : "Trip plan";
  const missing = shared && days ? shared.ids.length - days.reduce((n, d) => n + d.stops.length, 0) : 0;
  const added = shared?.added ?? [];
  const totalDays = (days?.length ?? 0) + added.length;
  const startDate = parseStartDate(start);

  const downloadIcs = () => {
    if (!days || !startDate) return;
    const calDays = days.map((d) => ({
      day: d.day,
      title: d.stops.map((s) => s.name).join(" · "),
      description: d.stops.map((s) => [s.name, s.destination_name].filter(Boolean).join(", ")).join("\n"),
    }));
    const blob = new Blob([tripIcs(calDays, startDate, title)], { type: "text/calendar;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "trip.ics";
    a.click();
    URL.revokeObjectURL(a.href);
  };

  let body: React.ReactNode;
  if (!shared || (shared.ids.length > 0 && (!loaded || loaded.key !== key))) {
    body = <LoadingState label="Loading the trip" rows={4} />;
  } else if (shared.ids.length === 0) {
    body = (
      <EmptyState title="This link has no trip in it" icon="route">
        Ask for the link again, or plan a trip of your own.
      </EmptyState>
    );
  } else if (loaded && loaded.failed && (days?.length ?? 0) === 0) {
    body = (
      <ErrorState title="We couldn't load this trip">
        <p>Check your connection and try again.</p>
        <Button variant="secondary" size="sm" className="mt-3" onClick={() => setAttempt((n) => n + 1)}>
          <Icon name="refresh" className="h-3.5 w-3.5" />
          Try again
        </Button>
      </ErrorState>
    );
  } else if (!days || days.length === 0) {
    body = (
      <EmptyState title="These places are not offered any more" icon="route">
        The tours in this trip are not available now. Plan a new trip to see what is.
      </EmptyState>
    );
  } else {
    body = (
      <>
        <ol className="space-y-3" data-testid="shared-days">
          {days.map((d) => {
            const photo = d.stops.map((s) => s.photo_url_small).find(Boolean) ?? null;
            const dayKind = placeKind(d.stops[0]);
            return (
              <li key={d.stops[0].id} className="tp-print-day rounded-card border border-k-line bg-k-card p-4">
                <div className="flex items-start gap-4">
                  <StopPhoto src={photo} kind={dayKind} />
                  <div className="min-w-0 flex-1">
                    <p className="text-caption font-semibold uppercase tracking-wide text-k-accent-deep">Day {d.day}</p>
                    <ul className="mt-1 space-y-1.5">
                      {d.stops.map((s) => {
                        const kind = placeKind(s);
                        return (
                          <li key={s.id} className="flex items-start gap-2">
                            <span className="mt-0.5 text-k-accent-deep">
                              <PlaceIcon kind={kind} size={16} title={kindLabel(kind)} />
                            </span>
                            <span className="min-w-0">
                              <span className="block text-body font-semibold text-k-ink">{s.name}</span>
                              {s.destination_name && (
                                <span className="block text-small text-k-muted">
                                  {[s.destination_name, s.country].filter(Boolean).join(", ")}
                                </span>
                              )}
                            </span>
                          </li>
                        );
                      })}
                    </ul>
                  </div>
                </div>
              </li>
            );
          })}
        </ol>
        {added.length > 0 && (
          <ul className="mt-3 space-y-1 rounded-card border border-dashed border-k-line p-3 text-small text-k-body">
            {added.map((a, i) => (
              <li key={i}>
                {ADDED_LABEL[a.kind]}
                {a.kind === "extra_night" && a.afterIndex !== null && days.flatMap((d) => d.stops)[a.afterIndex]
                  ? ` after ${days.flatMap((d) => d.stops)[a.afterIndex].name}`
                  : ""}{" "}
                <span className="text-k-muted">(an addition, not part of a tour)</span>
              </li>
            ))}
          </ul>
        )}
        {missing > 0 && (
          <p role="status" className="mt-3 text-small text-k-muted">
            {missing === 1 ? "One place" : `${missing} places`} in this link {missing === 1 ? "is" : "are"} not offered
            any more and {missing === 1 ? "is" : "are"} left out.
          </p>
        )}
      </>
    );
  }

  return (
    <main className="tp-print-sheet mx-auto max-w-3xl px-4 py-6 sm:px-6 sm:py-10" data-testid="shared-trip">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-caption font-semibold uppercase tracking-[0.18em] text-k-accent-deep">Shared trip</p>
          <h1 className="mt-1 text-h1 font-semibold text-k-ink">{title}</h1>
          {totalDays > 0 && <p className="mt-1 text-small text-k-muted">{formatDays(totalDays)}</p>}
        </div>
        <div className="tp-no-print flex flex-wrap gap-2">
          <Button variant="secondary" size="sm" onClick={() => window.print()} disabled={!days || days.length === 0}>
            <Icon name="print" className="h-3.5 w-3.5" />
            Print or save as PDF
          </Button>
          <ButtonLink href={planHref} variant="ghost" size="sm">
            <Icon name="map" className="h-3.5 w-3.5" />
            Plan your own trip
          </ButtonLink>
        </div>
      </div>

      {days && days.length > 0 && (
        <div className="tp-no-print mt-4 flex flex-wrap items-end gap-2 rounded-card border border-k-line bg-k-card p-3">
          <label className="text-small text-k-body">
            <span className="block text-caption font-semibold text-k-muted">First day</span>
            <input
              type="date"
              value={start}
              onChange={(e) => setStart(e.target.value)}
              className="aa-focus mt-1 rounded-lg border border-k-line bg-k-card px-2 py-1 text-small text-k-ink"
            />
          </label>
          <Button variant="ghost" size="sm" onClick={downloadIcs} disabled={!startDate} data-ics="1">
            <Icon name="download" className="h-3.5 w-3.5" />
            Add to calendar (.ics)
          </Button>
        </div>
      )}

      <div className="mt-6">{body}</div>

      <p className="mt-6 text-small text-k-muted">
        A draft plan built from real tours. Places and timings are confirmed by the travel advisor before anything is
        booked.
      </p>
    </main>
  );
}
