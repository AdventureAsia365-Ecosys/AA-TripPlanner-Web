"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import * as api from "./api";
import { isB2bOnly } from "./advisorDemo";
import type { AiNoticeKey } from "./aiNotices";
import { isUuid, randomUuid } from "./guestIds";
import { whenIdle } from "./idle";
import { reorderLocally } from "./itineraryOrder";
import { createOrderSaver } from "./orderSaver";
import { applySteps, type ChatProposal } from "./planChat";
import { activeStop as pickActiveStop, photoKey, type StopSelection, type SyncSource } from "./stopSync";
import { idsStorageKey } from "./plannerTenant";
import type { LeadBody } from "./leadForm";
import type {
  BrowseFilters,
  CountryOption,
  Coverage,
  CustomDay,
  DestinationPin,
  ItineraryDay,
  RouteProposal,
  UnavailableStop,
} from "./types";
import type { StopDetail, Suggestion } from "./api";

interface TripState {
  sessionId: string;
  tripId: string;
  itinerary: ItineraryDay[];
  filters: BrowseFilters;
  status: string;
  inTripComponentIds: Set<string>;
  narration: string;
  narrating: boolean;
  // Where the narration came from: 'master' (AA's itinerary text, AI-assisted),
  // 'llm' (AI-written), 'mixed', 'master_basic' (AA text + plain day lines, used
  // when the daily AI budget is spent — AA-805), 'basic' (plain day lines only,
  // no AI text), or "" before any call.
  narrationSource: string;
  // AA-642 S0: the notice shown with a narration made without AI (AI paused, daily budget spent).
  narrationNotice: AiNoticeKey | null;
  // Semantic search: when searchResults is non-null the map shows this
  // ranked set instead of tiles-by-bounds; null means "browse mode".
  searchQuery: string;
  searchResults: DestinationPin[] | null;
  searching: boolean;
  // AA-1015: set when this visitor reached the search limit (429) or the bot check refused the search (403);
  // cleared by the next answered search.
  searchNotice: AiNoticeKey | null;
  // Countries that actually have components (for the country-first filter).
  countries: CountryOption[];
  // Next-to-pin suggestions, refreshed as the trip changes.
  suggestions: Suggestion[];
  // A destination the UI wants to open (e.g. from a suggestion click). The
  // map listens and opens that destination's popup so the user picks a
  // specific component (per product rule: never "add a whole destination").
  focusDestinationId: string | null;
  focusDestination: (id: string | null) => void;
  // A country the UI wants to *preview* highlighting on the map (e.g. while
  // hovering a country in the picker), WITHOUT committing it as a filter. The
  // map highlights this country's polygon; clearing it falls back to the
  // selected filters.country highlight. null means "no preview".
  previewCountry: string | null;
  setPreviewCountry: (country: string | null) => void;
  setFilters: (f: BrowseFilters) => void;
  runSearch: (q: string) => Promise<void>;
  clearSearch: () => void;
  add: (componentId: string) => Promise<void>;
  remove: (componentId: string) => Promise<void>;
  reorder: (orderedIds: string[]) => Promise<void>;
  // AA-1041 (item 4): the order before the last reorder (one step of undo; cleared by an add or a remove), and
  // whether the last reorder could not be saved (the list went back to the saved order).
  undoOrder: string[] | null;
  undoReorder: () => Promise<void>;
  reorderFailed: boolean;
  // AA-1041 (items 1, 2): the stops' catalog details (place photo, activity), by component id.
  stopDetails: Map<string, StopDetail>;
  // AA-1041 (item 7): the saved trip could not be read (5xx / network): the panel shows an error with a retry.
  loadFailed: boolean;
  retryLoad: () => void;
  narrate: (mode?: "compose" | "renarrate") => Promise<void>;
  send: (customer?: { name: string; phone: string; email: string; consent: boolean }) => Promise<
    { ok: boolean; needsRegistration: boolean; demo?: boolean }
  >;
  // AA-1038 slice 3: an agency tenant's planner sends a lead (lead.submitted.v1) instead.
  sendLead: (lead: LeadBody) => Promise<{ status: number; body: { error?: string; fields?: string[] } }>;
  // AA-674 / Jira PR-11: routes of real AA tours.
  coverage: Coverage | null;
  customDays: CustomDay[];
  // AA-986 L6: saved stops whose tour AA no longer serves (greyed in the trip panel, never on the map).
  unavailableStops: UnavailableStop[];
  // A proposed route drawn on the map as a preview (hover / selection), or null.
  routePreview: RouteProposal | null;
  setRoutePreview: (p: RouteProposal | null) => void;
  applyRoute: (p: RouteProposal) => Promise<boolean>;
  // AA-1041 slice 2: apply a planning chat proposal (removes, then apply-route); false when a step failed.
  applyChatProposal: (p: ChatProposal) => Promise<boolean>;
  addDay: (kind: CustomDay["kind"], afterComponentId?: string) => Promise<void>;
  removeDay: (dayId: string) => Promise<void>;
  // AA-812: what the visitor asked for before reaching the planner (home search or a tour page),
  // from the /plan query string. Pre-fills the route finder; never changes the trip on its own.
  intent: PlanIntent;
  // True once the saved trip (if any) has been restored, so a pre-fill never races the restore.
  ready: boolean;
}

export interface PlanIntent {
  country?: string;
  days?: number;
  travellers?: number;
  // catalog tour_id of a tour page's "Customise this trip" / "Ask an advisor"
  tourId?: string;
  // its stable catalog source_id: resolves the CURRENT tour_id if the tour was re-uploaded
  sourceId?: string;
  ask?: boolean;
}

const TripContext = createContext<TripState | null>(null);

// Guest identity: a random session + trip id (UUID v4), persisted in localStorage so a reload
// restores the same trip (the backend keeps the event log by trip_id). The ids are identifiers,
// not credentials — the HttpOnly guest cookie is (AA-827). Falls back to fresh ids if storage is
// unavailable (SSR, privacy mode); stored ids that are not UUIDs (an old Math.random fallback the
// backend never accepted) are replaced.
function loadOrCreateIds(): { sessionId: string; tripId: string } {
  if (typeof window !== "undefined") {
    try {
      const raw = window.localStorage.getItem(idsStorageKey());
      if (raw) {
        const parsed = JSON.parse(raw);
        if (isUuid(parsed?.sessionId) && isUuid(parsed?.tripId)) return parsed;
      }
    } catch {
      /* ignore */
    }
  }
  const ids = { sessionId: randomUuid(), tripId: randomUuid() };
  if (typeof window !== "undefined") {
    try {
      window.localStorage.setItem(idsStorageKey(), JSON.stringify(ids));
    } catch {
      /* ignore */
    }
  }
  return ids;
}

export function TripProvider({
  children,
  initialIntent = {},
}: {
  children: React.ReactNode;
  initialIntent?: PlanIntent;
}) {
  // AA-906: fixed for the provider's life, so a lazy state initializer (read during render), not a ref.
  const [{ sessionId, tripId }] = useState(loadOrCreateIds);

  const [intent] = useState<PlanIntent>(initialIntent);
  const [ready, setReady] = useState(false);
  const [itinerary, setItinerary] = useState<ItineraryDay[]>([]);
  // AA-812: a destination chosen on the home page focuses the map on that country.
  const [filters, setFilters] = useState<BrowseFilters>(
    initialIntent.country ? { country: initialIntent.country } : {},
  );
  const [status, setStatus] = useState<string>("draft");
  const [countries, setCountries] = useState<CountryOption[]>([]);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [focusDestinationId, setFocusDestinationId] = useState<string | null>(
    null,
  );
  const [previewCountry, setPreviewCountry] = useState<string | null>(null);
  const [narration, setNarration] = useState<string>("");
  const [narrationSource, setNarrationSource] = useState<string>("");
  const [narrationNotice, setNarrationNotice] = useState<AiNoticeKey | null>(null);
  const [narrating, setNarrating] = useState<boolean>(false);
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [searchResults, setSearchResults] = useState<DestinationPin[] | null>(
    null,
  );
  const [searching, setSearching] = useState<boolean>(false);
  const [searchNotice, setSearchNotice] = useState<AiNoticeKey | null>(null);
  const [coverage, setCoverage] = useState<Coverage | null>(null);
  const [customDays, setCustomDays] = useState<CustomDay[]>([]);
  const [unavailableStops, setUnavailableStops] = useState<UnavailableStop[]>([]);
  const [routePreview, setRoutePreview] = useState<RouteProposal | null>(null);
  const [undoOrder, setUndoOrder] = useState<string[] | null>(null);
  const [reorderFailed, setReorderFailed] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);
  const [loadAttempt, setLoadAttempt] = useState(0);
  // AA-1041: the stop details fetched, tagged with the id set they answer (derived, no setState in an effect body).
  const [stopsFetched, setStopsFetched] = useState<{ key: string; stops: Map<string, StopDetail> }>({
    key: "",
    stops: new Map(),
  });
  // The itinerary the reorder callbacks start from (AA-906: synced after each commit, never written during render).
  const itineraryRef = useRef<ItineraryDay[]>([]);
  useLayoutEffect(() => {
    itineraryRef.current = itinerary;
  }, [itinerary]);
  // The latest filters for the suggestion fetch and runSearch without making them dependencies.
  // AA-906 (React Compiler lint: no ref writes during render): synced after each commit, in a layout
  // effect so every passive effect below already sees the new value.
  const filtersRef = useRef<BrowseFilters>(filters);
  useLayoutEffect(() => {
    filtersRef.current = filters;
  }, [filters]);

  const inTripComponentIds = useMemo(
    () => new Set(itinerary.map((d) => d.component_id)),
    [itinerary],
  );

  // Load the country list once (for the country-first filter dropdown).
  useEffect(() => {
    let active = true;
    api.fetchCountries().then((cs) => {
      if (active) setCountries(cs);
    });
    return () => {
      active = false;
    };
  }, []);

  // Restore a previously-built trip on mount (persisted trip_id). The backend
  // rebuilds the itinerary from its event log, so a reload brings it back.
  // AA-1041 (item 7): a read that failed (5xx / network) is told apart from a new trip; retryLoad reads again.
  useEffect(() => {
    let active = true;
    api.loadTrip(tripId).then(({ trip: res, failed }) => {
      if (!active) return;
      if (res?.itinerary && res.itinerary.length > 0) {
        setItinerary(res.itinerary);
        if (res.status) setStatus(res.status);
      }
      if (res?.custom_days) setCustomDays(res.custom_days);
      if (res?.unavailable_stops) setUnavailableStops(res.unavailable_stops);
      setLoadFailed(failed);
      setReady(true);
    });
    return () => {
      active = false;
    };
  }, [tripId, loadAttempt]);

  const retryLoad = useCallback(() => {
    setReady(false);
    setLoadFailed(false);
    setLoadAttempt((n) => n + 1);
  }, []);

  // AA-1041 (items 1, 2): the stops' place photos and activities, refetched when the SET of stops changes (a reorder
  // asks nothing). Missing ids only; the cache in lib/api.ts answers the rest.
  // AA-1041 (first-load performance): asked for once the trip has painted and the main thread is idle (lib/idle.ts), so
  // a returning visitor's trip draws first (icon tiles) and the photo answers never compete with the first render.
  const stopsKey = photoKey(itinerary);
  useEffect(() => {
    if (!stopsKey) return;
    let active = true;
    const cancel = whenIdle(() => {
      void api.fetchStops(stopsKey.split(",")).then(({ stops }) => {
        if (active) setStopsFetched({ key: stopsKey, stops });
      });
    });
    return () => {
      active = false;
      cancel();
    };
  }, [stopsKey]);
  const stopDetails = stopsFetched.stops;

  // Refresh next-to-pin suggestions whenever the trip's component set
  // changes. Keyed on the sorted component ids so a reorder doesn't refetch.
  const suggestKey = itinerary
    .map((d) => d.component_id)
    .sort()
    .join(",");
  // AA-674: the map's activity/intensity filters also narrow the suggestions.
  const filterKey = `${filters.activity ?? ""}|${filters.intensity_level ?? ""}`;
  // AA-674: which real tours cover the trip — refreshed when the day order or added days change.
  const coverageKey = `${itinerary.map((d) => d.component_id).join(",")}|${customDays
    .map((d) => d.day_id)
    .join(",")}`;
  const tripEmpty = itinerary.length === 0 && customDays.length === 0;
  // AA-906 (React Compiler lint, no setState in an effect body): an emptied trip clears its
  // suggestions / coverage during render, once per key change ("adjust state when a prop changes");
  // the effects below only fetch.
  const [keysSeen, setKeysSeen] = useState({ suggestKey, coverageKey });
  if (keysSeen.suggestKey !== suggestKey || keysSeen.coverageKey !== coverageKey) {
    setKeysSeen({ suggestKey, coverageKey });
    if (!suggestKey && keysSeen.suggestKey !== suggestKey) setSuggestions([]);
    if (tripEmpty && keysSeen.coverageKey !== coverageKey) setCoverage(null);
  }
  useEffect(() => {
    if (!suggestKey) return;
    let active = true;
    api.fetchSuggestions(tripId, filtersRef.current).then((s) => {
      if (active) setSuggestions(s);
    });
    return () => {
      active = false;
    };
  }, [suggestKey, filterKey, tripId]);

  // coverageKey is the trigger (day order / added days); tripEmpty is derived from the same values.
  useEffect(() => {
    if (tripEmpty) return;
    let active = true;
    api.fetchCoverage(tripId).then((c) => {
      if (active) setCoverage(c);
    });
    return () => {
      active = false;
    };
  }, [coverageKey, tripEmpty, tripId]);

  // AA-1041 (item 4): the new order shows at once (days renumbered like the server does), then the server's answer
  // replaces it. A refused or failed save puts the last saved order back and says so. Saved the way the planner always
  // saved an order: one reorder event with every component id (PATCH /trip/{id}/reorder). Quick moves (arrow keys
  // pressed fast) are saved one after another and only the latest answer is drawn (lib/orderSaver.ts), so an older
  // answer never draws over a newer move and the order stored is the one on screen.
  const orderSaver = useMemo(
    () =>
      createOrderSaver<ItineraryDay>({
        send: async (ids) => (await api.reorder(tripId, sessionId, ids)).itinerary,
        local: reorderLocally,
        show: setItinerary,
        settled: (outcome) => {
          if (outcome.ok) setUndoOrder(outcome.undo);
          else setReorderFailed(true);
        },
      }),
    [tripId, sessionId],
  );

  const applyRoute = useCallback(
    async (p: RouteProposal) => {
      const ids = p.days.map((d) => d.component_id).filter((x): x is string => !!x);
      if (ids.length === 0) return false;
      const res = await api.applyRoute(tripId, sessionId, ids);
      if (!res?.itinerary) return false;
      orderSaver.invalidate();
      setItinerary(res.itinerary);
      setRoutePreview(null);
      return true;
    },
    [tripId, sessionId, orderSaver],
  );

  const applyChatProposal = useCallback(
    async (p: ChatProposal) => {
      const steps = applySteps(p);
      let latest: ItineraryDay[] | null = null;
      let ok = true;
      for (const id of steps.remove) {
        const res = await api.removeComponent(tripId, sessionId, id);
        if (!res.itinerary) {
          ok = false;
          break;
        }
        latest = res.itinerary;
      }
      if (ok && steps.route.length) {
        const res = await api.applyRoute(tripId, sessionId, steps.route);
        if (res?.itinerary) latest = res.itinerary;
        else ok = false;
      }
      if (latest) {
        orderSaver.invalidate();
        setItinerary(latest);
        setUndoOrder(null);
        setUnavailableStops((stops) => stops.filter((s) => !steps.remove.includes(s.component_id)));
      }
      return ok;
    },
    [tripId, sessionId, orderSaver],
  );

  const addDay = useCallback(
    async (kind: CustomDay["kind"], afterComponentId?: string) => {
      const days = await api.addDay(tripId, sessionId, kind, afterComponentId);
      if (days) setCustomDays(days);
    },
    [tripId, sessionId],
  );

  const removeDay = useCallback(
    async (dayId: string) => {
      const days = await api.removeDay(tripId, sessionId, dayId);
      if (days) setCustomDays(days);
    },
    [tripId, sessionId],
  );

  const add = useCallback(
    async (componentId: string) => {
      const res = await api.addComponent(tripId, sessionId, componentId);
      if (res.itinerary) {
        orderSaver.invalidate();
        setItinerary(res.itinerary);
        setUndoOrder(null);
      }
    },
    [tripId, sessionId, orderSaver],
  );

  const remove = useCallback(
    async (componentId: string) => {
      const res = await api.removeComponent(tripId, sessionId, componentId);
      if (res.itinerary) {
        orderSaver.invalidate();
        setItinerary(res.itinerary);
        setUndoOrder(null);
        // AA-986 L6: removing a stop AA no longer serves takes it off the greyed list too.
        setUnavailableStops((stops) => stops.filter((s) => s.component_id !== componentId));
      }
    },
    [tripId, sessionId, orderSaver],
  );

  const saveOrder = useCallback(
    async (orderedIds: string[], isUndo: boolean) => {
      setReorderFailed(false);
      await orderSaver.save(itineraryRef.current, orderedIds, isUndo);
    },
    [orderSaver],
  );
  const reorder = useCallback((orderedIds: string[]) => saveOrder(orderedIds, false), [saveOrder]);
  const undoReorder = useCallback(async () => {
    if (undoOrder) await saveOrder(undoOrder, true);
  }, [undoOrder, saveOrder]);

  const runSearch = useCallback(async (q: string) => {
    const query = q.trim();
    setSearchQuery(q);
    if (!query) {
      setSearchResults(null);
      setSearching(false);
      return;
    }
    setSearching(true);
    try {
      // Semantic search is ranked within the active closed-enum filters
      // (activity/intensity/season/country) — pass the current filters.
      const { pins, notice } = await api.search(query, filtersRef.current);
      // AA-1015: over the search limit, or refused by the bot check, the map keeps what it shows and a calm line says
      // why (no empty result).
      setSearchNotice(notice);
      if (!notice) setSearchResults(pins);
    } finally {
      setSearching(false);
    }
  }, []);

  const clearSearch = useCallback(() => {
    setSearchNotice(null);
    setSearchQuery("");
    setSearchResults(null);
    setSearching(false);
  }, []);

  const narrate = useCallback(
    async (mode: "compose" | "renarrate" = "compose") => {
      setNarrating(true);
      try {
        const res = await api.narrate(tripId, sessionId, mode);
        if (res.ok) {
          setNarration(res.narration);
          setNarrationSource(res.source ?? "");
        }
        // AA-1015: a limit answer keeps the current narration and only shows its notice.
        if (res.ok || res.notice) setNarrationNotice(res.notice);
      } finally {
        setNarrating(false);
      }
    },
    [tripId, sessionId],
  );

  const send = useCallback(
    async (customer?: { name: string; phone: string; email: string; consent: boolean }) => {
      const { status: code, body } = await api.sendToAdvisor(
        tripId,
        sessionId,
        customer,
      );
      if (code === 200) {
        if (body.itinerary) setItinerary(body.itinerary);
        setStatus("sent");
        return { ok: true, needsRegistration: false };
      }
      if (code === 422 && body.error === "registration_required") {
        return { ok: false, needsRegistration: true };
      }
      // AA-1034 R0: the Lambda refuses every hand-off while the B2B-only demo is on (an old bundle can still call it).
      if (isB2bOnly(code, body)) return { ok: false, needsRegistration: false, demo: true };
      return { ok: false, needsRegistration: false };
    },
    [tripId, sessionId],
  );

  const sendLead = useCallback(
    async (lead: LeadBody) => {
      const { status: code, body } = await api.sendToAdvisor(tripId, sessionId, undefined, lead);
      if (code === 200) {
        if (body.itinerary) setItinerary(body.itinerary);
        setStatus("sent");
      }
      return { status: code, body };
    },
    [tripId, sessionId],
  );

  // AA-1041 (performance): one value object per change of what it holds, so a provider render that changes none of it
  // (a retry counter, the keys the suggestion fetch tracks) re-renders no consumer. The map / list hover lives in its
  // own context (StopSyncProvider below): a hover re-renders the map and the day list only, not the whole planner.
  const value = useMemo<TripState>(() => ({
    sessionId,
    tripId,
    itinerary,
    filters,
    status,
    inTripComponentIds,
    narration,
    narrating,
    narrationSource,
    narrationNotice,
    searchQuery,
    searchResults,
    searching,
    searchNotice,
    countries,
    suggestions,
    focusDestinationId,
    focusDestination: setFocusDestinationId,
    previewCountry,
    setPreviewCountry,
    setFilters,
    runSearch,
    clearSearch,
    add,
    remove,
    reorder,
    undoOrder,
    undoReorder,
    reorderFailed,
    stopDetails,
    loadFailed,
    retryLoad,
    narrate,
    send,
    sendLead,
    coverage,
    customDays,
    unavailableStops,
    routePreview,
    setRoutePreview,
    applyRoute,
    applyChatProposal,
    addDay,
    removeDay,
    intent,
    ready,
  }), [
    sessionId,
    tripId,
    itinerary,
    filters,
    status,
    inTripComponentIds,
    narration,
    narrating,
    narrationSource,
    narrationNotice,
    searchQuery,
    searchResults,
    searching,
    searchNotice,
    countries,
    suggestions,
    focusDestinationId,
    setFocusDestinationId,
    previewCountry,
    setPreviewCountry,
    setFilters,
    runSearch,
    clearSearch,
    add,
    remove,
    reorder,
    undoOrder,
    undoReorder,
    reorderFailed,
    stopDetails,
    loadFailed,
    retryLoad,
    narrate,
    send,
    sendLead,
    coverage,
    customDays,
    unavailableStops,
    routePreview,
    setRoutePreview,
    applyRoute,
    applyChatProposal,
    addDay,
    removeDay,
    intent,
    ready,
  ]);

  return (
    <TripContext.Provider value={value}>
      <StopSyncProvider>{children}</StopSyncProvider>
    </TripContext.Provider>
  );
}

// AA-1041 (item 3): map / itinerary sync. The active stop is the hovered one, else the selected one. A context of its
// own (first-load / interaction performance): hovering a stop row or a marker changes this state many times a second,
// and only the map (MapView) and the day list (DayList) read it, so only they re-render.
export interface StopSyncState {
  activeStopId: string | null;
  selectedStop: StopSelection | null;
  hoverStop: (componentId: string | null) => void;
  selectStop: (componentId: string | null, from: SyncSource) => void;
}

const StopSyncContext = createContext<StopSyncState | null>(null);

function StopSyncProvider({ children }: { children: React.ReactNode }) {
  const [hoveredStop, setHoveredStop] = useState<string | null>(null);
  const [selectedStop, setSelectedStop] = useState<StopSelection | null>(null);
  const selectSeq = useRef(0);
  const selectStop = useCallback((componentId: string | null, from: SyncSource) => {
    selectSeq.current += 1;
    setSelectedStop(componentId ? { componentId, from, seq: selectSeq.current } : null);
  }, []);
  const activeStopId = pickActiveStop(hoveredStop, selectedStop);
  const value = useMemo<StopSyncState>(
    () => ({ activeStopId, selectedStop, hoverStop: setHoveredStop, selectStop }),
    [activeStopId, selectedStop, selectStop],
  );
  return <StopSyncContext.Provider value={value}>{children}</StopSyncContext.Provider>;
}

export function useStopSync(): StopSyncState {
  const ctx = useContext(StopSyncContext);
  if (!ctx) throw new Error("useStopSync must be used within TripProvider");
  return ctx;
}

export function useTrip(): TripState {
  const ctx = useContext(TripContext);
  if (!ctx) throw new Error("useTrip must be used within TripProvider");
  return ctx;
}
