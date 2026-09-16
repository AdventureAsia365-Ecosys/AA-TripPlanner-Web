"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import * as api from "./api";
import type {
  BrowseFilters,
  CountryOption,
  DestinationPin,
  ItineraryDay,
} from "./types";
import type { Suggestion } from "./api";

// Guest identity: a random session + trip id, held in memory only (guests
// are not persisted — lost on tab close, per requirements).
function randomId(): string {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

interface TripState {
  sessionId: string;
  tripId: string;
  itinerary: ItineraryDay[];
  filters: BrowseFilters;
  status: string;
  inTripComponentIds: Set<string>;
  narration: string;
  narrating: boolean;
  // Where the narration came from: 'master' (AA's authored itinerary text),
  // 'llm' (AI-written), 'mixed', or "" before any narrate call.
  narrationSource: string;
  // Semantic search: when searchResults is non-null the map shows this
  // ranked set instead of tiles-by-bounds; null means "browse mode".
  searchQuery: string;
  searchResults: DestinationPin[] | null;
  searching: boolean;
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
  narrate: (mode?: "compose" | "renarrate") => Promise<void>;
  send: (customer?: { name: string; phone: string; email: string }) => Promise<
    { ok: boolean; needsRegistration: boolean }
  >;
}

const TripContext = createContext<TripState | null>(null);

const STORAGE_KEY = "aa-tripplanner:ids";

// Persist the guest session + trip id so a reload restores the same trip
// (the backend keeps the event log by trip_id). Falls back to fresh ids if
// storage is unavailable (SSR, privacy mode).
function loadOrCreateIds(): { sessionId: string; tripId: string } {
  if (typeof window !== "undefined") {
    try {
      const raw = window.localStorage.getItem(STORAGE_KEY);
      if (raw) {
        const parsed = JSON.parse(raw);
        if (parsed?.sessionId && parsed?.tripId) return parsed;
      }
    } catch {
      /* ignore */
    }
  }
  const ids = { sessionId: randomId(), tripId: randomId() };
  if (typeof window !== "undefined") {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(ids));
    } catch {
      /* ignore */
    }
  }
  return ids;
}

export function TripProvider({ children }: { children: React.ReactNode }) {
  const idsRef = useRef<{ sessionId: string; tripId: string }>();
  if (!idsRef.current) {
    idsRef.current = loadOrCreateIds();
  }
  const { sessionId, tripId } = idsRef.current;

  const [itinerary, setItinerary] = useState<ItineraryDay[]>([]);
  const [filters, setFilters] = useState<BrowseFilters>({});
  const [status, setStatus] = useState<string>("draft");
  const [countries, setCountries] = useState<CountryOption[]>([]);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [focusDestinationId, setFocusDestinationId] = useState<string | null>(
    null,
  );
  const [previewCountry, setPreviewCountry] = useState<string | null>(null);
  const [narration, setNarration] = useState<string>("");
  const [narrationSource, setNarrationSource] = useState<string>("");
  const [narrating, setNarrating] = useState<boolean>(false);
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [searchResults, setSearchResults] = useState<DestinationPin[] | null>(
    null,
  );
  const [searching, setSearching] = useState<boolean>(false);
  const filtersRef = useRef<BrowseFilters>({});
  filtersRef.current = filters;

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
  useEffect(() => {
    let active = true;
    api.fetchTrip(tripId).then((res) => {
      if (active && res?.itinerary && res.itinerary.length > 0) {
        setItinerary(res.itinerary);
        if (res.status) setStatus(res.status);
      }
    });
    return () => {
      active = false;
    };
  }, [tripId]);

  // Refresh next-to-pin suggestions whenever the trip's component set
  // changes. Keyed on the sorted component ids so a reorder doesn't refetch.
  const suggestKey = itinerary
    .map((d) => d.component_id)
    .sort()
    .join(",");
  useEffect(() => {
    if (!suggestKey) {
      setSuggestions([]);
      return;
    }
    let active = true;
    api.fetchSuggestions(tripId).then((s) => {
      if (active) setSuggestions(s);
    });
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [suggestKey, tripId]);

  const add = useCallback(
    async (componentId: string) => {
      const res = await api.addComponent(tripId, sessionId, componentId);
      if (res.itinerary) setItinerary(res.itinerary);
    },
    [tripId, sessionId],
  );

  const remove = useCallback(
    async (componentId: string) => {
      const res = await api.removeComponent(tripId, sessionId, componentId);
      if (res.itinerary) setItinerary(res.itinerary);
    },
    [tripId, sessionId],
  );

  const reorder = useCallback(
    async (orderedIds: string[]) => {
      const res = await api.reorder(tripId, sessionId, orderedIds);
      if (res.itinerary) setItinerary(res.itinerary);
    },
    [tripId, sessionId],
  );

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
      const results = await api.search(query, filtersRef.current);
      setSearchResults(results);
    } finally {
      setSearching(false);
    }
  }, []);

  const clearSearch = useCallback(() => {
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
      } finally {
        setNarrating(false);
      }
    },
    [tripId, sessionId],
  );

  const send = useCallback(
    async (customer?: { name: string; phone: string; email: string }) => {
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
      return { ok: false, needsRegistration: false };
    },
    [tripId, sessionId],
  );

  const value: TripState = {
    sessionId,
    tripId,
    itinerary,
    filters,
    status,
    inTripComponentIds,
    narration,
    narrating,
    narrationSource,
    searchQuery,
    searchResults,
    searching,
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
    narrate,
    send,
  };

  return <TripContext.Provider value={value}>{children}</TripContext.Provider>;
}

export function useTrip(): TripState {
  const ctx = useContext(TripContext);
  if (!ctx) throw new Error("useTrip must be used within TripProvider");
  return ctx;
}
