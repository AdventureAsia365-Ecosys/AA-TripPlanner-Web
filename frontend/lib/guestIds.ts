// AA-827: the browser's guest ids, kept in localStorage so a reload restores the trip. They are
// identifiers, not credentials: what proves the session is the HttpOnly guest cookie (guestToken.ts),
// which client JS never sees. No React import, so `npm test` can run it.

export const STORAGE_KEY = "aa-tripplanner:ids";
export const RESET_NOTICE_KEY = "aa-tripplanner:reset-notice";
export const RESET_NOTICE = "This trip belongs to another browser — starting a new trip.";

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

let resetting = false;

// The stored ids are unusable (401: the session is bound to another token; 403: the trip belongs to
// another session). Forget them, leave a notice for the next page load, and reload with a new trip.
export function resetGuestIds(): void {
  if (typeof window === "undefined" || resetting) return;
  resetting = true;
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* ignore */
  }
  try {
    window.sessionStorage.setItem(RESET_NOTICE_KEY, RESET_NOTICE);
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
