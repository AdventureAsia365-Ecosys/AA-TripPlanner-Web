// AA-812 — /plan: the map planner (moved from "/" unchanged in behaviour). The query string carries
// what the visitor chose on the home search or a tour page:
//   ?country=Vietnam&days=7&travellers=2   → route finder pre-filled, map on that country
//   ?tour=<tour_id>&source=<source_id>[&ask=1] → that tour's whole route pinned (apply-route)
import type { Metadata } from "next";
import PlannerApp from "./PlannerApp";
import { parseIntent } from "@/lib/planIntent";

export const metadata: Metadata = {
  title: "Trip planner",
  description:
    "Browse Adventure Asia destinations on a map, pin the experiences that move you, and watch a day-by-day itinerary come together.",
  alternates: { canonical: "/plan" },
  // Personal planning state: keep it out of search results.
  robots: { index: false, follow: true },
};

export default function PlanPage({ searchParams }: { searchParams: Record<string, string | string[] | undefined> }) {
  return <PlannerApp intent={parseIntent(searchParams)} />;
}
