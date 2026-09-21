"use client";

import { useEffect, useState } from "react";
import { useTrip } from "@/lib/useTrip";
import { fetchRouteLegs, formatLeg, nearestGateway, type RouteLeg } from "@/lib/mapbox";
import { COUNTRY_GATEWAY } from "@/lib/types";

const LONG_TRIP_WARN_DAYS = 25;

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

interface NarrationBlock {
  day: number | null;
  text: string;
}

// Parse the LLM narration into per-day blocks. Handles the clean
// "Day N: ..." format and defensively strips any leftover markdown
// (**bold**, headings) from older/looser outputs.
function parseNarration(raw: string): NarrationBlock[] {
  const clean = raw.replace(/\*\*/g, "").replace(/^#+\s*/gm, "").trim();
  const blocks: NarrationBlock[] = [];
  // Split before each "Day N" marker (keep the marker with its text).
  const parts = clean.split(/(?=Day\s+\d+\s*[:\-–])/i).map((p) => p.trim()).filter(Boolean);
  if (parts.length === 0) return [{ day: null, text: clean }];
  for (const p of parts) {
    const m = p.match(/^Day\s+(\d+)\s*[:\-–]\s*(.*)$/is);
    if (m) {
      blocks.push({ day: Number(m[1]), text: m[2].trim() });
    } else {
      blocks.push({ day: null, text: p });
    }
  }
  return blocks;
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
  } = useTrip();
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [showReg, setShowReg] = useState(false);
  const [reg, setReg] = useState({ name: "", phone: "", email: "" });
  const [sentMsg, setSentMsg] = useState<string | null>(null);
  // Per-leg travel estimates between consecutive days (index i = day i -> i+1).
  const [legs, setLegs] = useState<RouteLeg[]>([]);
  // How many narration day-blocks are revealed so far — drives a light
  // "typing" reveal. The narration itself arrives in one response (mostly
  // AA's authored text, so instant); this just animates it in for polish.
  const [revealCount, setRevealCount] = useState(0);

  // Recompute travel legs whenever the ordered set of geocoded stops changes.
  // Keyed on the id+coord sequence so a pure re-render doesn't refetch.
  const legKey = itinerary
    .map((d) => `${d.component_id}:${d.lat ?? ""},${d.lng ?? ""}`)
    .join("|");
  useEffect(() => {
    const stops = itinerary
      .filter((d) => typeof d.lat === "number" && typeof d.lng === "number")
      .map((d) => [d.lng as number, d.lat as number] as [number, number]);
    if (stops.length < 2) {
      setLegs([]);
      return;
    }
    let cancelled = false;
    fetchRouteLegs(stops).then((result) => {
      if (!cancelled && result) setLegs(result);
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
    if (!narration) {
      setRevealCount(0);
      return;
    }
    setRevealCount(0);
    const total = parseNarration(narration).length;
    let n = 0;
    const id = setInterval(() => {
      n += 1;
      setRevealCount(n);
      if (n >= total) clearInterval(id);
    }, 180);
    return () => clearInterval(id);
  }, [narration]);

  // Total road distance — sum ONLY real road legs (flight legs have no
  // meaningful driving distance). Also count how many legs are flights so we
  // can label the total honestly ("road" km, not the whole journey).
  const totalRoadKm = legs.reduce((s, l) => s + (l.distanceKm ?? 0), 0);
  const hasFlightLeg = legs.some((l) => l.mode === "flight");

  // Arrival (fly into the gateway nearest the first stop) and departure (fly
  // out from the gateway nearest the last stop). Purely informative — an
  // advisor arranges the real flights.
  const geoStops = itinerary.filter(
    (d) => typeof d.lat === "number" && typeof d.lng === "number",
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

  const tooLong = itinerary.length > LONG_TRIP_WARN_DAYS;
  const sent = status === "sent";

  return (
    <div className="flex h-full flex-col">
      {/* Panel header */}
      <div className="flex items-baseline justify-between border-b border-aa-line px-4 py-3.5">
        <h2 className="text-base font-semibold text-aa-ink">Your trip</h2>
        {itinerary.length > 0 && (
          <div className="flex items-center gap-2">
            {totalRoadKm > 0 && (
              <span className="text-xs text-aa-muted">
                ~{Math.round(totalRoadKm)} km by road{hasFlightLeg ? " +" : ""}
              </span>
            )}
            <span className="rounded-full bg-aa-ink px-2.5 py-0.5 text-xs font-semibold text-white">
              {itinerary.length} day{itinerary.length > 1 ? "s" : ""}
            </span>
          </div>
        )}
      </div>

      {itinerary.length === 0 ? (
        <div className="flex flex-1 flex-col justify-center px-5 py-8">
          <div className="text-center">
            <span aria-hidden className="text-3xl">🗺️</span>
            <p className="mt-3 text-sm font-semibold text-aa-ink">
              Start building your trip
            </p>
            <p className="mt-1 text-xs leading-relaxed text-aa-muted">
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
                <span className="mt-0.5 inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-aa-gold-soft text-xs font-bold text-aa-gold-dark">
                  {step.n}
                </span>
                <div>
                  <p className="text-xs font-semibold text-aa-ink">
                    {step.title}
                  </p>
                  <p className="mt-0.5 text-[11px] leading-relaxed text-aa-muted">
                    {step.body}
                  </p>
                </div>
              </li>
            ))}
          </ol>

          <p className="mt-6 rounded-xl border border-aa-line bg-aa-gold-soft/40 px-3 py-2.5 text-center text-[11px] leading-relaxed text-aa-muted">
            💡 Tip: try the search box to find experiences by activity, like
            <span className="font-medium text-aa-ink"> “tea plantation trek”</span> or
            <span className="font-medium text-aa-ink"> “temple visit”</span>.
          </p>
        </div>
      ) : (
        <>
          <div className="flex-1 overflow-y-auto px-4 py-3">
            {/* Trip summary: at-a-glance overview of the draft. */}
            <div className="mb-3 grid grid-cols-3 gap-2">
              <div className="rounded-xl border border-aa-line bg-white p-2.5 text-center">
                <p className="text-base font-bold text-aa-ink">{itinerary.length}</p>
                <p className="text-[10px] uppercase tracking-wide text-aa-muted">
                  {itinerary.length > 1 ? "Days" : "Day"}
                </p>
              </div>
              <div className="rounded-xl border border-aa-line bg-white p-2.5 text-center">
                <p className="text-base font-bold text-aa-ink">
                  {countriesVisited.size || 1}
                </p>
                <p className="text-[10px] uppercase tracking-wide text-aa-muted">
                  {countriesVisited.size > 1 ? "Countries" : "Country"}
                </p>
              </div>
              <div className="rounded-xl border border-aa-line bg-white p-2.5 text-center">
                <p className="text-base font-bold text-aa-ink">
                  {totalRoadKm > 0 ? `~${Math.round(totalRoadKm)}` : "—"}
                </p>
                <p className="text-[10px] uppercase tracking-wide text-aa-muted">
                  km by road
                </p>
              </div>
            </div>

            {tooLong && (
              <div
                role="alert"
                className="mb-3 rounded-xl border border-aa-gold/40 bg-aa-gold-soft p-2.5 text-xs text-aa-gold-dark"
              >
                This trip is {itinerary.length} days — quite long. You can keep
                adding, but consider trimming for a smoother journey.
              </div>
            )}

            {/* Arrival: fly into the nearest gateway, then transfer to day 1. */}
            {arrival && (
              <div className="mb-2 flex items-start gap-3 rounded-xl border border-dashed border-aa-line bg-aa-sand/50 p-3">
                <span aria-hidden className="mt-0.5 text-lg">✈️</span>
                <div className="min-w-0 flex-1">
                  <p className="text-xs font-semibold text-aa-ink">
                    Getting there
                  </p>
                  <p className="mt-0.5 text-[11px] leading-relaxed text-aa-muted">
                    Fly into <span className="font-medium text-aa-ink">{arrival.gw.city} ({arrival.gw.iata})</span>
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
                    className={`group flex items-start gap-3 rounded-xl border bg-white p-3 shadow-aa-sm transition ${
                      dragIndex === index
                        ? "border-aa-gold opacity-60"
                        : "border-aa-line hover:border-aa-gold/40"
                    }`}
                  >
                    <span className="mt-0.5 inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-aa-gold text-xs font-bold text-white">
                      {d.day}
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-semibold text-aa-ink">
                        {d.name}
                      </p>
                      {d.rationale && (
                        <p className="mt-0.5 text-xs leading-relaxed text-aa-muted">
                          {d.rationale}
                        </p>
                      )}
                    </div>
                    <button
                      onClick={() => remove(d.component_id)}
                      aria-label={`Remove day ${d.day}`}
                      className="aa-focus shrink-0 rounded-md px-1.5 py-0.5 text-xs text-aa-muted opacity-0 transition group-hover:opacity-100 hover:text-red-600"
                    >
                      Remove
                    </button>
                  </div>

                  {/* Travel connector to the next day. Road legs show real
                      driving distance/time; flight legs say so (no fake km). */}
                  {index < itinerary.length - 1 && legs[index] && (
                    <div className="flex flex-wrap items-center gap-x-1.5 py-1 pl-3.5 text-[11px] text-aa-muted">
                      <span aria-hidden className="text-aa-gold">↓</span>
                      <span>{formatLeg(legs[index])}</span>
                    </div>
                  )}
                </li>
              ))}
            </ol>

            {/* Departure: transfer from the last stop to its nearest gateway. */}
            {departure && (
              <div className="mt-2 flex items-start gap-3 rounded-xl border border-dashed border-aa-line bg-aa-sand/50 p-3">
                <span aria-hidden className="mt-0.5 text-lg">🛫</span>
                <div className="min-w-0 flex-1">
                  <p className="text-xs font-semibold text-aa-ink">Heading home</p>
                  <p className="mt-0.5 text-[11px] leading-relaxed text-aa-muted">
                    After your last stop, {transferHint(departure.km)}
                    {departure.km >= 15 && <> (about {Math.round(departure.km)} km away)</>} back to{" "}
                    <span className="font-medium text-aa-ink">{departure.gw.city} ({departure.gw.iata})</span>
                    {" "}for your flight home.
                  </p>
                </div>
              </div>
            )}

            <p className="mt-2 px-1 text-[11px] text-aa-muted">
              Drag day cards to reorder. Distances are rough estimates and
              travel modes are suggestions — your advisor arranges the real
              transfers, boats and flights for an adventure route.
            </p>

            {/* Next-to-pin suggestions — places similar to what's pinned.
                Clicking opens the destination so the traveller picks a
                specific experience (never a whole-destination add). */}
            {suggestions.length > 0 && (
              <div className="mt-4 rounded-xl border border-aa-line bg-white p-3">
                <p className="text-xs font-semibold text-aa-ink">
                  You might also like
                </p>
                <p className="mt-0.5 text-[11px] text-aa-muted">
                  Based on what you&apos;ve pinned.
                </p>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {suggestions.map((s) => (
                    <button
                      key={s.id}
                      onClick={() => focusDestination(s.id)}
                      title={`${s.name} — ${s.why}`}
                      className="aa-focus rounded-full border border-aa-line px-2.5 py-1 text-xs text-aa-ink transition hover:border-aa-gold/60 hover:bg-aa-gold-soft"
                    >
                      + {s.name}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* AI narration — proposes connective day-by-day copy. The
                customer's chosen order is always respected. */}
            <div className="mt-4 rounded-xl border border-aa-line bg-white p-3">
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-1.5">
                  <p className="text-xs font-semibold text-aa-ink">
                    Day-by-day narration
                  </p>
                  {narration && narrationSource === "master" && (
                    <span
                      title="Straight from Adventure Asia's own tour itineraries"
                      className="rounded-full bg-aa-gold-soft px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide text-aa-gold-dark"
                    >
                      AA itinerary
                    </span>
                  )}
                  {narration && narrationSource === "mixed" && (
                    <span
                      title="Mostly AA's own itinerary text, with AI filling any gaps"
                      className="rounded-full bg-aa-gold-soft px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide text-aa-gold-dark"
                    >
                      AA + AI
                    </span>
                  )}
                </div>
                <button
                  onClick={() => narrate("compose")}
                  disabled={narrating}
                  className="aa-focus shrink-0 rounded-lg border border-aa-gold px-2.5 py-1 text-xs font-semibold text-aa-gold-dark transition hover:bg-aa-gold-soft disabled:opacity-50"
                >
                  {narrating
                    ? "Composing…"
                    : narration
                      ? "Regenerate"
                      : "✨ Compose"}
                </button>
              </div>
              {narration ? (
                <div className="mt-2.5 space-y-2.5">
                  {narrationBlocks.slice(0, revealCount).map((b, i) => (
                    <div key={i} className="flex gap-2.5">
                      {b.day !== null && (
                        <span className="mt-0.5 inline-flex h-5 shrink-0 items-center rounded-full bg-aa-gold-soft px-2 text-[10px] font-bold uppercase tracking-wide text-aa-gold-dark">
                          Day {b.day}
                        </span>
                      )}
                      <p className="text-xs leading-relaxed text-aa-ink-soft">
                        {b.text}
                      </p>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="mt-2 text-[11px] text-aa-muted">
                  We&apos;ll pull each day&apos;s story from Adventure
                  Asia&apos;s own tour itineraries, in your chosen order.
                </p>
              )}
            </div>
          </div>

          {/* Sticky CTA footer */}
          <div className="border-t border-aa-line bg-white px-4 py-3">
            {sentMsg && (
              <p
                className={`mb-2 rounded-lg px-2.5 py-1.5 text-xs font-medium ${
                  sent
                    ? "bg-aa-gold-soft text-aa-gold-dark"
                    : "bg-red-50 text-red-600"
                }`}
              >
                {sentMsg}
              </p>
            )}
            {!showReg ? (
              <button
                onClick={() => handleSend(false)}
                disabled={sent}
                className="aa-focus w-full rounded-xl bg-aa-gold px-3 py-2.5 text-sm font-semibold text-white shadow-aa-sm transition hover:bg-aa-gold-dark disabled:cursor-not-allowed disabled:opacity-50"
              >
                {sent ? "✓ Sent to advisor" : "Send to an advisor"}
              </button>
            ) : (
              <div className="space-y-2">
                <p className="text-xs text-aa-muted">
                  Add your details so an advisor can turn this draft into a
                  bookable itinerary.
                </p>
                <input
                  placeholder="Full name"
                  value={reg.name}
                  onChange={(e) => setReg({ ...reg, name: e.target.value })}
                  className="aa-focus w-full rounded-lg border border-aa-line px-3 py-2 text-sm text-aa-ink placeholder:text-aa-muted"
                />
                <input
                  placeholder="Phone"
                  value={reg.phone}
                  onChange={(e) => setReg({ ...reg, phone: e.target.value })}
                  className="aa-focus w-full rounded-lg border border-aa-line px-3 py-2 text-sm text-aa-ink placeholder:text-aa-muted"
                />
                <input
                  placeholder="Email"
                  value={reg.email}
                  onChange={(e) => setReg({ ...reg, email: e.target.value })}
                  className="aa-focus w-full rounded-lg border border-aa-line px-3 py-2 text-sm text-aa-ink placeholder:text-aa-muted"
                />
                <button
                  onClick={() => handleSend(true)}
                  disabled={!reg.name || (!reg.phone && !reg.email)}
                  className="aa-focus w-full rounded-xl bg-aa-gold px-3 py-2.5 text-sm font-semibold text-white shadow-aa-sm transition hover:bg-aa-gold-dark disabled:cursor-not-allowed disabled:opacity-50"
                >
                  Register &amp; send
                </button>
              </div>
            )}
            <p className="mt-2 text-center text-[11px] text-aa-muted">
              An advisor confirms logistics before anything is booked.
            </p>
          </div>
        </>
      )}
    </div>
  );
}
