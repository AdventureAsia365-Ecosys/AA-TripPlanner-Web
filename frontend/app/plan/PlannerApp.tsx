"use client";

import { useEffect, useState } from "react";
import Header from "@/components/Header";
import MapView from "@/components/MapView";
import FilterChips from "@/components/FilterChips";
import TripPanel from "@/components/TripPanel";
import TourListPane from "@/components/TourListPane";
import PlanPrefill from "@/components/PlanPrefill";
import GuestResetNotice from "@/components/GuestResetNotice";
import { TripProvider, useTrip, type PlanIntent } from "@/lib/useTrip";
import { Icon } from "@/ui/Icon";

// Below this width we stack map/trip and toggle between them, so neither
// gets squeezed on a phone. Above it, the classic side-by-side layout.
const MOBILE_BREAKPOINT = 768;

// AA-812: Map | List switch over the map area. The map stays mounted under the list (Mapbox keeps
// its canvas), the list is an overlay.
function PaneToggle({ pane, setPane }: { pane: "map" | "list"; setPane: (p: "map" | "list") => void }) {
  const btn = (p: "map" | "list", label: string) => (
    <button
      onClick={() => setPane(p)}
      aria-pressed={pane === p}
      className={`aa-focus px-3.5 py-1.5 text-xs font-semibold transition ${
        pane === p ? "bg-k-ink text-k-bg" : "text-k-ink hover:bg-k-accent-tint"
      }`}
    >
      {label}
    </button>
  );
  return (
    <div className="absolute bottom-4 left-1/2 z-20 flex -translate-x-1/2 overflow-hidden rounded-full border border-k-line bg-k-card shadow-aa">
      {btn("map", "Map")}
      {btn("list", "List of tours")}
    </div>
  );
}

function Layout({ initialMobileView }: { initialMobileView: "map" | "trip" }) {
  const { itinerary } = useTrip();
  const [isMobile, setIsMobile] = useState(false);
  const [mobileView, setMobileView] = useState<"map" | "trip">(initialMobileView);
  const [pane, setPane] = useState<"map" | "list">("map");
  // "Ask an advisor" from a tour page: once its route is pinned, show the trip (send button).
  const onPrefilled = () => {
    if (initialMobileView === "trip") setMobileView("trip");
  };
  const mapArea = (
    <>
      <FilterChips />
      <MapView />
      {pane === "list" && <TourListPane />}
      <PaneToggle pane={pane} setPane={setPane} />
      <PlanPrefill onApplied={onPrefilled} />
    </>
  );

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
            {mapArea}
          </section>
          <aside
            className="border-l border-k-line bg-k-bg"
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
          {mapArea}
        </section>
        <aside
          className="bg-k-bg"
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
      <nav className="flex border-t border-k-line bg-k-card">
        <button
          onClick={() => setMobileView("map")}
          className={`aa-focus flex-1 py-3 text-sm font-semibold transition ${
            mobileView === "map"
              ? "bg-k-accent-tint text-k-accent-deep"
              : "text-k-muted"
          }`}
        >
          <Icon name="map" className="mr-1 inline h-4 w-4 align-[-3px]" />
          Explore
        </button>
        <button
          onClick={() => setMobileView("trip")}
          className={`aa-focus relative flex-1 py-3 text-sm font-semibold transition ${
            mobileView === "trip"
              ? "bg-k-accent-tint text-k-accent-deep"
              : "text-k-muted"
          }`}
        >
          <Icon name="route" className="mr-1 inline h-4 w-4 align-[-3px]" />
          Your trip
          {itinerary.length > 0 && (
            <span className="ml-1.5 rounded-full bg-k-ink px-1.5 py-0.5 text-[10px] font-bold text-k-bg">
              {new Set(itinerary.map((d) => d.day)).size}
            </span>
          )}
        </button>
      </nav>
    </div>
  );
}

export default function PlannerApp({ intent }: { intent: PlanIntent }) {
  // AA-837: the planner follows the site theme (ui/core/tokens.css). Its surfaces and form controls use
  // the k-* tokens, so browser-styled controls (country select, days input) stay readable in dark.
  // The Mapbox basemap stays the light outdoors style in both themes (map imagery, not UI text).
  return (
    <TripProvider initialIntent={intent}>
      <Layout initialMobileView={intent.ask ? "trip" : "map"} />
      <GuestResetNotice />
    </TripProvider>
  );
}
