"use client";

// AA-812 — search-first hero form (trip.com pattern, AA flavour): where, how many days, how many
// travellers → "Plan my trip" opens the map planner with the route finder pre-filled.
// AA-995 (spec §4.1 row 3) — the white search card that overlaps the hero: "Where to" select, "How long"
// chips 5/7/10/14 (plus "Other" for any length 2–30, so no length the old number field allowed is lost),
// a travellers stepper and the gold "Plan my trip" button. One row from md, stacked below. Targets >= 44 px.

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Icon, buttonClass } from "@/ui";
import { formatDays } from "@/lib/text";

const DAY_PRESETS = [5, 7, 10, 14];

export interface CountryChoice {
  country: string;
  tour_count: number;
}

export function planHref(country: string, days: number, travellers: number): string {
  const p = new URLSearchParams();
  if (country) p.set("country", country);
  p.set("days", String(days));
  p.set("travellers", String(travellers));
  return `/plan?${p}`;
}

// Clamp a typed number only when the visitor is done (blur / submit): clamping every keystroke
// turns "14" into 30 (the "1" becomes 2, then "24" becomes 30).
export function clampCount(raw: string, min: number, max: number, fallback: number): number {
  const n = Math.round(Number(raw));
  if (!raw.trim() || !Number.isFinite(n)) return fallback;
  return Math.max(min, Math.min(max, n));
}

const TRAVELLERS_MIN = 1;
const TRAVELLERS_MAX = 20;

export default function HomeSearch({ countries }: { countries: CountryChoice[] }) {
  const router = useRouter();
  const [country, setCountry] = useState("");
  // Raw strings while typing; numbers only on blur / submit.
  const [daysRaw, setDaysRaw] = useState("7");
  const [travellersRaw, setTravellersRaw] = useState("2");
  const days = clampCount(daysRaw, 2, 30, 7);
  const travellers = clampCount(travellersRaw, TRAVELLERS_MIN, TRAVELLERS_MAX, 2);
  const preset = DAY_PRESETS.includes(days) && daysRaw === String(days);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    setDaysRaw(String(days));
    setTravellersRaw(String(travellers));
    router.push(planHref(country, days, travellers));
  };

  const field = "flex min-w-0 flex-col gap-1.5 px-1 py-2 md:px-5 md:py-1";
  const label = "flex items-center gap-1.5 text-kicker uppercase text-k-muted";
  const chip = (on: boolean) =>
    `inline-flex min-h-11 shrink-0 items-center gap-1 rounded-pill border px-3.5 text-small transition-colors duration-fast ease-std ${
      on
        ? "border-k-accent bg-k-accent-tint font-semibold text-k-accent-deep"
        : "border-k-line text-k-body hover:border-k-accent-border"
    }`;
  const step = "inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-full border border-k-line text-k-ink transition-colors duration-fast ease-std hover:border-k-accent-border disabled:opacity-40";

  return (
    <form
      onSubmit={submit}
      aria-label="Plan a trip"
      className="rounded-panel border border-k-line bg-k-card p-4 shadow-e3 md:p-3"
    >
      <div className="grid gap-2 md:grid-cols-[minmax(0,1.1fr)_minmax(0,1.6fr)_auto_auto] md:items-center md:divide-x md:divide-k-line">
        <label className={field}>
          <span className={label}><Icon name="pin" className="h-3.5 w-3.5" /> Where to</span>
          <select
            value={country}
            onChange={(e) => setCountry(e.target.value)}
            aria-label="Destination"
            className="min-h-11 w-full min-w-0 max-w-full rounded-tile border border-k-line bg-k-card px-3 text-body font-medium text-k-ink md:border-0 md:px-0"
          >
            <option value="">Anywhere in Asia</option>
            {countries.map((c) => (
              <option key={c.country} value={c.country}>
                {c.country} ({c.tour_count} tour{c.tour_count > 1 ? "s" : ""})
              </option>
            ))}
          </select>
        </label>

        <fieldset className={field}>
          <legend className="sr-only">How long</legend>
          <span aria-hidden className={label}><Icon name="calendar" className="h-3.5 w-3.5" /> How long</span>
          {/* Own horizontal scroll on a narrow screen; the page never scrolls sideways. */}
          <div className="tp-noscrollbar -m-1.5 flex items-center gap-1.5 overflow-x-auto p-1.5">
            {DAY_PRESETS.map((d) => (
              <button key={d} type="button" onClick={() => setDaysRaw(String(d))} aria-pressed={preset && days === d} className={chip(preset && days === d)}>
                {formatDays(d)}
              </button>
            ))}
            <label className={`${chip(!preset)} cursor-text`}>
              <span className="sr-only">Other length in days</span>
              <input
                type="number"
                inputMode="numeric"
                min={2}
                max={30}
                value={daysRaw}
                onChange={(e) => setDaysRaw(e.target.value)}
                onBlur={() => setDaysRaw(String(days))}
                className="w-10 bg-transparent text-center text-small font-semibold text-k-ink outline-none"
              />
              <span aria-hidden>days</span>
            </label>
          </div>
        </fieldset>

        <div className={field} role="group" aria-labelledby="home-travellers-label">
          <span id="home-travellers-label" className={label}><Icon name="users" className="h-3.5 w-3.5" /> Travellers</span>
          <div className="flex items-center gap-2">
            <button type="button" className={step} aria-label="Fewer travellers" disabled={travellers <= TRAVELLERS_MIN}
                    onClick={() => setTravellersRaw(String(Math.max(TRAVELLERS_MIN, travellers - 1)))}>
              <Icon name="minus" className="h-4 w-4" />
            </button>
            <input
              type="number"
              inputMode="numeric"
              min={TRAVELLERS_MIN}
              max={TRAVELLERS_MAX}
              value={travellersRaw}
              aria-label="Number of travellers"
              onChange={(e) => setTravellersRaw(e.target.value)}
              onBlur={() => setTravellersRaw(String(travellers))}
              className="w-10 bg-transparent text-center text-body font-semibold text-k-ink outline-none"
            />
            <button type="button" className={step} aria-label="More travellers" disabled={travellers >= TRAVELLERS_MAX}
                    onClick={() => setTravellersRaw(String(Math.min(TRAVELLERS_MAX, travellers + 1)))}>
              <Icon name="plus" className="h-4 w-4" />
            </button>
          </div>
        </div>

        <div className="pt-2 md:py-1 md:pl-4">
          <button type="submit" className={buttonClass("primary", "lg", "min-h-[52px] w-full md:w-auto")}>
            <Icon name="route" className="h-4 w-4" /> Plan my trip
          </button>
        </div>
      </div>
    </form>
  );
}
