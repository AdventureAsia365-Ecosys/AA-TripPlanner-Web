// AA-827: the browser's guest ids, kept in localStorage so a reload restores the trip. They are
// identifiers, not credentials: what proves the session is the HttpOnly guest cookie (guestToken.ts),
// which client JS never sees. No React import, so `npm test` can run it.

export const STORAGE_KEY = "aa-tripplanner:ids";
export const RESET_NOTICE_KEY = "aa-tripplanner:reset-notice";
export const RESET_NOTICE = "This trip belongs to another browser — starting a new trip.";
export const REPLACED_NOTICE = "This trip was replaced in another tab — showing the current one.";
export const COOKIES_NOTICE =
  "This browser is not keeping the planner's cookie, so your changes cannot be saved. Allow cookies for this site to keep your trip.";
// When this tab last reset its ids (ms since epoch, per tab). A second reset within RESET_LOOP_MS
// means the new trip failed straight away too: the cookie is not being kept, so stop reloading.
export const LAST_RESET_KEY = "aa-tripplanner:last-reset";
export const RESET_LOOP_MS = 60_000;
// The BFF's 401 error code when a token minted on that request was refused (guestToken.COOKIES_REQUIRED;
// guestToken.ts is server-only, so the client keeps its own copy, pinned equal by a test).
export const COOKIES_REQUIRED = "cookies_required";
// A notice shown while the page stays (GuestResetNotice listens for it).
export const NOTICE_EVENT = "aa-tripplanner:notice";

export interface GuestIds {
  sessionId: string;
  tripId: string;
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

// Canonical lowercase UUID, the only id shape the assembly Lambda accepts.
export function isUuid(value: unknown): value is string {
  return typeof value === "string" && UUID_RE.test(value);
}

// RFC 4122 v4 from 16 random bytes.
export function uuidFromBytes(bytes: Uint8Array): string {
  const b = Uint8Array.from(bytes.subarray(0, 16));
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

// crypto.randomUUID is missing outside secure contexts (plain-http LAN dev); getRandomValues is not.
export function randomUuid(): string {
  if (typeof crypto !== "undefined" && crypto.randomUUID) return crypto.randomUUID();
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return uuidFromBytes(bytes);
}

// True when the stored localStorage value still holds exactly these ids. Another tab may already have
// replaced them with a fresh trip; that one must not be deleted.
export function storedIdsMatch(raw: string | null, ids: GuestIds): boolean {
  if (!raw) return false;
  try {
    const parsed = JSON.parse(raw);
    return parsed?.sessionId === ids.sessionId && parsed?.tripId === ids.tripId;
  } catch {
    return false;
  }
}

export function isResetLoop(lastReset: string | null, now: number): boolean {
  const t = Number(lastReset);
  return Number.isFinite(t) && t > 0 && now - t >= 0 && now - t < RESET_LOOP_MS;
}

export function showGuestNotice(text: string): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent<string>(NOTICE_EVENT, { detail: text }));
}

let resetting = false;

// The ids that failed are unusable (401: the session is bound to another token; 403: the trip
// belongs to another session). Forget them (only if localStorage still holds them), leave a notice
// for the next page load, and reload: the page then starts a new trip, or picks up the newer trip
// another tab has already stored. A second reset in this tab within RESET_LOOP_MS does not reload:
// it says cookies are needed instead.
export function resetGuestIds(failed: GuestIds): void {
  if (typeof window === "undefined" || resetting) return;
  const now = Date.now();
  let last: string | null = null;
  try {
    last = window.sessionStorage.getItem(LAST_RESET_KEY);
  } catch {
    /* ignore */
  }
  if (isResetLoop(last, now)) {
    showGuestNotice(COOKIES_NOTICE);
    return;
  }
  resetting = true;
  let notice = RESET_NOTICE;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (storedIdsMatch(raw, failed)) window.localStorage.removeItem(STORAGE_KEY);
    else if (raw) notice = REPLACED_NOTICE;
  } catch {
    /* ignore */
  }
  try {
    window.sessionStorage.setItem(RESET_NOTICE_KEY, notice);
    window.sessionStorage.setItem(LAST_RESET_KEY, String(now));
  } catch {
    /* ignore */
  }
  window.location.reload();
}

// The notice left by resetGuestIds, once.
export function takeResetNotice(): string | null {
  if (typeof window === "undefined") return null;
  try {
    const text = window.sessionStorage.getItem(RESET_NOTICE_KEY);
    if (text) window.sessionStorage.removeItem(RESET_NOTICE_KEY);
    return text;
  } catch {
    return null;
  }
}
