"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useTrip } from "@/lib/useTrip";
import {
  ACTIVITIES,
  INTENSITIES,
  REGION_OF,
  REGION_ORDER,
} from "@/lib/types";
import type { CountryOption } from "@/lib/types";
import ActivityIcon from "./ActivityIcon";

const MONTHS = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

const SEARCH_DEBOUNCE_MS = 400;

function label(v: string): string {
  return v.replace(/_/g, " ");
}

// Group countries by region (adventure.asia's own top-level grouping) so the
// country-first dropdown reads like the official site.
function groupByRegion(countries: CountryOption[]) {
  const groups: Record<string, CountryOption[]> = {};
  for (const c of countries) {
    const region = REGION_OF[c.country] ?? "Other";
    (groups[region] ??= []).push(c);
  }
  return REGION_ORDER.map((region) => ({
    region,
    items: (groups[region] ?? []).sort((a, b) =>
      a.country.localeCompare(b.country),
    ),
  })).filter((g) => g.items.length > 0);
}

export default function FilterChips() {
  const {
    filters,
    setFilters,
    runSearch,
    clearSearch,
    searching,
    searchResults,
    countries,
    setPreviewCountry,
  } = useTrip();
  const [open, setOpen] = useState(true);
  const [text, setText] = useState("");
  // Country picker is a custom listbox (not a native <select>) so hovering a
  // country can preview-highlight it on the map — native <option> elements
  // don't emit reliable mouseenter events.
  const [countryOpen, setCountryOpen] = useState(false);
  const countryBoxRef = useRef<HTMLDivElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>();

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      if (text.trim()) runSearch(text);
      else clearSearch();
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [text, runSearch, clearSearch]);

  // Close the country listbox on an outside click, and drop any hover-preview
  // highlight so the map returns to the selected country.
  useEffect(() => {
    if (!countryOpen) return;
    const onDown = (e: MouseEvent) => {
      if (!countryBoxRef.current?.contains(e.target as Node)) {
        setCountryOpen(false);
        setPreviewCountry(null);
      }
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [countryOpen, setPreviewCountry]);

  const grouped = useMemo(() => groupByRegion(countries), [countries]);

  const toggleActivity = (a: string) =>
    setFilters({ ...filters, activity: filters.activity === a ? undefined : a });

  const activeCount =
    (filters.activity ? 1 : 0) +
    (filters.intensity_level ? 1 : 0) +
    (filters.season ? 1 : 0) +
    (filters.country ? 1 : 0);

  const resultCount = searchResults?.length ?? null;
  // Activity is a secondary refinement — invite the user to pick a country
  // first (product requirement: country → then activity).
  const activityStepEnabled = Boolean(filters.country);

  return (
    <div className="pointer-events-none absolute left-4 right-4 top-4 z-10 flex justify-start">
      <div className="pointer-events-auto w-full max-w-2xl rounded-2xl border border-aa-line bg-white/95 shadow-aa backdrop-blur">
        {/* Search + toggle row */}
        <div className="flex items-center gap-2 px-3 py-2.5">
          <span className="flex h-9 flex-1 items-center gap-2 rounded-xl border border-aa-line bg-aa-sand px-3">
            {searching ? (
              <svg width="16" height="16" viewBox="0 0 24 24" className="animate-spin text-aa-gold">
                <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="3" fill="none" strokeDasharray="42" strokeLinecap="round" />
              </svg>
            ) : (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" className="text-aa-muted">
                <path
                  d="M21 21l-4.3-4.3M11 19a8 8 0 100-16 8 8 0 000 16z"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                />
              </svg>
            )}
            <input
              aria-label="Search experiences"
              placeholder="Search experiences — e.g. sunrise trek, street food, temples"
              value={text}
              onChange={(e) => setText(e.target.value)}
              className="aa-focus w-full bg-transparent text-sm text-aa-ink placeholder:text-aa-muted focus:outline-none"
            />
            {text && (
              <button
                onClick={() => setText("")}
                aria-label="Clear search"
                className="aa-focus rounded p-0.5 text-aa-muted hover:text-aa-ink"
              >
                ✕
              </button>
            )}
          </span>
          <button
            onClick={() => setOpen((o) => !o)}
            aria-expanded={open}
            className="aa-focus flex h-9 items-center gap-1.5 rounded-xl border border-aa-line px-3 text-sm font-medium text-aa-ink hover:bg-aa-sand"
          >
            Filters
            {activeCount > 0 && (
              <span className="inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-aa-gold px-1 text-[11px] font-semibold text-white">
                {activeCount}
              </span>
            )}
          </button>
        </div>

        {/* Search result banner */}
        {resultCount !== null && (
          <div className="flex items-center justify-between border-t border-aa-line bg-aa-gold-soft px-3 py-1.5 text-xs text-aa-gold-dark">
            <span>
              {resultCount === 0
                ? "No matches — try different words or clear a filter."
                : `${resultCount} place${resultCount > 1 ? "s" : ""} match “${text.trim()}”, best first.`}
            </span>
            <button
              onClick={() => setText("")}
              className="aa-focus font-semibold underline-offset-2 hover:underline"
            >
              Back to map
            </button>
          </div>
        )}

        {open && (
          <div className="aa-animate-in border-t border-aa-line px-3 py-3">
            {/* STEP 1 — Country (primary filter). Country first, then activity. */}
            <div className="flex flex-wrap items-center gap-2">
              <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-aa-ink text-[11px] font-bold text-white">
                1
              </span>
              <span className="text-xs font-semibold text-aa-ink">Country</span>
              <div ref={countryBoxRef} className="relative min-w-[12rem] flex-1">
                <button
                  type="button"
                  aria-haspopup="listbox"
                  aria-expanded={countryOpen}
                  onClick={() => {
                    setCountryOpen((o) => !o);
                    setPreviewCountry(null);
                  }}
                  className="aa-focus flex w-full items-center justify-between rounded-lg border border-aa-line bg-white px-2.5 py-1.5 text-xs text-aa-ink"
                >
                  <span className={filters.country ? "" : "text-aa-muted"}>
                    {filters.country ?? "All countries"}
                  </span>
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" className="text-aa-muted">
                    <path d="M6 9l6 6 6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </button>

                {countryOpen && (
                  <div
                    role="listbox"
                    aria-label="Country"
                    onMouseLeave={() => setPreviewCountry(null)}
                    className="aa-animate-in absolute left-0 right-0 top-full z-20 mt-1 max-h-72 overflow-auto rounded-lg border border-aa-line bg-white py-1 shadow-aa"
                  >
                    <button
                      type="button"
                      role="option"
                      aria-selected={!filters.country}
                      onMouseEnter={() => setPreviewCountry(null)}
                      onClick={() => {
                        setFilters({ ...filters, country: undefined });
                        setCountryOpen(false);
                        setPreviewCountry(null);
                      }}
                      className="block w-full px-3 py-1.5 text-left text-xs text-aa-ink hover:bg-aa-sand"
                    >
                      All countries
                    </button>
                    {grouped.map((g) => (
                      <div key={g.region}>
                        <div className="px-3 pb-0.5 pt-2 text-[10px] font-semibold uppercase tracking-wide text-aa-muted">
                          {g.region}
                        </div>
                        {g.items.map((c) => {
                          const selected = filters.country === c.country;
                          return (
                            <button
                              key={c.country}
                              type="button"
                              role="option"
                              aria-selected={selected}
                              onMouseEnter={() => setPreviewCountry(c.country)}
                              onClick={() => {
                                setFilters({ ...filters, country: c.country });
                                setCountryOpen(false);
                                setPreviewCountry(null);
                              }}
                              className={`flex w-full items-center justify-between px-3 py-1.5 text-left text-xs hover:bg-aa-sand ${
                                selected ? "font-semibold text-aa-gold-dark" : "text-aa-ink"
                              }`}
                            >
                              <span>{c.country}</span>
                              <span className="text-aa-muted">{c.component_count}</span>
                            </button>
                          );
                        })}
                      </div>
                    ))}
                  </div>
                )}
              </div>
              {filters.country && (
                <button
                  onClick={() => {
                    setFilters({ ...filters, country: undefined });
                    setPreviewCountry(null);
                  }}
                  className="aa-focus rounded-lg px-2 py-1 text-xs font-medium text-aa-muted hover:text-aa-ink"
                >
                  Clear
                </button>
              )}
            </div>

            {/* STEP 2 — Activity (refines within the chosen country). */}
            <div className="mt-3">
              <div className="mb-1.5 flex items-center gap-2">
                <span
                  className={`inline-flex h-5 w-5 items-center justify-center rounded-full text-[11px] font-bold ${
                    activityStepEnabled
                      ? "bg-aa-ink text-white"
                      : "bg-aa-line text-aa-muted"
                  }`}
                >
                  2
                </span>
                <span className="text-xs font-semibold text-aa-ink">Activity</span>
                {!activityStepEnabled && (
                  <span className="text-[11px] text-aa-muted">
                    (optional — or pick a country first)
                  </span>
                )}
              </div>
              <div className="flex flex-wrap gap-1.5">
                {ACTIVITIES.map((a) => {
                  const active = filters.activity === a;
                  return (
                    <button
                      key={a}
                      onClick={() => toggleActivity(a)}
                      aria-pressed={active}
                      className={`aa-focus flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-medium capitalize transition ${
                        active
                          ? "border-aa-gold bg-aa-gold text-white shadow-aa-sm"
                          : "border-aa-line bg-white text-aa-ink hover:border-aa-gold/60 hover:bg-aa-gold-soft"
                      }`}
                    >
                      <ActivityIcon activity={a} size={14} />
                      {label(a)}
                    </button>
                  );
                })}
              </div>
            </div>

            {/* Extra refinements */}
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <select
                aria-label="Intensity"
                value={filters.intensity_level ?? ""}
                onChange={(e) =>
                  setFilters({ ...filters, intensity_level: e.target.value || undefined })
                }
                className="aa-focus rounded-lg border border-aa-line bg-white px-2.5 py-1.5 text-xs capitalize text-aa-ink"
              >
                <option value="">Any intensity</option>
                {INTENSITIES.map((i) => (
                  <option key={i} value={i}>
                    {label(i)}
                  </option>
                ))}
              </select>

              <select
                aria-label="Season month"
                value={filters.season ?? ""}
                onChange={(e) =>
                  setFilters({
                    ...filters,
                    season: e.target.value ? Number(e.target.value) : undefined,
                  })
                }
                className="aa-focus rounded-lg border border-aa-line bg-white px-2.5 py-1.5 text-xs text-aa-ink"
              >
                <option value="">Any month</option>
                {MONTHS.map((m, i) => (
                  <option key={m} value={i + 1}>
                    {m}
                  </option>
                ))}
              </select>

              {activeCount > 0 && (
                <button
                  onClick={() => setFilters({})}
                  className="aa-focus ml-auto rounded-lg px-2.5 py-1.5 text-xs font-medium text-aa-muted hover:text-aa-ink"
                >
                  Clear all
                </button>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
