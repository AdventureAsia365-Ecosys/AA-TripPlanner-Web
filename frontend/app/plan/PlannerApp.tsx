"use client";

import { useEffect, useState } from "react";
import dynamic from "next/dynamic";
import Header, { type HeaderAgency } from "@/components/Header";
import FilterChips from "@/components/FilterChips";
import TripPanel from "@/components/TripPanel";
import TourListPane from "@/components/TourListPane";
import PlanPrefill from "@/components/PlanPrefill";
import GuestResetNotice from "@/components/GuestResetNotice";
import { TripProvider, useTrip, type PlanIntent } from "@/lib/useTrip";
import { setPlannerTenant, type PlannerAgency } from "@/lib/plannerTenant";
import { reloadIntoPlannerDocument } from "@/lib/plannerNav";
import { whenIdle } from "@/lib/idle";
import { Icon } from "@/ui/Icon";
import { LoadingState } from "@/ui/States";

// AA-1041 (first-load performance, S277 Lighthouse check): the map and the Mapbox library (about 1.4 MB of script, the
// bulk of the planner's first load) are their own chunk, asked for once the planner has hydrated, painted and gone idle
// (`whenIdle`, lib/idle.ts). Before, the page could not hydrate (search box, route finder, trip panel) until Mapbox was
// downloaded and evaluated, and its evaluation, the hydration and the map's construction could land in one long
// main-thread task. The map area keeps its place with a neutral box until the map draws (absolute, so nothing moves).
function MapPending() {
  return <div aria-hidden data-testid="map-loading" className="absolute inset-0 bg-k-neutral-bg" />;
}
const MapView = dynamic(() => import("@/components/MapView"), { ssr: false, loading: MapPending });

// AA-1041: the planning chat is fetched and mounted the first time its tab (or the phone's Chat view) opens, then
// stays mounted so the conversation survives a tab switch. Nothing of it runs on the planner's first load.
const PlanChat = dynamic(() => import("@/components/PlanChat"), {
  ssr: false,
  loading: () => (
    <div className="p-4">
      <LoadingState label="Loading the chat" rows={2} compact />
    </div>
  ),
});

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

// AA-1041 slice 2: the desktop side panel holds the trip and the planning chat as two tabs. Both stay mounted (the
// chat keeps its conversation while the traveller looks at the trip); the hidden one is `hidden`.
type SideTab = "trip" | "chat";

function SideTabs({ tab, setTab }: { tab: SideTab; setTab: (t: SideTab) => void }) {
  const btn = (t: SideTab, label: string, icon: string) => (
    <button
      type="button"
      role="tab"
      id={`side-tab-${t}`}
      aria-selected={tab === t}
      aria-controls={`side-panel-${t}`}
      onClick={() => setTab(t)}
      className={`aa-focus flex flex-1 items-center justify-center gap-1.5 py-2.5 text-xs font-semibold transition ${
        tab === t ? "border-b-2 border-k-accent text-k-ink" : "border-b-2 border-transparent text-k-muted hover:text-k-ink"
      }`}
    >
      <Icon name={icon} className="h-4 w-4" />
      {label}
    </button>
  );
  return (
    <div role="tablist" aria-label="Trip panel" className="flex border-b border-k-line bg-k-card">
      {btn("trip", "Your trip", "route")}
      {btn("chat", "Plan with chat", "chat")}
    </div>
  );
}

function Layout({ initialMobileView, agency }: { initialMobileView: "map" | "trip"; agency: HeaderAgency | null }) {
  const { itinerary } = useTrip();
  const [isMobile, setIsMobile] = useState(false);
  const [mobileView, setMobileView] = useState<"map" | "trip" | "chat">(initialMobileView);
  const [sideTab, setSideTab] = useState<SideTab>("trip");
  // AA-1041: the chat panel mounts on its first opening and stays mounted after (see PlanChat above).
  const [chatOpened, setChatOpened] = useState(false);
  const showSideTab = (t: SideTab) => {
    if (t === "chat") setChatOpened(true);
    setSideTab(t);
  };
  const showMobileView = (v: "map" | "trip" | "chat") => {
    if (v === "chat") setChatOpened(true);
    setMobileView(v);
  };
  const [pane, setPane] = useState<"map" | "list">("map");
  // AA-1041: the map is mounted (and its chunk fetched) after the first paint, when the main thread is idle.
  const [mapWanted, setMapWanted] = useState(false);
  useEffect(() => whenIdle(() => setMapWanted(true)), []);
  // "Ask an advisor" from a tour page: once its route is pinned, show the trip (send button).
  const onPrefilled = () => {
    if (initialMobileView === "trip") setMobileView("trip");
  };
  const mapArea = (
    <>
      <FilterChips />
      {mapWanted ? <MapView /> : <MapPending />}
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
        <Header agency={agency} />
        <main className="overflow-hidden" style={{ display: "flex", flex: 1, minHeight: 0 }}>
          <section style={{ position: "relative", flex: 1, height: "100%" }}>
            {mapArea}
          </section>
          <aside
            className="border-l border-k-line bg-k-bg"
            style={{ height: "100%", width: "25rem", flexShrink: 0, display: "flex", flexDirection: "column" }}
          >
            <SideTabs tab={sideTab} setTab={showSideTab} />
            <div id="side-panel-trip" role="tabpanel" aria-labelledby="side-tab-trip" hidden={sideTab !== "trip"}
              style={{ flex: 1, minHeight: 0, overflowY: "auto" }}>
              <TripPanel />
            </div>
            <div id="side-panel-chat" role="tabpanel" aria-labelledby="side-tab-chat" hidden={sideTab !== "chat"}
              style={{ flex: 1, minHeight: 0 }}>
              {chatOpened && <PlanChat />}
            </div>
          </aside>
        </main>
      </div>
    );
  }

  // Mobile: one pane at a time with a bottom toggle. Both panes stay mounted
  // (map hidden with visibility, not unmounted) so Mapbox keeps its canvas.
  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh", width: "100vw" }}>
      <Header agency={agency} />
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
          {/* AA-1041: "Show on map" on a phone switches to the map, where the stop is highlighted. */}
          <TripPanel onShowOnMap={() => setMobileView("map")} />
        </aside>
        {/* AA-1041 slice 2: the planning chat, its own view on a phone (mounted, so the conversation stays). */}
        <section
          aria-label="Plan with chat"
          style={{
            position: "absolute",
            inset: 0,
            visibility: mobileView === "chat" ? "visible" : "hidden",
          }}
        >
          {chatOpened && <PlanChat />}
        </section>
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
        <button
          onClick={() => showMobileView("chat")}
          data-testid="mobile-chat-tab"
          className={`aa-focus flex-1 py-3 text-sm font-semibold transition ${
            mobileView === "chat"
              ? "bg-k-accent-tint text-k-accent-deep"
              : "text-k-muted"
          }`}
        >
          <Icon name="chat" className="mr-1 inline h-4 w-4 align-[-3px]" />
          Chat
        </button>
      </nav>
    </div>
  );
}

export default function PlannerApp({
  intent,
  tenantSlug = null,
  agency = null,
}: {
  intent: PlanIntent;
  tenantSlug?: string | null;
  agency?: PlannerAgency | null;
}) {
  // AA-1038 R5 slice 2: a /{slug}/plan page names its tenant before the trip state loads (the ids key and the
  // x-tp-tenant-slug header of every /api/trip call, lib/plannerTenant.ts). The bare /plan passes none. Set during
  // render on purpose: TripProvider's lazy state init (below) reads the ids key in this same render.
  setPlannerTenant(tenantSlug, agency);
  // AA-1038 R5 hosting: the web ACL hands the browser its AWS WAF token on a document load of a planner page. Every
  // in-site link into the planner is a full page load (ui/SiteLink.tsx); this catches any other client-side way in
  // (a redirect, back / forward) with one reload into a planner document (lib/plannerNav.ts).
  useEffect(() => {
    reloadIntoPlannerDocument();
  }, []);
  // AA-1094 (S278 audit M21): the tenant is this page's only. Leaving the planner without a document load (back /
  // forward to an AA page) must not keep the agency's slug for AA's pages (their account link, their /api/trip calls).
  // Set again in the effect too, so a development double mount does not leave it cleared.
  const agencyName = agency?.name ?? null;
  const agencyBadge = agency?.brandingBadge ?? false;
  useEffect(() => {
    setPlannerTenant(tenantSlug, agencyName === null ? null : { name: agencyName, brandingBadge: agencyBadge });
    return () => setPlannerTenant(null);
  }, [tenantSlug, agencyName, agencyBadge]);
  const headerAgency = tenantSlug && agencyName ? { slug: tenantSlug, name: agencyName } : null;
  // AA-837: the planner follows the site theme (ui/core/tokens.css). Its surfaces and form controls use
  // the k-* tokens, so browser-styled controls (country select, days input) stay readable in dark.
  // The Mapbox basemap stays the light outdoors style in both themes (map imagery, not UI text).
  return (
    <TripProvider initialIntent={intent}>
      <Layout initialMobileView={intent.ask ? "trip" : "map"} agency={headerAgency} />
      <GuestResetNotice />
    </TripProvider>
  );
}
