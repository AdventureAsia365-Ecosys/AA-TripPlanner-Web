"use client";

// AA-1041 (R7 slice 1): the trip's days in the trip panel.
// - item 1: a photo per day (its first stop with a CIS place photo) and per stop when a day has several;
// - item 2: an icon per stop (lib/placeIcon.ts) and per travel leg;
// - item 3: hover / focus of a stop highlights its marker on the map; "Show on map" eases the map to it; a marker
//   selected on the map scrolls its stop into view here (lib/stopSync.ts, state in lib/useTrip.tsx);
// - item 4: reorder of days (the day handle, when a day has several stops) and stops (the stop handle): drag with a
//   mouse or a finger (pointer events; the handle has touch-action: none, the list still scrolls everywhere else), or
//   the arrow keys / Home / End on a focused handle. Every move is saved the way the planner always saved an order
//   (lib/itineraryOrder.ts), announced in a live region, and can be undone from the trip panel.
// Everything is drawn with the k-* tokens, so an agency tenant's brand (R5 theme tokens) colours it.
import { useEffect, useRef, useState } from "react";
import { useTrip } from "@/lib/useTrip";
import { formatLeg, type RouteLeg } from "@/lib/mapbox";
import { isLine, lineClass, stretchSubtitle } from "@/lib/lines";
import {
  dropIndex,
  groupByDay,
  keyTarget,
  moveAnnouncement,
  moveDay,
  moveStop,
} from "@/lib/itineraryOrder";
import { kindLabel, legKind, placeKind } from "@/lib/placeIcon";
import { dayPhoto, stopPhoto } from "@/lib/stopSync";
import type { ItineraryDay } from "@/lib/types";
import { PlaceIcon } from "@/components/ActivityIcon";
import StopActivities from "@/components/StopActivities";
import StopPhoto from "@/components/StopPhoto";
import { Icon } from "@/ui/Icon";

type SortKind = "stop" | "day";
interface Drag {
  kind: SortKind;
  from: number;
  to: number;
  startY: number;
  dy: number;
  centres: number[];
  moved: boolean;
}

// A pointer has to travel this far before a press on a handle becomes a drag (a tap or a click stays a click).
const DRAG_START_PX = 4;

export default function DayList({
  legs,
  onShowOnMap,
}: {
  legs: Map<string, RouteLeg>;
  onShowOnMap?: () => void;
}) {
  const {
    itinerary,
    customDays,
    remove,
    reorder,
    addDay,
    removeDay,
    stopDetails,
    activeStopId,
    selectedStop,
    hoverStop,
    selectStop,
  } = useTrip();
  const listRef = useRef<HTMLOListElement | null>(null);
  const [openStop, setOpenStop] = useState<string | null>(null);
  const [drag, setDrag] = useState<Drag | null>(null);
  const [announce, setAnnounce] = useState("");
  // The handle to focus again once the moved row is drawn in its new place (keyboard moves keep focus on it).
  const refocus = useRef<string | null>(null);

  const groups = groupByDay(itinerary);
  const nameOfDestination = (id: string) => itinerary.find((x) => x.destination_id === id && !isLine(x))?.name ?? null;
  const detailOf = (d: ItineraryDay) => stopDetails.get(d.component_id);
  const kindOf = (d: ItineraryDay) =>
    placeKind({
      name: d.name,
      destination_name: d.destination_name ?? detailOf(d)?.destination_name,
      activity: d.activity ?? detailOf(d)?.activity,
    });
  const stopLabelOf = (d: ItineraryDay) => `Day ${d.day}: ${d.name ?? "stop"}`;

  // Keep keyboard focus on the moved handle after the list redraws.
  useEffect(() => {
    const id = refocus.current;
    if (!id || !listRef.current) return;
    const el = listRef.current.querySelector<HTMLElement>(`[data-handle="${CSS.escape(id)}"]`);
    if (el) {
      el.focus();
      refocus.current = null;
    }
  }, [itinerary]);

  // A marker selected on the map: bring its stop into view here.
  useEffect(() => {
    if (!selectedStop || selectedStop.from !== "map" || !listRef.current) return;
    const el = listRef.current.querySelector<HTMLElement>(`[data-stop="${CSS.escape(selectedStop.componentId)}"]`);
    el?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [selectedStop]);

  const commit = async (kind: SortKind, from: number, to: number, handleId: string, label: string) => {
    const next = kind === "stop" ? moveStop(itinerary, itinerary[from].component_id, to) : moveDay(itinerary, from, to);
    if (!next) return;
    refocus.current = handleId;
    setAnnounce(moveAnnouncement(label, to, kind === "stop" ? itinerary.length : groups.length));
    await reorder(next);
  };

  const onHandleKey = (e: React.KeyboardEvent, kind: SortKind, index: number, handleId: string, label: string) => {
    const length = kind === "stop" ? itinerary.length : groups.length;
    const to = keyTarget(e.key, index, length);
    if (to === null) return;
    e.preventDefault();
    void commit(kind, index, to, handleId, label);
  };

  const onPointerDown = (e: React.PointerEvent, kind: SortKind, index: number) => {
    if (e.button !== 0 || !listRef.current) return;
    const rows = listRef.current.querySelectorAll<HTMLElement>(kind === "stop" ? "[data-sort-stop]" : "[data-sort-day]");
    const centres = [...rows].map((r) => {
      const b = r.getBoundingClientRect();
      return b.top + b.height / 2;
    });
    (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
    setDrag({ kind, from: index, to: index, startY: e.clientY, dy: 0, centres, moved: false });
  };
  const onPointerMove = (e: React.PointerEvent) => {
    if (!drag) return;
    const dy = e.clientY - drag.startY;
    if (!drag.moved && Math.abs(dy) < DRAG_START_PX) return;
    setDrag({ ...drag, dy, moved: true, to: dropIndex(drag.centres, drag.centres[drag.from] + dy, drag.from) });
  };
  const onPointerUp = (handleId: string, label: string) => {
    if (!drag) return;
    const d = drag;
    setDrag(null);
    if (d.moved && d.to !== d.from) void commit(d.kind, d.from, d.to, handleId, label);
  };

  const dragStyle = (kind: SortKind, index: number): React.CSSProperties | undefined =>
    drag && drag.moved && drag.kind === kind && drag.from === index
      ? { transform: `translateY(${drag.dy}px)`, position: "relative", zIndex: 10 }
      : undefined;
  const dropMark = (kind: SortKind, index: number) =>
    drag && drag.moved && drag.kind === kind && drag.to === index && drag.from !== index
      ? drag.to > drag.from
        ? "border-b-4 border-b-k-accent"
        : "border-t-4 border-t-k-accent"
      : "";

  // A render function, not a component: the handle keeps its DOM node (and its pointer capture) across re-renders.
  const handle = (kind: SortKind, index: number, id: string, label: string) => (
    <button
      type="button"
      data-handle={id}
      data-testid={kind === "stop" ? "stop-handle" : "day-handle"}
      aria-label={`Reorder ${label}`}
      aria-describedby="tp-reorder-hint"
      onKeyDown={(e) => onHandleKey(e, kind, index, id, label)}
      onPointerDown={(e) => onPointerDown(e, kind, index)}
      onPointerMove={onPointerMove}
      onPointerUp={() => onPointerUp(id, label)}
      onPointerCancel={() => setDrag(null)}
      className="aa-focus tp-no-print -ml-1 inline-flex h-8 w-6 shrink-0 cursor-grab touch-none items-center justify-center rounded-md text-k-muted hover:bg-k-accent-tint hover:text-k-accent-deep active:cursor-grabbing"
    >
      <Icon name="grip" className="h-4 w-4" />
    </button>
  );

  const stopActions = (d: ItineraryDay) => (
    <>
      <div className="tp-no-print mt-1 flex flex-wrap gap-x-3 gap-y-1">
        {typeof d.lat === "number" && !isLine(d) && (
          <button
            type="button"
            onClick={() => {
              selectStop(d.component_id, "list");
              onShowOnMap?.();
            }}
            data-testid="show-on-map"
            className="aa-focus text-[11px] font-medium text-k-accent-deep"
          >
            Show on map
          </button>
        )}
        {d.destination_id && (
          <button
            type="button"
            onClick={() => setOpenStop(openStop === d.component_id ? null : d.component_id)}
            className="aa-focus text-[11px] font-medium text-k-accent-deep"
          >
            {openStop === d.component_id ? "Hide activities" : "Activities here"}
          </button>
        )}
        <button
          type="button"
          onClick={() => addDay("extra_night", d.component_id)}
          className="aa-focus text-[11px] text-k-muted hover:text-k-ink"
        >
          + extra night
        </button>
      </div>
      {openStop === d.component_id && d.destination_id && <StopActivities destinationId={d.destination_id} />}
      {customDays
        .filter((c) => c.kind === "extra_night" && c.after_component_id === d.component_id)
        .map((c) => (
          <p key={c.day_id} className="mt-1 text-[11px] text-k-body">
            Extra night here <span className="text-k-muted">(your addition)</span>{" "}
            <button type="button" onClick={() => removeDay(c.day_id)} className="aa-focus tp-no-print text-k-muted hover:text-k-danger">
              remove
            </button>
          </p>
        ))}
    </>
  );

  const stopText = (d: ItineraryDay) => (
    <>
      <p className="truncate text-sm font-semibold text-k-ink">
        {isLine(d) && (
          // The SVG wave, not the "〰" glyph: it is missing from Poppins and most system fonts.
          <span role="img" aria-label="Route" className="mr-1 inline-block align-[-3px] text-k-accent-deep">
            <Icon name="wave" className="h-4 w-4" />
          </span>
        )}
        {d.name}
      </p>
      {isLine(d) && <p className="mt-0.5 text-xs text-k-muted">{stretchSubtitle(d, nameOfDestination)}</p>}
      {d.rationale && <p className="mt-0.5 text-xs leading-relaxed text-k-muted">{d.rationale}</p>}
    </>
  );

  const removeButton = (d: ItineraryDay) => (
    <button
      type="button"
      onClick={() => remove(d.component_id)}
      aria-label={`Remove ${stopLabelOf(d)}`}
      className="aa-focus tp-no-print shrink-0 rounded-md px-1.5 py-0.5 text-xs text-k-muted opacity-100 transition hover:text-k-danger md:opacity-0 md:focus:opacity-100 md:group-hover:opacity-100"
    >
      Remove
    </button>
  );

  // Sync handlers on a stop row: hover and keyboard focus inside it highlight its marker.
  const syncProps = (d: ItineraryDay) => ({
    "data-stop": d.component_id,
    "data-active": activeStopId === d.component_id ? "true" : "false",
    onMouseEnter: () => hoverStop(d.component_id),
    onMouseLeave: () => hoverStop(null),
    onFocus: () => hoverStop(d.component_id),
    onBlur: (e: React.FocusEvent) => {
      if (!e.currentTarget.contains(e.relatedTarget as Node | null)) hoverStop(null);
    },
  });

  // Each stop's place in the flat list (the stop handles reorder that list).
  const flatIndex = new Map(itinerary.map((d, i) => [d.component_id, i]));
  return (
    <>
      <p id="tp-reorder-hint" className="sr-only">
        Drag the handle, or press the up and down arrow keys on it, to move it. Home and End move it to the start or the
        end.
      </p>
      <ol ref={listRef} aria-label="Your days" className="space-y-0" data-testid="day-list">
        {groups.map((g, gi) => {
          const first = g.stops[0];
          const last = g.stops[g.stops.length - 1];
          const many = g.stops.length > 1;
          const dayActive = g.stops.some((s) => s.component_id === activeStopId);
          const photo = dayPhoto(stopDetails, g.stops.map((s) => s.component_id));
          const leg = gi < groups.length - 1 ? legs.get(last.component_id) : undefined;
          const dayLabel = many ? `Day ${g.day}` : stopLabelOf(first);
          return (
            <li key={first.component_id} data-sort-day={gi} data-testid="day" className="tp-print-day">
              <div
                style={dragStyle("day", gi)}
                className={`rounded-xl border bg-k-card p-3 shadow-aa-sm transition ${dropMark("day", gi)} ${
                  dayActive ? "border-k-accent" : "border-k-line"
                }`}
              >
                {many ? (
                  <>
                    <div className="flex items-center gap-3">
                      {handle("day", gi, `day:${first.component_id}`, dayLabel)}
                      <span className="relative">
                        <StopPhoto src={photo} kind={kindOf(first)} />
                        <span className="absolute -left-1.5 -top-1.5 inline-flex h-6 w-6 items-center justify-center rounded-full bg-k-accent text-[11px] font-bold text-k-on-accent ring-2 ring-k-card">
                          {g.day}
                        </span>
                      </span>
                      <div className="min-w-0 flex-1">
                        <p className="text-sm font-semibold text-k-ink">Day {g.day}</p>
                        <p className="truncate text-xs text-k-muted">
                          {g.stops.length} stops · {first.destination_name ?? detailOf(first)?.destination_name ?? first.name}
                        </p>
                      </div>
                    </div>
                    <ul className="mt-2 space-y-1.5 border-t border-k-line pt-2">
                      {g.stops.map((d) => {
                        const si = flatIndex.get(d.component_id) ?? 0;
                        const kind = kindOf(d);
                        return (
                          <li
                            key={d.component_id}
                            data-sort-stop={si}
                            {...syncProps(d)}
                            style={dragStyle("stop", si)}
                            className={`group flex items-start gap-2 rounded-lg p-1.5 transition data-[active=true]:bg-k-accent-tint ${dropMark("stop", si)}`}
                          >
                            {handle("stop", si, d.component_id, stopLabelOf(d))}
                            {stopPhoto(stopDetails, d.component_id) ? (
                              <StopPhoto src={stopPhoto(stopDetails, d.component_id)} kind={kind} size="sm" />
                            ) : (
                              <span className="mt-1 text-k-accent-deep">
                                <PlaceIcon kind={kind} size={18} title={kindLabel(kind)} />
                              </span>
                            )}
                            <div className="min-w-0 flex-1">
                              {stopText(d)}
                              {stopActions(d)}
                            </div>
                            {removeButton(d)}
                          </li>
                        );
                      })}
                    </ul>
                  </>
                ) : (
                  (() => {
                    const si = flatIndex.get(first.component_id) ?? 0;
                    const d = first;
                    const kind = kindOf(d);
                    return (
                      <div
                        data-sort-stop={si}
                        {...syncProps(d)}
                        style={dragStyle("stop", si)}
                        className={`group -m-1.5 flex items-start gap-3 rounded-lg p-1.5 transition data-[active=true]:bg-k-accent-tint ${dropMark("stop", si)}`}
                      >
                        {handle("stop", si, d.component_id, stopLabelOf(d))}
                        <span className="relative">
                          <StopPhoto src={photo} kind={kind} />
                          <span className="absolute -left-1.5 -top-1.5 inline-flex h-6 w-6 items-center justify-center rounded-full bg-k-accent text-[11px] font-bold text-k-on-accent ring-2 ring-k-card">
                            {d.day}
                          </span>
                        </span>
                        <div className="min-w-0 flex-1">
                          <p className="flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wide text-k-muted">
                            <PlaceIcon kind={kind} size={12} className="text-k-accent-deep" />
                            Day {d.day} · {kindLabel(kind)}
                          </p>
                          {stopText(d)}
                          {stopActions(d)}
                        </div>
                        {removeButton(d)}
                      </div>
                    );
                  })()
                )}
              </div>

              {/* Travel connector to the next day. Road legs show real driving distance/time; flight legs say so
                  (no fake km). The icon follows the leg's mode. */}
              {leg && (
                <div className="flex flex-wrap items-center gap-x-1.5 py-1 pl-3.5 text-[11px] text-k-muted" data-testid="leg">
                  <span className="text-k-accent-deep">
                    <PlaceIcon kind={legKind(leg.mode, leg.stretch ? lineClass(leg.stretch.name) : null)} size={14} />
                  </span>
                  <span>{formatLeg(leg)}</span>
                </div>
              )}
              {!leg && gi < groups.length - 1 && <div className="h-2" aria-hidden />}
            </li>
          );
        })}
      </ol>
      <p role="status" aria-live="polite" className="sr-only" data-testid="reorder-announce">
        {announce}
      </p>
    </>
  );
}
