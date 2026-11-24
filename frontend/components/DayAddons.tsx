"use client";

// AA-1152 (S285 advisory PR-29 T6, B2 option C): each day card lists the optional activities AA sells at the day's
// places (the place the traveller sleeps first, then the places visited; backend/assembly/day_addons.py). Ticking one
// is a trip event, so it survives a reload and goes with the request to the advisor (its sv: code, never a price). On
// an agency's planner an add-on shows the agency's own "from" price when the agency publishes prices, with the note
// that its quote is final; AA's own planner shows none. One read per trip (the provider), refreshed when the stops
// change; a failed read shows nothing.

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { fetchTripAddons, setTripAddon } from "@/lib/api";
import { addonsOfDay, durationText, priceText, type DayAddon, type TripAddons } from "@/lib/dayAddons";
import { ADDONS } from "@/lib/dayAddons.messages";
import type { Translator } from "@/lib/planner.messages";
import { useTrip } from "@/lib/useTrip";
import { usePlannerLang, usePlannerT } from "@/components/PlannerLang";

interface AddonsState {
  data: TripAddons | null;
  busy: string | null;
  failed: boolean;
  toggle: (day: number, addon: DayAddon) => Promise<void>;
}

const AddonsContext = createContext<AddonsState>({ data: null, busy: null, failed: false, toggle: async () => {} });

export function DayAddonsProvider({ children }: { children: ReactNode }) {
  const { tripId, sessionId, itinerary } = useTrip();
  const [data, setData] = useState<TripAddons | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const stopsKey = itinerary.map((d) => `${d.day}:${d.component_id}`).join(",");

  useEffect(() => {
    let active = true;
    if (!stopsKey) return;
    fetchTripAddons(tripId).then((d) => {
      if (active) setData(d);
    });
    return () => {
      active = false;
    };
  }, [tripId, stopsKey]);

  const toggle = useCallback(
    async (day: number, addon: DayAddon) => {
      setBusy(`${day}:${addon.component_id}`);
      const next = await setTripAddon(tripId, sessionId, day, addon.component_id, !addon.selected);
      setBusy(null);
      setFailed(next === null);
      if (next) setData(next);
    },
    [tripId, sessionId],
  );

  // An empty trip has no day card, so a list kept from before it emptied is never shown.
  const value = useMemo(() => ({ data: stopsKey ? data : null, busy, failed, toggle }), [stopsKey, data, busy, failed, toggle]);
  return <AddonsContext.Provider value={value}>{children}</AddonsContext.Provider>;
}

function facts(a: DayAddon, t: Translator<typeof ADDONS.en>): string {
  return [
    durationText(a.duration_minutes),
    a.operating_weekdays.length ? t("runs", a.operating_weekdays) : null,
    a.start_times.length ? t("starts", a.start_times.join(", ")) : null,
    a.min_pax > 1 ? t("minPax", a.min_pax) : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

export function DayAddonsList({ day }: { day: number }) {
  const { data, busy, failed, toggle } = useContext(AddonsContext);
  const t = usePlannerT(ADDONS);
  const lang = usePlannerLang();
  const items = addonsOfDay(data, day);
  if (items.length === 0) return null;
  const locale = lang === "vi" ? "vi-VN" : "en-US";
  const priced = items.some((a) => a.price);
  return (
    <div className="mt-2 border-t border-k-line pt-2" data-testid="day-addons">
      <p className="text-[11px] font-semibold uppercase tracking-wide text-k-muted">{t("title")}</p>
      <p className="text-[11px] text-k-muted">{t("help")}</p>
      <ul className="mt-1 space-y-1" aria-label={t("title")}>
        {items.map((a) => (
          <li key={a.component_id}>
            <label className="flex cursor-pointer items-start gap-2 text-[11px] text-k-body">
              <input
                type="checkbox"
                checked={a.selected}
                disabled={busy !== null}
                onChange={() => toggle(day, a)}
                aria-label={t("add", a.title)}
                className="mt-0.5 accent-k-accent-deep"
                data-testid="day-addon"
              />
              <span>
                <span className="font-medium text-k-ink">{a.title}</span>
                {a.at === "sleep" && <span className="text-k-muted"> · {t("atSleep")}</span>}
                <span className="block text-k-muted">{facts(a, t)}</span>
                {a.price && (
                  <span className="block font-medium text-k-accent-deep" data-testid="day-addon-price">
                    {t("from", priceText(a.price, locale))}
                  </span>
                )}
              </span>
            </label>
          </li>
        ))}
      </ul>
      {priced && <p className="mt-1 text-[10px] text-k-muted">{t("priceNote", t("yourAgency"))}</p>}
      {failed && <p className="mt-1 text-[11px] text-k-danger" role="status">{t("failed")}</p>}
    </div>
  );
}
