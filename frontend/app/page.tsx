"use client";

import { useEffect, useState } from "react";
import Header from "@/components/Header";
import MapView from "@/components/MapView";
import FilterChips from "@/components/FilterChips";
import TripPanel from "@/components/TripPanel";
import { TripProvider, useTrip } from "@/lib/useTrip";

// Below this width we stack map/trip and toggle between them, so neither
// gets squeezed on a phone. Above it, the classic side-by-side layout.
const MOBILE_BREAKPOINT = 768;

function Layout() {
  const { itinerary } = useTrip();
  const [isMobile, setIsMobile] = useState(false);
  const [mobileView, setMobileView] = useState<"map" | "trip">("map");

  useEffect(() => {
    const mq = window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT - 1}px)`);
    const onChange = () => setIsMobile(mq.matches);
    onChange();
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  // Desktop: side-by-side. The map's absolute container needs a sized,
  // positioned ancestor (load-bearing inline styles for Mapbox sizing).
  if (!isMobile) {
    return (
      <div style={{ display: "flex", flexDirection: "column", height: "100vh", width: "100vw" }}>
        <Header />
        <main className="overflow-hidden" style={{ display: "flex", flex: 1, minHeight: 0 }}>
          <section style={{ position: "relative", flex: 1, height: "100%" }}>
            <FilterChips />
            <MapView />
          </section>
          <aside
            className="border-l border-aa-line bg-aa-offwhite"
            style={{ height: "100%", width: "25rem", flexShrink: 0, overflowY: "auto" }}
          >
            <TripPanel />
          </aside>
        </main>
      </div>
    );
  }

  // Mobile: one pane at a time with a bottom toggle. Both panes stay mounted
  // (map hidden with visibility, not unmounted) so Mapbox keeps its canvas.
  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh", width: "100vw" }}>
      <Header />
      <main className="overflow-hidden" style={{ position: "relative", flex: 1, minHeight: 0 }}>
        <section
          style={{
            position: "absolute",
            inset: 0,
            visibility: mobileView === "map" ? "visible" : "hidden",
          }}
        >
          <FilterChips />
          <MapView />
        </section>
        <aside
          className="bg-aa-offwhite"
          style={{
            position: "absolute",
            inset: 0,
            overflowY: "auto",
            visibility: mobileView === "trip" ? "visible" : "hidden",
          }}
        >
          <TripPanel />
        </aside>
      </main>

      {/* Bottom toggle */}
      <nav className="flex border-t border-aa-line bg-white">
        <button
          onClick={() => setMobileView("map")}
          className={`aa-focus flex-1 py-3 text-sm font-semibold transition ${
            mobileView === "map"
              ? "bg-aa-gold-soft text-aa-gold-dark"
              : "text-aa-muted"
          }`}
        >
          🗺️ Explore
        </button>
        <button
          onClick={() => setMobileView("trip")}
          className={`aa-focus relative flex-1 py-3 text-sm font-semibold transition ${
            mobileView === "trip"
              ? "bg-aa-gold-soft text-aa-gold-dark"
              : "text-aa-muted"
          }`}
        >
          🧳 Your trip
          {itinerary.length > 0 && (
            <span className="ml-1.5 rounded-full bg-aa-ink px-1.5 py-0.5 text-[10px] font-bold text-white">
              {itinerary.length}
            </span>
          )}
        </button>
      </nav>
    </div>
  );
}

export default function Home() {
  return (
    <TripProvider>
      <Layout />
    </TripProvider>
  );
}
