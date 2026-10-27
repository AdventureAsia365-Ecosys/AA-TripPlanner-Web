// AA-682 S5 — My Payments helpers: the BFF allow-list, chips and labels. Run: npm test.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  balanceAmount,
  bookingHeaders,
  bookingPaymentsUrl,
  balanceLabel,
  dateLabel,
  invoiceStatus,
  money,
  paymentLabel,
  paymentsOutcome,
  pickPayments,
  scheduleChip,
  scheduleKindLabel,
  tripDates,
} from "./myPayments.ts";

const INVOICE = {
  invoice_number: "INV-000003",
  status: "partially_paid",
  document_type: "proforma",
  currency: "USD",
  total_amount: "2400.00",
  paid_amount: "720.00",
  balance: "1680.00",
  next_due_date: "2026-12-11",
  overdue_amount: "0.00",
  issued_at: "2026-10-27T09:30:00Z",
  trip: { code: "TC-000022", planner_trip_id: "trip-1", start_date: "2027-01-10", end_date: "2027-01-14" },
  schedule: [
    { seq: 1, kind: "deposit", amount: "720.00", due_date: "2026-10-30", remaining_amount: "0.00", state: "paid" },
    { seq: 2, kind: "final", amount: "1680.00", due_date: "2026-12-11", remaining_amount: "1680.00", state: "due_soon" },
  ],
  payments: [{ ref: "PMT-000007", kind: "receipt", amount: "720.00", currency: "USD", method: "bank_transfer",
    received_on: "2026-10-28" }],
  payment_instructions: "Bank transfer to the account on your payment request.",
};

function keys(node: unknown, out = new Set<string>()): Set<string> {
  if (Array.isArray(node)) node.forEach((n) => keys(n, out));
  else if (node && typeof node === "object") {
    for (const [k, v] of Object.entries(node)) {
      out.add(k);
      keys(v, out);
    }
  }
  return out;
}

test("the BFF forwards the contract fields only", () => {
  const upstream = {
    invoices: [{
      ...INVOICE,
      recorded_by: "staff-1",
      notes: "internal",
      trip: { ...INVOICE.trip, supplier_id: "s-1" },
      payments: [{ ...INVOICE.payments[0], evidence_url: "https://x.invalid/slip.pdf", reference: "VCB 1" }],
    }],
    debug: true,
  };
  const [inv] = pickPayments(upstream);
  assert.deepEqual(inv, INVOICE);
  for (const bad of ["recorded_by", "notes", "supplier_id", "evidence_url", "reference", "debug"]) {
    assert.ok(!keys(inv).has(bad), bad);
  }
});

test("pickPayments tolerates a malformed answer", () => {
  assert.deepEqual(pickPayments(null), []);
  assert.deepEqual(pickPayments({ invoices: "x" }), []);
  assert.deepEqual(pickPayments({ invoices: [null, 3, { status: "sent" }] }), []); // no number: dropped
  const [inv] = pickPayments({ invoices: [{ invoice_number: "INV-000009" }] });
  assert.equal(inv.balance, null);
  assert.deepEqual(inv.schedule, []);
  assert.equal(inv.trip.planner_trip_id, null);
});

test("schedule chips: Scheduled, Due soon, Paid, Overdue", () => {
  assert.deepEqual(scheduleChip("scheduled"), { label: "Scheduled", tone: "neutral" });
  assert.deepEqual(scheduleChip("due_soon"), { label: "Due soon", tone: "info" });
  assert.deepEqual(scheduleChip("paid"), { label: "Paid", tone: "success" });
  assert.deepEqual(scheduleChip("overdue"), { label: "Overdue", tone: "danger" });
  assert.equal(scheduleChip("cancelled").label, "Cancelled");
  assert.equal(scheduleChip("something-new").label, "Scheduled");
});

test("invoice status and kind labels", () => {
  assert.equal(invoiceStatus("sent").label, "To pay");
  assert.equal(invoiceStatus("partially_paid").label, "Partly paid");
  assert.equal(invoiceStatus("paid").label, "Paid in full");
  assert.equal(invoiceStatus("cancelled").label, "Cancelled");
  assert.equal(scheduleKindLabel("deposit"), "Deposit");
  assert.equal(scheduleKindLabel("final"), "Final payment");
  assert.equal(scheduleKindLabel("other"), "Payment");
  assert.equal(paymentLabel(INVOICE.payments[0]), "Bank transfer");
  assert.equal(paymentLabel({ ...INVOICE.payments[0], kind: "refund", method: "cash" }), "Refund · Cash");
});

test("money uses the currency's own decimals and never fails", () => {
  assert.equal(money("2400.00", "USD"), "US$2,400.00");
  assert.equal(money("60000000.00", "VND"), "₫60,000,000");
  assert.equal(money(null, "USD"), "—");
  assert.equal(money("12.00", "??"), "12.00 ??");
  assert.equal(money("abc", "USD"), "abc USD");
});

test("dates in English, UTC", () => {
  assert.equal(dateLabel("2027-01-10"), "10 Jan 2027");
  assert.equal(dateLabel("2026-10-27T23:30:00Z"), "27 Oct 2026");
  assert.equal(dateLabel(null), "");
  assert.equal(dateLabel("nope"), "");
  assert.equal(tripDates(INVOICE.trip), "10 Jan 2027 – 14 Jan 2027");
  assert.equal(tripDates({ ...INVOICE.trip, end_date: null }), "10 Jan 2027");
});

test("overpaid balance reads as Overpaid with a positive amount", () => {
  assert.equal(balanceLabel("1680.00"), "Balance");
  assert.equal(balanceLabel("-50.00"), "Overpaid");
  assert.equal(balanceAmount("-50.00"), "50.00");
  assert.equal(balanceAmount(null), null);
});

test("outcome: 401 is the sign-in prompt, other failures an error", () => {
  assert.equal(paymentsOutcome(200), "ok");
  assert.equal(paymentsOutcome(401), "signed_out");
  assert.equal(paymentsOutcome(502), "error");
  assert.equal(paymentsOutcome(0), "error");
});

test("the Booking URL: Dev by default, https only, no path from the env", () => {
  assert.equal(bookingPaymentsUrl({}), "https://api-booking.lumiguides.it.com/booking/customer/v1/payments");
  assert.equal(bookingPaymentsUrl({ BOOKING_API_URL: " https://api.example.test/ " }),
    "https://api.example.test/booking/customer/v1/payments");
  assert.equal(bookingPaymentsUrl({ BOOKING_API_URL: "http://api.example.test" }), null);
  assert.equal(bookingPaymentsUrl({ BOOKING_API_URL: "https://api.example.test/x?y" }), null);
  assert.deepEqual(bookingHeaders("tok", "rid"),
    { accept: "application/json", authorization: "Bearer tok", "x-request-id": "rid" });
});
