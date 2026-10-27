"use client";
// AA-682 S5 — My Payments on /trips: the signed-in traveller's payment requests from AA-Booking
// (GET /api/me/payments → booking.customer.v1). Read only: totals, the payment schedule with its chips (Scheduled,
// Due soon, Paid, Overdue), the payments AA has verified and how to pay. No pay button, no upload: money is sent by
// bank transfer and checked by the trip consultant. Each request is shown under the planned trip it came from when
// that trip is in My Trips (planner_trip_id), else under its booking reference.
import { useCallback, useEffect, useState } from "react";
import { Badge, Button, ButtonLink, EmptyState } from "@/ui";
import { tripTitle, type MyTrip } from "@/lib/myTrips";
import {
  balanceAmount,
  balanceLabel,
  dateLabel,
  invoiceStatus,
  money,
  paymentLabel,
  paymentsOutcome,
  scheduleChip,
  scheduleKindLabel,
  tripDates,
  type Invoice,
} from "@/lib/myPayments";

type State = { kind: "loading" } | { kind: "signed_out" } | { kind: "error" } | { kind: "ok"; invoices: Invoice[] };

function Figure({ label, value, strong = false }: { label: string; value: string; strong?: boolean }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] font-semibold uppercase tracking-[0.04em] text-k-muted">{label}</dt>
      <dd className={`mt-0.5 truncate text-sm ${strong ? "font-semibold text-k-ink" : "text-k-body"}`}>{value}</dd>
    </div>
  );
}

function InvoiceCard({ invoice, trips }: { invoice: Invoice; trips: MyTrip[] }) {
  const planned = invoice.trip.planner_trip_id
    ? trips.find((t) => t.trip_id === invoice.trip.planner_trip_id)
    : undefined;
  const title = planned ? tripTitle(planned) : "Your trip";
  const dates = tripDates(invoice.trip);
  const status = invoiceStatus(invoice.status);
  const cancelled = invoice.status === "cancelled";
  return (
    <li className="rounded-2xl border border-k-line bg-k-card p-4 shadow-aa-sm" data-testid="payment-request">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-k-ink">{title}</p>
          <p className="text-xs text-k-muted">
            {[dates, `Booking ${invoice.trip.code}`].filter(Boolean).join(" · ")}
          </p>
        </div>
        <Badge tone={status.tone}>{status.label}</Badge>
      </div>

      <p className="mt-3 text-xs text-k-muted">
        Payment request {invoice.invoice_number}
        {invoice.issued_at ? ` · sent ${dateLabel(invoice.issued_at)}` : ""}
      </p>
      <dl className="mt-2 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Figure label="Total" value={money(invoice.total_amount, invoice.currency)} />
        <Figure label="Paid" value={money(invoice.paid_amount, invoice.currency)} />
        {!cancelled && (
          <Figure label={balanceLabel(invoice.balance)} value={money(balanceAmount(invoice.balance), invoice.currency)}
            strong />
        )}
        {!cancelled && (
          <Figure label="Next due" value={invoice.next_due_date ? dateLabel(invoice.next_due_date) : "Nothing due"} />
        )}
      </dl>

      {invoice.schedule.length > 0 && !cancelled && (
        <section className="mt-4" aria-label={`Payment schedule of ${invoice.invoice_number}`}>
          <h3 className="text-xs font-semibold text-k-ink">Payment schedule</h3>
          <ul className="mt-2 divide-y divide-k-line rounded-xl border border-k-line">
            {invoice.schedule.map((row) => {
              const chip = scheduleChip(row.state);
              return (
                <li key={row.seq} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 px-3 py-2">
                  <span className="truncate text-sm text-k-ink">{scheduleKindLabel(row.kind)}</span>
                  <span className="text-right text-sm font-medium text-k-ink">{money(row.amount, invoice.currency)}</span>
                  <span className="text-xs text-k-muted">Due {dateLabel(row.due_date)}</span>
                  <span className="text-right"><Badge tone={chip.tone}>{chip.label}</Badge></span>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      {invoice.payments.length > 0 && (
        <section className="mt-4" aria-label={`Payments received for ${invoice.invoice_number}`}>
          <h3 className="text-xs font-semibold text-k-ink">Payments received</h3>
          <ul className="mt-2 space-y-1 text-sm">
            {invoice.payments.map((p) => (
              <li key={p.ref} className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-3 text-k-body">
                <span className="text-xs text-k-muted">{dateLabel(p.received_on)}</span>
                <span className="truncate">{paymentLabel(p)}</span>
                <span className="font-medium text-k-ink">
                  {p.kind === "refund" ? "−" : ""}
                  {money(p.amount, p.currency)}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {invoice.payment_instructions && (
        <section className="mt-4 rounded-xl bg-k-neutral-bg p-3" aria-label="How to pay">
          <h3 className="text-xs font-semibold text-k-ink">How to pay</h3>
          <p className="mt-1 whitespace-pre-line text-[13px] leading-relaxed text-k-body">
            {invoice.payment_instructions}
          </p>
          <p className="mt-2 text-[13px] text-k-muted">
            Please quote {invoice.invoice_number} with your payment. It shows here once our team has checked it.
          </p>
        </section>
      )}
    </li>
  );
}

export default function MyPayments({ trips }: { trips: MyTrip[] }) {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);
  const retry = useCallback(() => {
    setState({ kind: "loading" });
    setAttempt((n) => n + 1);
  }, []);

  useEffect(() => {
    let live = true;
    fetch("/api/me/payments", { cache: "no-store" })
      .then(async (r) => {
        if (!live) return;
        const outcome = paymentsOutcome(r.status);
        if (outcome !== "ok") return setState({ kind: outcome });
        const b = await r.json();
        if (live) setState({ kind: "ok", invoices: Array.isArray(b?.invoices) ? b.invoices : [] });
      })
      .catch(() => live && setState({ kind: "error" }));
    return () => {
      live = false;
    };
  }, [attempt]);

  return (
    <section aria-labelledby="my-payments-title" className="mt-10" data-testid="my-payments">
      <h2 id="my-payments-title" className="text-xl font-semibold text-k-ink">Payments</h2>
      <p className="mt-1 text-[13px] text-k-muted">
        Payment requests from your trip consultant, what you have paid and what is still due.
      </p>

      <div className="mt-4">
        {state.kind === "loading" && <p className="text-sm text-k-muted" role="status">Loading your payments…</p>}
        {state.kind === "signed_out" && (
          <EmptyState title="Sign in again to see your payments" icon="shield">
            Your session has ended.
            <div className="mt-4 flex justify-center">
              <ButtonLink href="/sign-in?next=/trips" variant="primary" size="sm">Sign in</ButtonLink>
            </div>
          </EmptyState>
        )}
        {state.kind === "error" && (
          <div role="alert" className="rounded-2xl border border-k-line bg-k-card px-5 py-6 text-center">
            <p className="text-sm font-semibold text-k-ink">We could not load your payments</p>
            <p className="mt-1 text-[13px] text-k-muted">Please try again in a moment.</p>
            <div className="mt-3 flex justify-center">
              <Button variant="secondary" size="sm" onClick={retry}>Try again</Button>
            </div>
          </div>
        )}
        {state.kind === "ok" && state.invoices.length === 0 && (
          <EmptyState title="No payment requests yet" icon="calendar">
            When your trip consultant confirms a trip you sent while signed in and sends a payment request, it
            appears here with its due dates. For a trip sent without signing in, your trip consultant sends the
            payment request by email.
          </EmptyState>
        )}
        {state.kind === "ok" && state.invoices.length > 0 && (
          <ul className="space-y-4" data-testid="payment-requests">
            {state.invoices.map((inv) => (
              <InvoiceCard key={inv.invoice_number} invoice={inv} trips={trips} />
            ))}
          </ul>
        )}
      </div>

      <p className="mt-4 text-[13px] text-k-muted">Questions? Contact your trip consultant.</p>
    </section>
  );
}
