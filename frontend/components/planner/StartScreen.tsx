"use client";

// Planner screen 1 (AA-674 / Jira PR-11, laid out like Trip.com's Trip.Planner start form):
// where the traveller is heading, for how long, and what they enjoy — then either start from a
// real Adventure Asia route or build the trip themselves on the map.

import { useTrip } from "@/lib/useTrip";
import { ACTIVITIES, INTENSITIES } from "@/lib/types";
import ActivityIcon from "@/components/ActivityIcon";
import { Icon } from "./icons";

const DURATIONS = [3, 5, 7, 10, 14];

function label(v: string): string {
  return v.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

export default function StartScreen({ onRoutes, onCreate }: { onRoutes: () => void; onCreate: () => void }) {
  const { countries, intent, setIntent, setFilters, filters } = useTrip();

  const pickCountry = (country: string) => {
    setIntent({ ...intent, country });
    setFilters({ ...filters, country: country || undefined });
  };
  const pickActivity = (a: string) => {
    const activity = intent.activity === a ? undefined : a;
    setIntent({ ...intent, activity });
    setFilters({ ...filters, activity });
  };
  const pickIntensity = (v: string) => {
    const intensity_level = v || undefined;
    setIntent({ ...intent, intensity_level });
    setFilters({ ...filters, intensity_level });
  };

  return (
    <div className="px-5 py-5">
      <h1 className="flex items-center gap-2 text-2xl font-bold text-aa-ink">
        <Icon name="route" className="h-6 w-6 text-aa-gold" /> Trip Planner
      </h1>
      <p className="mt-1 text-sm text-aa-muted">
        Plan an Asia adventure from Adventure Asia&apos;s real tours, then make it yours.
      </p>

      <div className="mt-5 rounded-2xl border border-aa-line bg-white p-4 shadow-aa-sm">
        <div className="grid grid-cols-5 gap-3">
          <label className="col-span-3 rounded-xl border border-aa-line px-3 py-2.5 focus-within:border-aa-gold">
            <span className="flex items-center gap-1.5 text-sm font-semibold text-aa-ink">
              <Icon name="pin" className="h-4 w-4" /> Heading to
            </span>
            <select
              aria-label="Heading to"
              value={intent.country}
              onChange={(e) => pickCountry(e.target.value)}
              className="aa-focus mt-1 w-full bg-transparent text-sm text-aa-ink outline-none"
            >
              <option value="">Country</option>
              {countries.map((c) => (
                <option key={c.country} value={c.country}>
                  {c.country}
                </option>
              ))}
            </select>
          </label>
          <div className="col-span-2 rounded-xl border border-aa-line px-3 py-2.5">
            <span className="flex items-center gap-1.5 text-sm font-semibold text-aa-ink">
              <Icon name="calendar" className="h-4 w-4" /> Duration
            </span>
            <div className="mt-1 flex items-center gap-1">
              <input
                aria-label="Days"
                type="number"
                min={2}
                max={30}
                value={intent.days}
                onChange={(e) => setIntent({ ...intent, days: Math.max(2, Math.min(30, Number(e.target.value) || 2)) })}
                className="aa-focus w-12 bg-transparent text-sm text-aa-ink outline-none"
              />
              <span className="text-sm text-aa-muted">days</span>
            </div>
          </div>
        </div>

        <div className="mt-3 flex flex-wrap gap-1.5" aria-label="Duration presets">
          {DURATIONS.map((d) => (
            <button
              key={d}
              onClick={() => setIntent({ ...intent, days: d })}
              className={`aa-focus rounded-full border px-3 py-1 text-xs transition ${
                intent.days === d
                  ? "border-aa-gold bg-aa-gold-soft font-semibold text-aa-gold-dark"
                  : "border-aa-line text-aa-ink hover:border-aa-gold/60"
              }`}
            >
              {d} days
            </button>
          ))}
        </div>

        <details className="mt-3 group" open={!!intent.activity || !!intent.intensity_level}>
          <summary className="flex cursor-pointer list-none items-center gap-1.5 text-sm font-semibold text-aa-ink">
            <Icon name="heart" className="h-4 w-4" /> Preferences
            <Icon name="chevron" className="h-3.5 w-3.5 text-aa-muted transition group-open:rotate-180" />
          </summary>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {ACTIVITIES.map((a) => (
              <button
                key={a}
                onClick={() => pickActivity(a)}
                className={`aa-focus inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs transition ${
                  intent.activity === a
                    ? "border-aa-gold bg-aa-gold-soft font-semibold text-aa-gold-dark"
                    : "border-aa-line text-aa-ink hover:border-aa-gold/60"
                }`}
              >
                <ActivityIcon activity={a} size={14} /> {label(a)}
              </button>
            ))}
          </div>
          <select
            aria-label="Intensity"
            value={intent.intensity_level ?? ""}
            onChange={(e) => pickIntensity(e.target.value)}
            className="aa-focus mt-2 rounded-lg border border-aa-line px-2 py-1 text-xs text-aa-ink"
          >
            <option value="">Any intensity</option>
            {INTENSITIES.map((i) => (
              <option key={i} value={i}>
                {label(i)}
              </option>
            ))}
          </select>
        </details>

        <div className="mt-4 grid grid-cols-2 gap-3">
          <button
            onClick={onCreate}
            className="aa-focus rounded-xl border-2 border-aa-gold px-3 py-2.5 text-sm font-semibold text-aa-gold-dark transition hover:bg-aa-gold-soft"
          >
            Create it myself
          </button>
          <button
            onClick={onRoutes}
            disabled={!intent.country}
            title={intent.country ? "" : "Choose where you're heading first"}
            className="aa-focus inline-flex items-center justify-center gap-1.5 rounded-xl bg-aa-gold px-3 py-2.5 text-sm font-semibold text-white shadow-aa-sm transition hover:bg-aa-gold-dark disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Icon name="sparkle" className="h-4 w-4" /> Start from an AA route
          </button>
        </div>
      </div>

      <ul className="mt-5 space-y-2.5 text-xs text-aa-muted">
        <li className="flex gap-2">
          <Icon name="check" className="mt-0.5 h-3.5 w-3.5 shrink-0 text-aa-gold" />
          Every route is made of days from Adventure Asia&apos;s own tours — nothing an advisor can&apos;t sell.
        </li>
        <li className="flex gap-2">
          <Icon name="check" className="mt-0.5 h-3.5 w-3.5 shrink-0 text-aa-gold" />
          Pick activities at each stop, add a night or a free day, then send it to an advisor.
        </li>
      </ul>
    </div>
  );
}
