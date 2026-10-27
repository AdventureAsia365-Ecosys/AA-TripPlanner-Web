// AA-682 S5 — the pure half of My Payments (components/MyPayments.tsx and app/api/me/payments): the
// booking.customer.v1 shapes AA-Booking returns (root docs/contracts/booking.customer.v1.openapi.yaml), the
// allow-list the BFF forwards, and how amounts, dates and schedule chips are labelled. No React import, so
// `npm test` runs it.

export type InvoiceStatus = "sent" | "partially_paid" | "paid" | "cancelled";
export type ScheduleState = "scheduled" | "due_soon" | "paid" | "overdue" | "cancelled";

export interface ScheduleRow {
  seq: number;
  kind: string;
  amount: string;
  due_date: string;
  remaining_amount: string;
  state: ScheduleState | string;
}

export interface Payment {
  ref: string;
  kind: "receipt" | "refund" | string;
  amount: string;
  currency: string;
  method: string;
  received_on: string;
}

export interface TripRef {
  code: string;
  planner_trip_id: string | null;
  start_date: string | null;
  end_date: string | null;
}

export interface Invoice {
  invoice_number: string;
  status: InvoiceStatus | string;
  document_type: string;
  currency: string;
  total_amount: string;
  paid_amount: string;
  balance: string | null;
  next_due_date: string | null;
  overdue_amount: string;
  issued_at: string | null;
  trip: TripRef;
  schedule: ScheduleRow[];
  payments: Payment[];
  payment_instructions: string | null;
}

// ── The BFF's allow-list: only the contract's fields reach the browser, whatever the upstream sends ──────────────

function str(v: unknown): string {
  return typeof v === "string" ? v : "";
}

function strOrNull(v: unknown): string | null {
  return typeof v === "string" && v ? v : null;
}

function list(v: unknown): Record<string, unknown>[] {
  return Array.isArray(v) ? v.filter((x): x is Record<string, unknown> => !!x && typeof x === "object") : [];
}

export function pickInvoice(raw: Record<string, unknown>): Invoice {
  const trip = (raw.trip && typeof raw.trip === "object" ? raw.trip : {}) as Record<string, unknown>;
  return {
    invoice_number: str(raw.invoice_number),
    status: str(raw.status),
    document_type: str(raw.document_type),
    currency: str(raw.currency),
    total_amount: str(raw.total_amount),
    paid_amount: str(raw.paid_amount),
    balance: strOrNull(raw.balance),
    next_due_date: strOrNull(raw.next_due_date),
    overdue_amount: str(raw.overdue_amount),
    issued_at: strOrNull(raw.issued_at),
    trip: {
      code: str(trip.code),
      planner_trip_id: strOrNull(trip.planner_trip_id),
      start_date: strOrNull(trip.start_date),
      end_date: strOrNull(trip.end_date),
    },
    schedule: list(raw.schedule).map((s) => ({
      seq: typeof s.seq === "number" ? s.seq : 0,
      kind: str(s.kind),
      amount: str(s.amount),
      due_date: str(s.due_date),
      remaining_amount: str(s.remaining_amount),
      state: str(s.state),
    })),
    payments: list(raw.payments).map((p) => ({
      ref: str(p.ref),
      kind: str(p.kind),
      amount: str(p.amount),
      currency: str(p.currency),
      method: str(p.method),
      received_on: str(p.received_on),
    })),
    payment_instructions: strOrNull(raw.payment_instructions),
  };
}

export function pickPayments(body: unknown): Invoice[] {
  const invoices = body && typeof body === "object" ? (body as Record<string, unknown>).invoices : undefined;
  return list(invoices).map(pickInvoice).filter((i) => i.invoice_number);
}

// ── Labels ───────────────────────────────────────────────────────────────────────────────────────────────────────

export type ChipTone = "neutral" | "info" | "success" | "danger";

export function scheduleChip(state: string): { label: string; tone: ChipTone } {
  switch (state) {
    case "paid":
      return { label: "Paid", tone: "success" };
    case "due_soon":
      return { label: "Due soon", tone: "info" };
    case "overdue":
      return { label: "Overdue", tone: "danger" };
    case "cancelled":
      return { label: "Cancelled", tone: "neutral" };
    default:
      return { label: "Scheduled", tone: "neutral" };
  }
}

export function invoiceStatus(status: string): { label: string; tone: ChipTone } {
  switch (status) {
    case "paid":
      return { label: "Paid in full", tone: "success" };
    case "partially_paid":
      return { label: "Partly paid", tone: "info" };
    case "cancelled":
      return { label: "Cancelled", tone: "neutral" };
    default:
      return { label: "To pay", tone: "info" };
  }
}

const KIND: Record<string, string> = { deposit: "Deposit", second: "Second payment", final: "Final payment" };

export function scheduleKindLabel(kind: string): string {
  return KIND[kind] ?? "Payment";
}

const METHOD: Record<string, string> = {
  bank_transfer: "Bank transfer",
  card_offline: "Card",
  cash: "Cash",
};

export function paymentLabel(p: Payment): string {
  const method = METHOD[p.method] ?? "Other";
  return p.kind === "refund" ? `Refund · ${method}` : method;
}

// "US$2,400.00", "₫60,000,000" in English (UI opens in English); the currency's own decimals. The amount is a
// decimal string from the API: shown, never computed with.
export function money(amount: string | null, currency: string): string {
  if (amount === null || amount === "") return "—";
  const n = Number(amount);
  if (!Number.isFinite(n)) return `${amount} ${currency}`;
  try {
    return new Intl.NumberFormat("en-GB", { style: "currency", currency }).format(n);
  } catch {
    return `${amount} ${currency}`;
  }
}

// "10 Jan 2027", UTC so the server and every browser agree (a due date has no time of day).
export function dateLabel(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso.length === 10 ? `${iso}T00:00:00Z` : iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
}

export function tripDates(trip: TripRef): string {
  const start = dateLabel(trip.start_date);
  const end = dateLabel(trip.end_date);
  if (start && end) return `${start} – ${end}`;
  return start || end;
}

// The balance line: "Balance", or "Overpaid" when the verified payments exceed the total (AA refunds or adjusts).
export function balanceLabel(balance: string | null): string {
  return balance !== null && balance.startsWith("-") ? "Overpaid" : "Balance";
}

export function balanceAmount(balance: string | null): string | null {
  return balance !== null && balance.startsWith("-") ? balance.slice(1) : balance;
}

// What the Payments section shows for a GET /api/me/payments answer (same rule as My Trips: 401 = sign in).
export type PaymentsOutcome = "ok" | "signed_out" | "error";

export function paymentsOutcome(status: number): PaymentsOutcome {
  if (status === 401) return "signed_out";
  if (status >= 200 && status < 300) return "ok";
  return "error";
}

// The BFF's answer when Booking did not answer 2xx. Only the BFF's own "no session" branch is a 401: the section
// renders only after /api/me succeeded with the same session, so a Booking 401/403 is a Booking-side refusal (client
// id or issuer mismatch, clock skew), not a signed-out traveller. Mapping it to 401 would show "Your session has
// ended" and send the traveller round a sign-in loop that gets the same refusal again; 502 shows Try again instead.
export function upstreamFailure(upstreamStatus: number): { status: 502; error: "booking_refused" | "upstream_error" } {
  if (upstreamStatus === 401 || upstreamStatus === 403) return { status: 502, error: "booking_refused" };
  return { status: 502, error: "upstream_error" };
}

// ── The upstream call (app/api/me/payments) ──────────────────────────────────────────────────────────────────────

// AA-Booking's public API (Dev). Env BOOKING_API_URL overrides it (another environment, a local API); https only,
// no trailing slash. The traveller's access token is the only credential: no TripPlanner secret goes to Booking.
export const DEFAULT_BOOKING_API_URL = "https://api-booking.lumiguides.it.com";
export const PAYMENTS_PATH = "/booking/customer/v1/payments";

export function bookingPaymentsUrl(env: Record<string, string | undefined>): string | null {
  const base = (env.BOOKING_API_URL ?? "").trim().replace(/\/+$/, "") || DEFAULT_BOOKING_API_URL;
  if (!/^https:\/\/[a-z0-9.-]+(:\d+)?$/i.test(base)) return null;
  return `${base}${PAYMENTS_PATH}`;
}

export function bookingHeaders(accessToken: string, requestId: string): Record<string, string> {
  return { accept: "application/json", authorization: `Bearer ${accessToken}`, "x-request-id": requestId };
}
