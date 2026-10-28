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

// What My Trips shows for a GET /api/me answer. 401 = signed out (sign-in prompt). Any other failure (502 upstream,
// 503 sign-in not configured, a network error = status 0) is "error": the traveller may still hold a session, so the
// error state offers both "Try again" and "Sign out" (S251 follow-up of the S250 live run, which found no way to sign
// out while the list failed to load).
export type MeOutcome = "ok" | "signed_out" | "error";

// AA-995 (S255 final review): a signed-out visit to /trips asked GET /api/me, which answers 401, and the browser logs
// every 4xx as a console error. /api/auth/status (always 200, shared with the header link) already says "signed out",
// so My Trips shows the sign-in prompt without the second request. Only a real answer counts: a failed status call,
// or a deployment without sign-in (available false: /api/me answers 503 there, the error state), still asks /api/me.
export function signedOutWithoutMe(s: { answered: boolean; signedIn: boolean; available: boolean }): boolean {
  return s.answered && !s.signedIn && s.available;
}

export function meOutcome(status: number): MeOutcome {
  if (status === 401) return "signed_out";
  if (status >= 200 && status < 300) return "ok";
  return "error";
}
