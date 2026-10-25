// AA-908 — the pure half of My Trips (components/MyTrips.tsx): the shape GET /api/me returns per trip and how a trip
// is labelled. No React import, so `npm test` runs it.
import { formatDays } from "./text.ts";

export interface MyTrip {
  trip_id: string;
  session_id: string;
  status: "draft" | "sent" | string;
  stops: number;
  unavailable_stops: number;
  days: number;
  stop_names: string[];
  date_start: string | null;
  date_end: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export function tripTitle(t: MyTrip): string {
  if (!t.stop_names.length) return "A trip with no stops yet";
  return t.stop_names.join(" · ") + (t.stops > t.stop_names.length ? " …" : "");
}

export function tripStatusLabel(status: string): string {
  return status === "sent" ? "Sent to an advisor" : "Draft";
}

export function tripFacts(t: MyTrip): string {
  const parts = [formatDays(t.days), `${t.stops} ${t.stops === 1 ? "stop" : "stops"}`];
  if (t.unavailable_stops > 0) parts.push(`${t.unavailable_stops} no longer offered`);
  return parts.join(" · ");
}

// "Updated 25 Oct 2026" in English (UI opens in English), UTC so the server and every browser agree.
export function updatedLabel(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return `Updated ${d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" })}`;
}
