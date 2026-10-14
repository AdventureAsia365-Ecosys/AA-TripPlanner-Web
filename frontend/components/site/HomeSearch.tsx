"use client";

// AA-812 — search-first hero form (trip.com pattern, AA flavour): where, how many days, how many
// travellers → "Plan my trip" opens the map planner with the route finder pre-filled.

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Icon, buttonClass } from "@/ui";

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

export default function HomeSearch({ countries }: { countries: CountryChoice[] }) {
  const router = useRouter();
  const [country, setCountry] = useState("");
  // Raw strings while typing; numbers only on blur / submit.
  const [daysRaw, setDaysRaw] = useState("7");
  const [travellersRaw, setTravellersRaw] = useState("2");
  const days = clampCount(daysRaw, 2, 30, 7);
  const travellers = clampCount(travellersRaw, 1, 20, 2);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    setDaysRaw(String(days));
    setTravellersRaw(String(travellers));
    router.push(planHref(country, days, travellers));
  };

  const field = "rounded-xl border border-k-line bg-k-card px-3 py-2.5 focus-within:border-k-accent";
  const label = "flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.06em] text-k-muted";
  const input = "mt-1 w-full bg-transparent text-[15px] font-medium text-k-ink outline-none";

  return (
    <form onSubmit={submit} aria-label="Plan a trip" className="rounded-2xl border border-k-line bg-k-card p-3 shadow-aa sm:p-4">
      <div className="grid gap-2.5 sm:grid-cols-[1.6fr_1fr_1fr_auto] sm:items-stretch">
        <label className={field}>
          <span className={label}><Icon name="pin" className="h-3.5 w-3.5" /> Where to</span>
          <select value={country} onChange={(e) => setCountry(e.target.value)} className={input} aria-label="Destination">
            <option value="">Anywhere in Asia</option>
            {countries.map((c) => (
              <option key={c.country} value={c.country}>
                {c.country} ({c.tour_count} tour{c.tour_count > 1 ? "s" : ""})
              </option>
            ))}
          </select>
        </label>
        <label className={field}>
          <span className={label}><Icon name="calendar" className="h-3.5 w-3.5" /> Days</span>
          <input type="number" inputMode="numeric" min={2} max={30} value={daysRaw} aria-label="Number of days"
                 onChange={(e) => setDaysRaw(e.target.value)} onBlur={() => setDaysRaw(String(days))} className={input} />
        </label>
        <label className={field}>
          <span className={label}><Icon name="users" className="h-3.5 w-3.5" /> Travellers</span>
          <input type="number" inputMode="numeric" min={1} max={20} value={travellersRaw} aria-label="Number of travellers"
                 onChange={(e) => setTravellersRaw(e.target.value)} onBlur={() => setTravellersRaw(String(travellers))} className={input} />
        </label>
        <button type="submit" className={buttonClass("primary", "lg", "w-full sm:w-auto")}>
          <Icon name="route" className="h-4 w-4" /> Plan my trip
        </button>
      </div>
      <div className="mt-2.5 flex flex-wrap items-center gap-1.5" aria-label="Trip length presets">
        {DAY_PRESETS.map((d) => (
          <button
            key={d}
            type="button"
            onClick={() => setDaysRaw(String(d))}
            aria-pressed={daysRaw === String(d)}
            className={`rounded-full border px-3 py-1 text-[12px] transition ${
              days === d
                ? "border-k-accent bg-k-accent-tint font-semibold text-k-accent-deep"
                : "border-k-line text-k-body hover:border-k-accent-border"
            }`}
          >
            {d} days
          </button>
        ))}
      </div>
    </form>
  );
}
