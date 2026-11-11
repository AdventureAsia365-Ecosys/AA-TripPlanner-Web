"use client";

import { useEffect, useState } from "react";
import { useTrip } from "@/lib/useTrip";
import { fetchTripLegs, formatLeg, nearestGateway, type RouteLeg } from "@/lib/mapbox";
import { isLine, planTrip } from "@/lib/lines";
import { COUNTRY_GATEWAY } from "@/lib/types";
import RoutePlanner from "@/components/RoutePlanner";
import DayList from "@/components/DayList";
import ShareTrip from "@/components/ShareTrip";
import { PlaceIcon } from "@/components/ActivityIcon";
import { Icon } from "@/ui/Icon";
import { EmptyState, ErrorState, LoadingState } from "@/ui/States";
import { Button } from "@/ui/Button";
import { AiNotice } from "@/ui/AiNotice";
import AiLabel from "@/components/AiLabel";
import { aiNotice, narrationNoticeVariant } from "@/lib/aiNotices";
import { parseNarration } from "@/lib/narration";
import { LEGAL_LINKS, TERMS_AI_PATH } from "@/lib/legal";
import { formatDays } from "@/lib/text";
import { reasonText, stopLabel, unavailableBanner } from "@/lib/availability";
import { useAccount } from "@/lib/useAccount";
import { ADVISOR_HANDOFF_DEMO, DEMO_NOTICE } from "@/lib/advisorDemo";
import LeadForm from "@/components/LeadForm";
import { isAgencyPlanner } from "@/lib/leadForm";
import { plannerAgency, plannerTenant } from "@/lib/plannerTenant";

const DAY_KIND_LABEL: Record<string, string> = {
  extra_night: "Extra night",
  free_day: "Free day",
  extend_end: "Extra day at the end",
};

const LONG_TRIP_WARN_DAYS = 25;

// AA-819 (D2): the narration badge per `source` of POST /trip/{id}/narrate. The visible label itself
// says AI (not only the tap-to-open explanation): AA master text is the CIS writer model's output.
// `basic` (daily AI budget spent, no AA day at all: neutral name lines) has no AI text and no badge;
// `master_basic` is the same fallback with some AA days in it.
const AA_ITINERARY_LABEL = "AA itinerary · Adventure Asia AI";
const AA_ITINERARY_SHORT = "AA itinerary · AA AI";
const AA_ITINERARY_TEXT =
  "Adventure Asia's own itinerary text for these tour days, written by AI from our tour notes and " +
  "checked by automated quality checks.";
const NARRATION_LABEL: Record<string, { label: string; shortLabel: string; explanation: string }> = {
  master: {
    label: AA_ITINERARY_LABEL,
    shortLabel: AA_ITINERARY_SHORT,
    explanation: `${AA_ITINERARY_TEXT} Your advisor confirms every detail.`,
  },
  master_basic: {
    label: AA_ITINERARY_LABEL,
    shortLabel: AA_ITINERARY_SHORT,
    explanation:
      `${AA_ITINERARY_TEXT} AI writing is not available right now (paused, or today's limit is reached), ` +
      "so the days it does not cover show only their places. Your advisor confirms every detail.",
  },
  mixed: {
    label: "AA itinerary + Adventure Asia AI",
    shortLabel: "AA itinerary + AA AI",
    explanation:
      "Mostly Adventure Asia's own itinerary text, written by AI from our tour notes and checked by " +
      "automated quality checks; AI also wrote the days it did not cover. Your advisor confirms every detail.",
  },
  llm: {
    label: "Adventure Asia AI narration",
    shortLabel: "AA AI narration",
    explanation: "Written by AI from the tour descriptions. Your advisor confirms every detail.",
  },
};

// Map each gateway IATA back to its country, for a "countries visited" count.
const IATA_TO_COUNTRY: Record<string, string> = Object.entries(
  COUNTRY_GATEWAY,
).reduce<Record<string, string>>((acc, [country, gws]) => {
  for (const gw of gws) acc[gw.iata] = country;
  return acc;
}, {});

// Transfer hint from the gateway airport to the stop, tuned for adventure
// trips (overland / boat / domestic flight rather than city cabs).
function transferHint(km: number): string {
  if (km < 15) return "you're right by the airport";
  if (km < 120) return "a private 4WD or boat transfer";
  if (km < 400) return "a scenic overland drive or a domestic hop";
  return "a domestic flight, then an overland transfer";
}

export default function TripPanel({ onShowOnMap }: { onShowOnMap?: () => void } = {}) {
  const {
    itinerary,
    status,
    narration,
    narrating,
    narrationSource,
    narrationNotice,
    suggestions,
    focusDestination,
    remove,
    narrate,
    send,
    sendLead,
    coverage,
    customDays,
    addDay,
    removeDay,
    unavailableStops,
    ready,
    loadFailed,
    retryLoad,
    undoOrder,
    undoReorder,
    reorderFailed,
  } = useTrip();
  // Several activities of one AA tour day share a day (AA-674), so count distinct days.
  const tripDays = new Set(itinerary.map((d) => d.day)).size;
  const totalDays = tripDays + customDays.length;
  const [showReg, setShowReg] = useState(false);
  const [reg, setReg] = useState({ name: "", phone: "", email: "", consent: false });
  // AA-908: a signed-in traveller's email comes from the account (verified by the code), never typed. The
  // assembly Lambda reads it from the access token; the field below only shows it.
  const account = useAccount();
  const accountEmail = account.signedIn ? account.email : null;
  const [sentMsg, setSentMsg] = useState<string | null>(null);
  const [demoNotice, setDemoNotice] = useState(false);
  // AA-1038 slice 3: on an agency tenant's planner the button opens the trip request form (lead.submitted.v1).
  const [showLead, setShowLead] = useState(false);
  const agencySlug = plannerTenant();
  const agency = isAgencyPlanner(agencySlug) ? plannerAgency() : null;
  const agencyName = agency?.name || "your travel agency";
  // Per-leg travel estimates between consecutive stops, keyed by the component the leg leaves from. AA-847 slice
  // 6d: a river / road / trail day is a "feature" leg along the line between its two stops (lib/lines.ts planTrip).
  const [legs, setLegs] = useState<Map<string, RouteLeg>>(new Map());
  // How many narration day-blocks are revealed so far — drives a light
  // "typing" reveal. The narration itself arrives in one response (mostly
  // AA's authored text, so instant); this just animates it in for polish.
  // AA-906: tagged with the narration it counts for, so a new narration starts at 0 by derivation
  // (React Compiler lint, no setState in an effect body).
  const [reveal, setReveal] = useState<{ text: string; n: number }>({ text: "", n: 0 });
  const revealCount = reveal.text === narration ? reveal.n : 0;

  // Recompute travel legs whenever the ordered set of geocoded stops changes.
  // Keyed on the id+coord sequence so a pure re-render doesn't refetch.
  const legKey = itinerary
    .map((d) => `${d.component_id}:${d.lat ?? ""},${d.lng ?? ""}:${d.segment_basis ?? ""}`)
    .join("|");
  // AA-906 (React Compiler lint, no setState in an effect body): a trip that drops below two stops
  // clears its legs during render ("adjust state when a prop changes", once per new key); otherwise the
  // previous legs stay drawn until the new ones land, as before.
  const [legKeySeen, setLegKeySeen] = useState(legKey);
  if (legKey !== legKeySeen) {
    setLegKeySeen(legKey);
    if (planTrip(itinerary).stops.length < 2) setLegs(new Map());
  }
  useEffect(() => {
    const plan = planTrip(itinerary);
    if (plan.stops.length < 2) return;
    let cancelled = false;
    fetchTripLegs(plan).then((result) => {
      if (!cancelled) setLegs(new Map(result.map((leg, i) => [plan.stops[i].componentId, leg])));
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [legKey]);

  // Progressive reveal of narration blocks. Resets when the narration text
  // changes, then ticks up to the block count.
  const narrationBlocks = narration ? parseNarration(narration) : [];
  useEffect(() => {
    if (!narration) return;
    const total = parseNarration(narration).length;
    let n = 0;
    const id = setInterval(() => {
      n += 1;
      setReveal({ text: narration, n });
      if (n >= total) clearInterval(id);
    }, 180);
    return () => clearInterval(id);
  }, [narration]);

  // Total road distance — sum ONLY real road legs (flight legs have no
  // meaningful driving distance). Also count how many legs are flights so we
  // can label the total honestly ("road" km, not the whole journey).
  const allLegs = [...legs.values()].flatMap((l) => [l, ...(l.joins ?? [])]);
  const totalRoadKm = allLegs.reduce((s, l) => s + (l.mode === "road" ? l.distanceKm ?? 0 : 0), 0);
  const hasFlightLeg = allLegs.some((l) => l.mode === "flight");

  // Arrival (fly into the gateway nearest the first stop) and departure (fly
  // out from the gateway nearest the last stop). Purely informative — an
  // advisor arranges the real flights.
  const geoStops = itinerary.filter(
    (d) => typeof d.lat === "number" && typeof d.lng === "number" && !isLine(d),
  );
  const arrival =
    geoStops.length > 0
      ? nearestGateway([geoStops[0].lng as number, geoStops[0].lat as number])
      : null;
  const lastStop = geoStops[geoStops.length - 1];
  const departure =
    geoStops.length > 0
      ? nearestGateway([lastStop.lng as number, lastStop.lat as number])
      : null;

  // Countries visited: infer from each stop's nearest gateway country.
  const countriesVisited = new Set(
    geoStops
      .map((d) => {
        const g = nearestGateway([d.lng as number, d.lat as number]);
        return g ? IATA_TO_COUNTRY[g.gw.iata] : undefined;
      })
      .filter(Boolean) as string[],
  );

  const handleSend = async (withCustomer: boolean) => {
    setSentMsg(null);
    if (isAgencyPlanner(agencySlug)) {
      setShowLead(true);
      return;
    }
    // AA-1034 R0 (B2B-only model): the hand-off is a demo; nothing is sent and no contact details are asked for.
    if (ADVISOR_HANDOFF_DEMO) {
      setShowReg(false);
      setDemoNotice(true);
      return;
    }
    const res = await send(withCustomer ? { ...reg, email: accountEmail ?? reg.email } : undefined);
    if (res.demo) {
      setShowReg(false);
      setDemoNotice(true);
    } else if (res.ok) {
      setShowReg(false);
      setSentMsg("Sent to an advisor. They'll be in touch shortly.");
    } else if (res.needsRegistration) {
      setShowReg(true);
    } else {
      setSentMsg("Something went wrong. Please try again.");
    }
  };

  const tooLong = tripDays > LONG_TRIP_WARN_DAYS;
  const sent = status === "sent";

  return (
    <div className="flex h-full flex-col">
      {/* Panel header */}
      <div className="flex items-baseline justify-between border-b border-k-line px-4 py-3.5">
        <h2 className="text-base font-semibold text-k-ink">Your trip</h2>
        {itinerary.length > 0 && (
          <div className="flex items-center gap-2">
            {totalRoadKm > 0 && (
              <span className="text-xs text-k-muted">
                ~{Math.round(totalRoadKm)} km by road{hasFlightLeg ? " +" : ""}
              </span>
            )}
            <span className="rounded-full bg-k-ink px-2.5 py-0.5 text-xs font-semibold text-k-bg">
              {formatDays(totalDays)}
            </span>
          </div>
        )}
      </div>

      {/* AA-986 L6 (ADR 0015 decision 5): saved stops whose tour AA no longer serves. They are not on the map
          or in the day list; they stay here, greyed, until the traveller removes them or AA serves them again. */}
      {unavailableStops.length > 0 && (
        <div className="border-b border-k-line px-4 py-3">
          <div
            role="status"
            className="rounded-xl border border-k-accent-border bg-k-accent-tint p-2.5 text-xs text-k-accent-deep"
          >
            {unavailableBanner(unavailableStops)}
          </div>
          <ul className="mt-2 space-y-1.5" aria-label="Stops no longer available">
            {unavailableStops.map((s) => (
              <li
                key={s.component_id}
                className="flex items-start gap-3 rounded-xl border border-dashed border-k-line bg-k-bg p-2.5"
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm text-k-muted line-through">{stopLabel(s)}</p>
                  <p className="mt-0.5 text-[11px] text-k-muted">
                    {reasonText(s.reason)}
                    {s.destination_name && s.name && s.destination_name !== s.name && <> · {s.destination_name}</>}
                  </p>
                </div>
                <button
                  onClick={() => remove(s.component_id)}
                  aria-label={`Remove ${stopLabel(s)}`}
                  className="aa-focus shrink-0 rounded-md px-1.5 py-0.5 text-xs text-k-muted hover:text-k-danger"
                >
                  Remove
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* AA-1041 (R7 item 7): kit loading and error states while the saved trip is read. */}
      {!ready ? (
        <div className="px-4 py-4" data-testid="trip-loading">
          <LoadingState label="Loading your trip" rows={3} compact />
        </div>
      ) : loadFailed && itinerary.length === 0 ? (
        <div className="px-4 py-4" data-testid="trip-load-error">
          <ErrorState title="We couldn't load your trip">
            <p>Your trip is saved. Check your connection and try again.</p>
            <Button variant="secondary" size="sm" className="mt-3" onClick={retryLoad}>
              <Icon name="refresh" className="h-3.5 w-3.5" />
              Try again
            </Button>
          </ErrorState>
        </div>
      ) : itinerary.length === 0 ? (
        <div className="flex flex-1 flex-col justify-center px-5 py-8" data-testid="trip-empty">
          <RoutePlanner />
          <EmptyState title="Start building your trip" icon="map">
            Pin experiences from the map and watch your day-by-day itinerary take shape here.
          </EmptyState>

          <ol className="mt-6 space-y-3">
            {[
              {
                n: "1",
                title: "Choose a country",
                body: "Use the filters top-left to focus the map on a destination.",
              },
              {
                n: "2",
                title: "Pin what interests you",
                body: "Click a map marker, then add an experience to your trip.",
              },
              {
                n: "3",
                title: "Reorder & send",
                body: "Drag the handles to reorder days and stops, then send the draft to an advisor.",
              },
            ].map((step) => (
              <li key={step.n} className="flex items-start gap-3">
                <span className="mt-0.5 inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-k-accent-tint text-xs font-bold text-k-accent-deep">
                  {step.n}
                </span>
                <div>
                  <p className="text-xs font-semibold text-k-ink">
                    {step.title}
                  </p>
                  <p className="mt-0.5 text-[11px] leading-relaxed text-k-muted">
                    {step.body}
                  </p>
                </div>
              </li>
            ))}
          </ol>

          <p className="mt-6 rounded-xl border border-k-line bg-k-accent-tint px-3 py-2.5 text-center text-[11px] leading-relaxed text-k-muted">
            <Icon name="info" className="mr-1 inline h-3.5 w-3.5 align-[-2px] text-k-accent-deep" />
            Tip: try the search box to find experiences by activity, like
            <span className="font-medium text-k-ink"> “tea plantation trek”</span> or
            <span className="font-medium text-k-ink"> “temple visit”</span>.
          </p>
        </div>
      ) : (
        <>
          <div className="flex-1 overflow-y-auto px-4 py-3">
            {/* Trip summary: at-a-glance overview of the draft. */}
            <div className="mb-3 grid grid-cols-3 gap-2">
              <div className="rounded-xl border border-k-line bg-k-card p-2.5 text-center">
                <p className="text-base font-bold text-k-ink">{totalDays}</p>
                <p className="text-[10px] uppercase tracking-wide text-k-muted">
                  {totalDays > 1 ? "Days" : "Day"}
                </p>
              </div>
              <div className="rounded-xl border border-k-line bg-k-card p-2.5 text-center">
                <p className="text-base font-bold text-k-ink">
                  {countriesVisited.size || 1}
                </p>
                <p className="text-[10px] uppercase tracking-wide text-k-muted">
                  {countriesVisited.size > 1 ? "Countries" : "Country"}
                </p>
              </div>
              <div className="rounded-xl border border-k-line bg-k-card p-2.5 text-center">
                <p className="text-base font-bold text-k-ink">
                  {totalRoadKm > 0 ? `~${Math.round(totalRoadKm)}` : "—"}
                </p>
                <p className="text-[10px] uppercase tracking-wide text-k-muted">
                  km by road
                </p>
              </div>
            </div>

            {/* AA-1041 (R7 item 6): a read-only link to the trip, and its print view. */}
            <ShareTrip />

            <RoutePlanner compact />

            {/* AA-1041 (R7 item 4): the last reorder can be undone; a reorder that could not be saved says so. */}
            {reorderFailed && (
              <p role="alert" data-testid="reorder-failed"
                className="mb-2 rounded-lg border border-k-danger-border bg-k-danger-soft px-2.5 py-1.5 text-xs text-k-danger">
                We couldn&apos;t save the new order, so your days are back as they were. Please try again.
              </p>
            )}
            {undoOrder && !reorderFailed && (
              <div role="status" data-testid="reorder-saved"
                className="tp-no-print mb-2 flex items-center justify-between gap-2 rounded-lg border border-k-line bg-k-card px-2.5 py-1.5 text-xs text-k-body">
                <span>New order saved.</span>
                <button type="button" onClick={() => void undoReorder()} data-testid="reorder-undo"
                  className="aa-focus inline-flex items-center gap-1 font-semibold text-k-accent-deep">
                  <Icon name="undo" className="h-3.5 w-3.5" />
                  Undo
                </button>
              </div>
            )}

            {/* AA-674: the real Adventure Asia tours this trip is built from. */}
            {coverage && coverage.legs.length > 0 && (
              <div
                className={`mb-3 rounded-xl border p-3 ${
                  coverage.coverable ? "border-k-line bg-k-card" : "border-k-danger-border bg-k-danger-soft"
                }`}
                aria-label="Built from Adventure Asia tours"
              >
                <p className="text-xs font-semibold text-k-ink">
                  {coverage.coverable
                    ? `Built from ${coverage.tours} Adventure Asia tour${coverage.tours > 1 ? "s" : ""}`
                    : "Part of this trip isn't covered by an AA tour"}
                </p>
                <ul className="mt-1.5 space-y-1">
                  {coverage.legs.map((leg, i) => (
                    <li key={`${leg.tour_id}-${leg.day_from}-${i}`} className="text-[11px] text-k-body">
                      <span className="font-medium text-k-ink">{leg.tour_name ?? "AA tour"}</span>
                      {" "}· tour day {leg.day_from}
                      {leg.day_to !== leg.day_from ? `–${leg.day_to}` : ""}
                      {coverage.transfers[i] && (
                        <span className={coverage.transfers[i].ok ? "text-k-muted" : "text-k-danger"}>
                          {" "}→ {coverage.transfers[i].ok ? "transfer" : "no AA link"}
                          {coverage.transfers[i].km != null && ` (${Math.round(coverage.transfers[i].km as number)} km)`}
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
                {coverage.gaps.map((g) => (
                  <p key={`${g.from_pin}-${g.to_pin}`} role="alert" className="mt-1.5 text-[11px] text-k-danger">
                    {g.from_name} → {g.to_name}: {g.reason}. Your advisor can suggest a bridge, or pick
                    places on the same route.
                  </p>
                ))}
              </div>
            )}

            {tooLong && (
              <div
                role="alert"
                className="mb-3 rounded-xl border border-k-accent-border bg-k-accent-tint p-2.5 text-xs text-k-accent-deep"
              >
                This trip is {formatDays(tripDays)} — quite long. You can keep
                adding, but consider trimming for a smoother journey.
              </div>
            )}

            {/* Arrival: fly into the nearest gateway, then transfer to day 1. */}
            {arrival && (
              <div className="mb-2 flex items-start gap-3 rounded-xl border border-dashed border-k-line bg-k-bg p-3">
                <span className="mt-0.5 text-k-accent-deep">
                  <PlaceIcon kind="flight" size={18} />
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-xs font-semibold text-k-ink">
                    Getting there
                  </p>
                  <p className="mt-0.5 text-[11px] leading-relaxed text-k-muted">
                    Fly into <span className="font-medium text-k-ink">{arrival.gw.city} ({arrival.gw.iata})</span>
                    , then {transferHint(arrival.km)}
                    {arrival.km >= 15 && <> (about {Math.round(arrival.km)} km away)</>} to your
                    first stop.
                  </p>
                </div>
              </div>
            )}

            {/* AA-1041 (R7 slice 1): days with photos and icons, map sync, drag / keyboard reorder. */}
            <DayList legs={legs} onShowOnMap={onShowOnMap} />

            {/* AA-674 / PR-11 point 3: days added on top of the AA tours. */}
            <div className="mt-2 rounded-xl border border-dashed border-k-line p-2.5">
              {customDays
                .filter((c) => c.kind !== "extra_night")
                .map((c) => (
                  <p key={c.day_id} className="mb-1 text-[11px] text-k-body">
                    ➕ {DAY_KIND_LABEL[c.kind]} <span className="text-k-muted">(your addition, not part of a tour)</span>{" "}
                    <button onClick={() => removeDay(c.day_id)} className="aa-focus text-k-muted hover:text-k-danger">
                      remove
                    </button>
                  </p>
                ))}
              <div className="flex gap-2">
                <button
                  onClick={() => addDay("free_day")}
                  className="aa-focus rounded-md border border-k-line px-2 py-0.5 text-[11px] text-k-ink hover:border-k-accent-border"
                >
                  + free day
                </button>
                <button
                  onClick={() => addDay("extend_end")}
                  className="aa-focus rounded-md border border-k-line px-2 py-0.5 text-[11px] text-k-ink hover:border-k-accent-border"
                >
                  + extra day at the end
                </button>
              </div>
            </div>

            {/* Departure: transfer from the last stop to its nearest gateway. */}
            {departure && (
              <div className="mt-2 flex items-start gap-3 rounded-xl border border-dashed border-k-line bg-k-bg p-3">
                <span className="mt-0.5 text-k-accent-deep">
                  <PlaceIcon kind="flight" size={18} />
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-xs font-semibold text-k-ink">Heading home</p>
                  <p className="mt-0.5 text-[11px] leading-relaxed text-k-muted">
                    After your last stop, {transferHint(departure.km)}
                    {departure.km >= 15 && <> (about {Math.round(departure.km)} km away)</>} back to{" "}
                    <span className="font-medium text-k-ink">{departure.gw.city} ({departure.gw.iata})</span>
                    {" "}for your flight home.
                  </p>
                </div>
              </div>
            )}

            <p className="mt-2 px-1 text-[11px] text-k-muted">
              Drag a handle, or use the arrow keys on it, to reorder days and stops. Distances are rough estimates and
              travel modes are suggestions — your advisor arranges the real
              transfers, boats and flights for an adventure route.
            </p>

            {/* Next-to-pin suggestions — places similar to what's pinned.
                Clicking opens the destination so the traveller picks a
                specific experience (never a whole-destination add). */}
            {suggestions.length > 0 && (
              <div className="mt-4 rounded-xl border border-k-line bg-k-card p-3">
                <p className="text-xs font-semibold text-k-ink">
                  You might also like
                </p>
                <p className="mt-0.5 text-[11px] text-k-muted">
                  {suggestions.some((x) => (x.tour_count ?? 0) > 0)
                    ? "Where Adventure Asia tours go next from your last stop."
                    : "Based on what you've pinned."}
                </p>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {suggestions.map((s) => (
                    <button
                      key={s.id}
                      onClick={() => focusDestination(s.id)}
                      title={`${s.name} — ${s.why}`}
                      className="aa-focus rounded-full border border-k-line px-2.5 py-1 text-xs text-k-ink transition hover:border-k-accent-border hover:bg-k-accent-tint"
                    >
                      + {s.name}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* AI narration — proposes connective day-by-day copy. The
                customer's chosen order is always respected. */}
            <div className="mt-4 rounded-xl border border-k-line bg-k-card p-3">
              <div className="flex items-center justify-between gap-2">
                <p className="text-xs font-semibold text-k-ink">
                  Day-by-day narration
                </p>
                <button
                  onClick={() => narrate("compose")}
                  disabled={narrating}
                  className="aa-focus shrink-0 rounded-lg border border-k-accent px-2.5 py-1 text-xs font-semibold text-k-accent-deep transition hover:bg-k-accent-tint disabled:opacity-50"
                >
                  {narrating
                    ? "Composing…"
                    : narration
                      ? "Regenerate"
                      : "✨ Compose"}
                </button>
              </div>
              {/* AA-819: each narration state labelled honestly. On its own row and opening in
                  the flow: the panel scrolls, so a floating explanation would be clipped and
                  scroll it sideways (S239 review). */}
              {narration && narrationSource && NARRATION_LABEL[narrationSource] && (
                <AiLabel
                  className="mt-1.5"
                  placement="inline"
                  label={NARRATION_LABEL[narrationSource].label}
                  shortLabel={NARRATION_LABEL[narrationSource].shortLabel}
                  explanation={NARRATION_LABEL[narrationSource].explanation}
                  variant={narrationSource === "llm" ? "outline" : "solid"}
                />
              )}
              {/* AA-642 S0: a narration made without AI says why, in one calm line (no spinner, no error). */}
              {narrationNotice && (
                <p role="status" className="mt-1.5 text-[11px] leading-snug text-k-muted">
                  {aiNotice(narrationNotice)}
                </p>
              )}
              {narration ? (
                // AA-1040: machine-readable mark of the narration (EU AI Act Art. 50(2)); `data-ai-source` is the
                // narrate answer's `source` (master = AA itinerary text written earlier by AI, llm/mixed = on request).
                <div className="mt-2.5 space-y-2.5" data-testid="narration-text" data-ai-generated="true"
                  data-ai-source={narrationSource ?? undefined}>
                  {narrationBlocks.slice(0, revealCount).map((b, i) => (
                    <div key={i} className="flex gap-2.5">
                      {b.day !== null && (
                        <span className="mt-0.5 inline-flex h-5 shrink-0 items-center rounded-full bg-k-accent-tint px-2 text-[10px] font-bold uppercase tracking-wide text-k-accent-deep">
                          Day {b.day}
                        </span>
                      )}
                      <div className="min-w-0 text-xs leading-relaxed text-k-body">
                        {b.title && (
                          <p className="font-semibold text-k-ink">{b.title}</p>
                        )}
                        {b.text && <p className="whitespace-pre-line">{b.text}</p>}
                      </div>
                    </div>
                  ))}
                  {/* AA-1033: the traveller notice under the narration (AI-written or AI-assisted day text). AA-1040:
                      the narration variant when AI wrote days on this request (llm / mixed), Art. 50(1). */}
                  <AiNotice size="sm" variant={narrationNoticeVariant(narrationSource)} termsHref={TERMS_AI_PATH}
                    testId="narration-ai-notice" />
                </div>
              ) : (
                <p className="mt-2 text-[11px] text-k-muted">
                  We&apos;ll pull each day&apos;s story from Adventure
                  Asia&apos;s own tour itineraries, in your chosen order.
                </p>
              )}
            </div>
          </div>

          {/* Sticky CTA footer */}
          <div className="border-t border-k-line bg-k-card px-4 py-3">
            {demoNotice && (
              <div
                role="status"
                data-testid="advisor-demo-notice"
                className="mb-2 flex gap-2 rounded-lg border border-k-accent-border bg-k-accent-tint px-2.5 py-2 text-xs text-k-accent-deep"
              >
                <Icon name="info" className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                <div>
                  <p className="font-semibold">{DEMO_NOTICE.title}</p>
                  <p className="mt-0.5 text-k-body">{DEMO_NOTICE.body}</p>
                </div>
              </div>
            )}
            {sentMsg && (
              <p
                className={`mb-2 rounded-lg px-2.5 py-1.5 text-xs font-medium ${
                  sent
                    ? "bg-k-accent-tint text-k-accent-deep"
                    : "bg-k-danger-soft text-k-danger"
                }`}
              >
                {sentMsg}
              </p>
            )}
            {showLead && !sent ? (
              <LeadForm
                agency={agencyName}
                onSend={sendLead}
                onDone={(text) => {
                  setShowLead(false);
                  setSentMsg(text);
                }}
              />
            ) : !showReg ? (
              <button
                onClick={() => handleSend(false)}
                disabled={sent}
                className="aa-focus w-full rounded-xl bg-k-accent px-3 py-2.5 text-sm font-semibold text-k-on-accent shadow-aa-sm transition hover:brightness-95 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {sent ? "✓ Sent to advisor" : agency ? `Send to ${agencyName}` : "Send to an advisor"}
              </button>
            ) : (
              <div className="space-y-2">
                <p className="text-xs text-k-muted">
                  Add your details so an advisor can turn this draft into a
                  bookable itinerary.
                </p>
                <input
                  placeholder="Full name"
                  value={reg.name}
                  onChange={(e) => setReg({ ...reg, name: e.target.value })}
                  className="aa-focus w-full rounded-lg border border-k-line bg-k-card px-3 py-2 text-sm text-k-ink placeholder:text-k-muted"
                />
                <input
                  placeholder="Phone"
                  value={reg.phone}
                  onChange={(e) => setReg({ ...reg, phone: e.target.value })}
                  className="aa-focus w-full rounded-lg border border-k-line bg-k-card px-3 py-2 text-sm text-k-ink placeholder:text-k-muted"
                />
                {accountEmail ? (
                  <div>
                    <input
                      aria-label="Email"
                      value={accountEmail}
                      readOnly
                      data-testid="account-email"
                      className="w-full cursor-not-allowed rounded-lg border border-k-line bg-k-bg px-3 py-2 text-sm text-k-muted"
                    />
                    <p className="mt-1 text-[11px] text-k-muted">
                      Your signed-in email. Your advisor sees it as confirmed.
                    </p>
                  </div>
                ) : (
                  <input
                    placeholder="Email"
                    value={reg.email}
                    onChange={(e) => setReg({ ...reg, email: e.target.value })}
                    className="aa-focus w-full rounded-lg border border-k-line bg-k-card px-3 py-2 text-sm text-k-ink placeholder:text-k-muted"
                  />
                )}
                {/* AA-676: explicit contact consent travels with the hand-off to AA-Booking. */}
                <label className="flex items-start gap-2 text-xs text-k-muted">
                  <input
                    type="checkbox"
                    checked={reg.consent}
                    onChange={(e) => setReg({ ...reg, consent: e.target.checked })}
                    className="aa-focus mt-0.5"
                  />
                  <span>An Adventure Asia advisor may contact me about this trip.</span>
                </label>
                {/* AA-886: the legal pages sit next to the only PII form; a new tab keeps the form as typed. */}
                <p className="text-[11px] text-k-muted" data-testid="trip-legal-links">
                  How we use your details:{" "}
                  {LEGAL_LINKS.map((l, i) => (
                    <span key={l.href}>
                      {i > 0 && " · "}
                      <a
                        href={l.href}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="aa-focus font-semibold text-k-ink underline underline-offset-2 hover:text-k-accent-deep"
                      >
                        {l.label}
                      </a>
                    </span>
                  ))}
                  {" "}(draft)
                </p>
                <button
                  onClick={() => handleSend(true)}
                  disabled={!reg.name || (!reg.phone && !reg.email && !accountEmail) || !reg.consent}
                  className="aa-focus w-full rounded-xl bg-k-accent px-3 py-2.5 text-sm font-semibold text-k-on-accent shadow-aa-sm transition hover:brightness-95 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  Register &amp; send
                </button>
              </div>
            )}
            <p className="mt-2 text-center text-[11px] text-k-muted">
              An advisor confirms logistics before anything is booked.
            </p>
            {/* BO-8 (root ADR 0019 b8): "Powered by Adventure Asia" only when the agency's tp.branding flag is on
                (off by default); small, nofollow, never inside an AI text block. */}
            {agency?.brandingBadge && (
              <p className="mt-1 text-center text-[10px] text-k-muted" data-testid="powered-by">
                Powered by{" "}
                <a href="https://adventure.asia/" rel="nofollow noopener" target="_blank" className="aa-focus underline">
                  Adventure Asia
                </a>
              </p>
            )}
          </div>
        </>
      )}
    </div>
  );
}
