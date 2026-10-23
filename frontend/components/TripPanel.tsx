"use client";

import { useEffect, useState } from "react";
import { useTrip } from "@/lib/useTrip";
import { fetchTripLegs, formatLeg, nearestGateway, type RouteLeg } from "@/lib/mapbox";
import { isLine, planTrip, stretchSubtitle } from "@/lib/lines";
import { COUNTRY_GATEWAY } from "@/lib/types";
import RoutePlanner from "@/components/RoutePlanner";
import StopActivities from "@/components/StopActivities";
import { Icon } from "@/ui/Icon";
import AiLabel from "@/components/AiLabel";
import { parseNarration } from "@/lib/narration";
import { LEGAL_LINKS } from "@/lib/legal";
import { formatDays } from "@/lib/text";
import { reasonText, stopLabel, unavailableBanner } from "@/lib/availability";

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
      `${AA_ITINERARY_TEXT} Today's AI writing limit is reached, so the days it does not cover show ` +
      "only their places. Your advisor confirms every detail.",
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

export default function TripPanel() {
  const {
    itinerary,
    status,
    narration,
    narrating,
    narrationSource,
    suggestions,
    focusDestination,
    remove,
    reorder,
    narrate,
    send,
    coverage,
    customDays,
    addDay,
    removeDay,
    unavailableStops,
  } = useTrip();
  // AA-674: which day card has its activity list open.
  const [openStop, setOpenStop] = useState<string | null>(null);
  // Several activities of one AA tour day share a day (AA-674), so count distinct days.
  const tripDays = new Set(itinerary.map((d) => d.day)).size;
  const totalDays = tripDays + customDays.length;
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [showReg, setShowReg] = useState(false);
  const [reg, setReg] = useState({ name: "", phone: "", email: "", consent: false });
  const [sentMsg, setSentMsg] = useState<string | null>(null);
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
  // AA-847 slice 6d: the list subtitle of a line day names its two stops ("Huay Xai → Pak Beng · ≈ 88 km")
  const nameOfDestination = (id: string) => itinerary.find((x) => x.destination_id === id && !isLine(x))?.name ?? null;

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

  const onDrop = async (index: number) => {
    if (dragIndex === null || dragIndex === index) return;
    const ids = itinerary.map((d) => d.component_id);
    const [moved] = ids.splice(dragIndex, 1);
    ids.splice(index, 0, moved);
    setDragIndex(null);
    await reorder(ids);
  };

  const handleSend = async (withCustomer: boolean) => {
    setSentMsg(null);
    const res = await send(withCustomer ? reg : undefined);
    if (res.ok) {
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

      {itinerary.length === 0 ? (
        <div className="flex flex-1 flex-col justify-center px-5 py-8">
          <RoutePlanner />
          <div className="text-center">
            <Icon name="map" className="mx-auto h-8 w-8 text-k-accent-deep" />
            <p className="mt-3 text-sm font-semibold text-k-ink">
              Start building your trip
            </p>
            <p className="mt-1 text-xs leading-relaxed text-k-muted">
              Pin experiences from the map and watch your day-by-day itinerary
              take shape here.
            </p>
          </div>

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
                body: "Drag to reorder days, then send the draft to an AA advisor.",
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

            <RoutePlanner compact />

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
                <span aria-hidden className="mt-0.5 text-lg">✈️</span>
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

            <ol className="space-y-0">
              {itinerary.map((d, index) => (
                <li key={d.component_id}>
                  <div
                    draggable
                    onDragStart={() => setDragIndex(index)}
                    onDragOver={(e) => e.preventDefault()}
                    onDrop={() => onDrop(index)}
                    className={`group flex items-start gap-3 rounded-xl border bg-k-card p-3 shadow-aa-sm transition ${
                      dragIndex === index
                        ? "border-k-accent opacity-60"
                        : "border-k-line hover:border-k-accent-border"
                    }`}
                  >
                    <span className="mt-0.5 inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-k-accent text-xs font-bold text-k-on-accent">
                      {d.day}
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-semibold text-k-ink">
                        {isLine(d) && (
                          <span aria-label="Route" className="mr-1 text-k-accent-deep">〰</span>
                        )}
                        {d.name}
                      </p>
                      {isLine(d) && (
                        <p className="mt-0.5 text-xs text-k-muted">{stretchSubtitle(d, nameOfDestination)}</p>
                      )}
                      {d.rationale && (
                        <p className="mt-0.5 text-xs leading-relaxed text-k-muted">
                          {d.rationale}
                        </p>
                      )}
                      <div className="mt-1 flex gap-3">
                        {d.destination_id && (
                          <button
                            onClick={() => setOpenStop(openStop === d.component_id ? null : d.component_id)}
                            className="aa-focus text-[11px] font-medium text-k-accent-deep"
                          >
                            {openStop === d.component_id ? "Hide activities" : "Activities here"}
                          </button>
                        )}
                        <button
                          onClick={() => addDay("extra_night", d.component_id)}
                          className="aa-focus text-[11px] text-k-muted hover:text-k-ink"
                        >
                          + extra night
                        </button>
                      </div>
                      {openStop === d.component_id && d.destination_id && (
                        <StopActivities destinationId={d.destination_id} />
                      )}
                      {customDays
                        .filter((c) => c.kind === "extra_night" && c.after_component_id === d.component_id)
                        .map((c) => (
                          <p key={c.day_id} className="mt-1 text-[11px] text-k-body">
                            🌙 Extra night here <span className="text-k-muted">(your addition)</span>{" "}
                            <button onClick={() => removeDay(c.day_id)} className="aa-focus text-k-muted hover:text-k-danger">
                              remove
                            </button>
                          </p>
                        ))}
                    </div>
                    <button
                      onClick={() => remove(d.component_id)}
                      aria-label={`Remove day ${d.day}`}
                      className="aa-focus shrink-0 rounded-md px-1.5 py-0.5 text-xs text-k-muted opacity-0 transition group-hover:opacity-100 hover:text-k-danger"
                    >
                      Remove
                    </button>
                  </div>

                  {/* Travel connector to the next day. Road legs show real
                      driving distance/time; flight legs say so (no fake km). */}
                  {index < itinerary.length - 1 && legs.get(d.component_id) && (
                    <div className="flex flex-wrap items-center gap-x-1.5 py-1 pl-3.5 text-[11px] text-k-muted">
                      <span aria-hidden className="text-k-accent-deep">↓</span>
                      <span>{formatLeg(legs.get(d.component_id) as RouteLeg)}</span>
                    </div>
                  )}
                </li>
              ))}
            </ol>

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
                <span aria-hidden className="mt-0.5 text-lg">🛫</span>
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
              Drag day cards to reorder. Distances are rough estimates and
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
              {narration ? (
                <div className="mt-2.5 space-y-2.5">
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
            {!showReg ? (
              <button
                onClick={() => handleSend(false)}
                disabled={sent}
                className="aa-focus w-full rounded-xl bg-k-accent px-3 py-2.5 text-sm font-semibold text-k-on-accent shadow-aa-sm transition hover:brightness-95 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {sent ? "✓ Sent to advisor" : "Send to an advisor"}
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
                <input
                  placeholder="Email"
                  value={reg.email}
                  onChange={(e) => setReg({ ...reg, email: e.target.value })}
                  className="aa-focus w-full rounded-lg border border-k-line bg-k-card px-3 py-2 text-sm text-k-ink placeholder:text-k-muted"
                />
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
                  disabled={!reg.name || (!reg.phone && !reg.email) || !reg.consent}
                  className="aa-focus w-full rounded-xl bg-k-accent px-3 py-2.5 text-sm font-semibold text-k-on-accent shadow-aa-sm transition hover:brightness-95 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  Register &amp; send
                </button>
              </div>
            )}
            <p className="mt-2 text-center text-[11px] text-k-muted">
              An advisor confirms logistics before anything is booked.
            </p>
          </div>
        </>
      )}
    </div>
  );
}
