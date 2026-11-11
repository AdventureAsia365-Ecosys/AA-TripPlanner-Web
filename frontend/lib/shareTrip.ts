// AA-1041 (R7 slice 1, item 6) — the read-only share / print view of a trip.
//
// The link is `/plan/shared#v=1&s=<id>.<id>…&d=<added days>` (`/{slug}/plan/shared#…` on an agency tenant's planner).
// It carries ONLY component ids (in the trip's order) and the codes of the days the traveller added on top of the AA
// tours; the page reads the stops' names, places and photos from the CIS catalog (GET /browse/stops), so a link can
// never make the page show text someone typed. It is in the fragment: the browser never sends it to the server, no trip
// id, session or guest token is in it, and nothing is stored. Anyone with the link sees the same stops; a stop whose
// tour is not served any more is left out with a note.
//
// Added days: `n<i>` = an extra night after stop i (0-based), `f` = a free day, `e` = an extra day at the end.

export const SHARE_VERSION = "1";
export const MAX_SHARED_STOPS = 50;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

export type AddedDayKind = "extra_night" | "free_day" | "extend_end";

export interface SharedTrip {
  ids: string[];
  added: { kind: AddedDayKind; afterIndex: number | null }[];
}

/** The fragment of a trip (without the leading "#"). */
export function encodeShare(
  itinerary: readonly { component_id: string }[],
  customDays: readonly { kind: string; after_component_id: string | null }[] = [],
): string {
  const ids = itinerary.map((d) => d.component_id.toLowerCase()).filter((id) => UUID_RE.test(id)).slice(0, MAX_SHARED_STOPS);
  const codes: string[] = [];
  for (const c of customDays) {
    if (c.kind === "extra_night") {
      const i = c.after_component_id ? ids.indexOf(c.after_component_id.toLowerCase()) : -1;
      if (i >= 0) codes.push(`n${i}`);
    } else if (c.kind === "free_day") codes.push("f");
    else if (c.kind === "extend_end") codes.push("e");
  }
  const p = new URLSearchParams({ v: SHARE_VERSION, s: ids.join(".") });
  if (codes.length) p.set("d", codes.join(","));
  return p.toString();
}

/** The trip of a fragment; anything malformed is dropped (an empty trip, never an error). */
export function decodeShare(fragment: string): SharedTrip {
  const p = new URLSearchParams(fragment.replace(/^#/, ""));
  if (p.get("v") !== SHARE_VERSION) return { ids: [], added: [] };
  const ids = [...new Set((p.get("s") ?? "").split(".").map((x) => x.trim().toLowerCase()))]
    .filter((id) => UUID_RE.test(id))
    .slice(0, MAX_SHARED_STOPS);
  const added: SharedTrip["added"] = [];
  for (const code of (p.get("d") ?? "").split(",").slice(0, 30)) {
    if (code === "f") added.push({ kind: "free_day", afterIndex: null });
    else if (code === "e") added.push({ kind: "extend_end", afterIndex: null });
    else if (/^n\d{1,2}$/.test(code)) {
      const i = Number(code.slice(1));
      if (i < ids.length) added.push({ kind: "extra_night", afterIndex: i });
    }
  }
  return { ids, added };
}

/** The share page's path for the planner the traveller is on (an agency tenant's own path, else /plan). */
export function sharePath(tenantSlug: string | null): string {
  return tenantSlug ? `/${tenantSlug}/plan/shared` : "/plan/shared";
}

/** The full link to copy. */
export function shareUrl(origin: string, tenantSlug: string | null, fragment: string): string {
  return `${origin.replace(/\/$/, "")}${sharePath(tenantSlug)}#${fragment}`;
}

// --- calendar (.ics) --------------------------------------------------------------------------------------------

export interface CalendarDay {
  day: number;
  title: string;
  description?: string;
}

function icsText(s: string): string {
  return s.replace(/\\/g, "\\\\").replace(/;/g, "\\;").replace(/,/g, "\\,").replace(/\r?\n/g, "\\n");
}

// RFC 5545 3.1: lines longer than 75 octets are folded (CRLF + one space).
function fold(line: string): string {
  const out: string[] = [];
  let rest = line;
  while (rest.length > 74) {
    out.push(rest.slice(0, 74));
    rest = " " + rest.slice(74);
  }
  out.push(rest);
  return out.join("\r\n");
}

function ymd(d: Date): string {
  return d.toISOString().slice(0, 10).replace(/-/g, "");
}

/** A start date typed in an <input type="date"> ("2027-03-14"), as a UTC date, or null. */
export function parseStartDate(value: string): Date | null {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
  const d = new Date(`${value}T00:00:00Z`);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** One all-day event per trip day from `start` (day 1 = start). `stamp` is the file's DTSTAMP (now, by default). */
export function tripIcs(days: readonly CalendarDay[], start: Date, calendarName: string, stamp: Date = new Date()): string {
  const dtstamp = stamp.toISOString().replace(/[-:]/g, "").replace(/\.\d{3}Z$/, "Z");
  const lines = [
    "BEGIN:VCALENDAR",
    "VERSION:2.0",
    "PRODID:-//TripPlanner//Shared trip//EN",
    "CALSCALE:GREGORIAN",
    `X-WR-CALNAME:${icsText(calendarName)}`,
  ];
  for (const d of days) {
    const from = new Date(start.getTime() + (d.day - 1) * 86_400_000);
    const to = new Date(from.getTime() + 86_400_000);
    lines.push(
      "BEGIN:VEVENT",
      `UID:${ymd(start)}-day-${d.day}@tripplanner`,
      `DTSTAMP:${dtstamp}`,
      `DTSTART;VALUE=DATE:${ymd(from)}`,
      `DTEND;VALUE=DATE:${ymd(to)}`,
      `SUMMARY:${icsText(`Day ${d.day}: ${d.title}`)}`,
    );
    if (d.description) lines.push(`DESCRIPTION:${icsText(d.description)}`);
    lines.push("END:VEVENT");
  }
  lines.push("END:VCALENDAR");
  return lines.map(fold).join("\r\n") + "\r\n";
}
