"use client";

// AA-1038 R5 slice 3 (root ADR 0019 d11) — the trip request form of an agency tenant's planner (/{slug}/plan). The
// answers become one lead.submitted.v1 for that agency (lib/leadForm.ts, backend/assembly/leads.py); the traveller
// stays a guest and Adventure Asia keeps no copy once the request is delivered. The bare /plan and the AA demo tenant
// never show this form (TripPanel keeps the demo notice there).

import { useState } from "react";
import { Button } from "@/ui/Button";
import {
  BUDGET_BANDS,
  EMPTY_LEAD,
  PHONE_HINT,
  canSendLead,
  leadBody,
  leadFormErrors,
  leadResultMessage,
  type LeadBody,
  type LeadForm as LeadFormState,
} from "@/lib/leadForm";

const INPUT =
  "aa-focus w-full rounded-lg border bg-k-card px-3 py-2 text-sm text-k-ink placeholder:text-k-muted";

function field(bad: boolean): string {
  return `${INPUT} ${bad ? "border-k-danger" : "border-k-line"}`;
}

export default function LeadForm({
  agency,
  onSend,
  onDone,
}: {
  agency: string;
  onSend: (lead: LeadBody) => Promise<{ status: number; body: { error?: string; fields?: string[] } }>;
  onDone: (message: string) => void;
}) {
  const [form, setForm] = useState<LeadFormState>(EMPTY_LEAD);
  const [touched, setTouched] = useState(false);
  const [serverBad, setServerBad] = useState<string[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const bad = touched ? [...leadFormErrors(form), ...serverBad] : serverBad;
  const set = (patch: Partial<LeadFormState>) => {
    setForm({ ...form, ...patch });
    setServerBad([]);
  };

  const submit = async () => {
    setTouched(true);
    if (!canSendLead(form)) return;
    setBusy(true);
    const lang = typeof document !== "undefined" && document.documentElement.lang === "vi" ? "vi" : "en";
    const { status, body } = await onSend(leadBody(form, lang));
    setBusy(false);
    const text = leadResultMessage(status, body, agency);
    if (status === 200) {
      onDone(text);
      return;
    }
    setServerBad(body?.fields ?? []);
    setMessage(text);
  };

  return (
    <form
      className="space-y-2"
      data-testid="lead-form"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <p className="text-xs text-k-muted">
        Send this trip to {agency}. They plan it with you and confirm every detail.
      </p>
      <input
        aria-label="Full name"
        placeholder="Full name"
        autoComplete="name"
        value={form.name}
        onChange={(e) => set({ name: e.target.value })}
        className={field(bad.includes("name"))}
      />
      <input
        aria-label="Email"
        placeholder="Email"
        type="email"
        autoComplete="email"
        value={form.email}
        onChange={(e) => set({ email: e.target.value })}
        className={field(bad.includes("email"))}
      />
      <input
        aria-label={PHONE_HINT}
        placeholder={PHONE_HINT}
        title="Start with + and your country code, for example +44 for the United Kingdom"
        type="tel"
        autoComplete="tel"
        value={form.phone}
        onChange={(e) => set({ phone: e.target.value })}
        className={field(bad.includes("phone"))}
      />
      <div className="grid grid-cols-2 gap-2">
        <label className="text-[11px] text-k-muted">
          Adults
          <input
            type="number"
            min={1}
            max={50}
            value={form.adults}
            onChange={(e) => set({ adults: Number(e.target.value) })}
            className={`${field(bad.includes("adults"))} mt-0.5`}
          />
        </label>
        <label className="text-[11px] text-k-muted">
          Children&apos;s ages
          <input
            placeholder="e.g. 4, 9"
            value={form.children}
            onChange={(e) => set({ children: e.target.value })}
            className={`${field(bad.includes("child_ages"))} mt-0.5`}
          />
        </label>
        <label className="text-[11px] text-k-muted">
          Start date (optional)
          <input
            type="date"
            value={form.startDate}
            onChange={(e) => set({ startDate: e.target.value })}
            className={`${field(bad.includes("start_date"))} mt-0.5`}
          />
        </label>
        <label className="text-[11px] text-k-muted">
          Budget per person
          <select
            value={form.budget}
            onChange={(e) => set({ budget: e.target.value })}
            className={`${field(bad.includes("budget_band"))} mt-0.5`}
          >
            <option value="">Choose (optional)</option>
            {BUDGET_BANDS.map((b) => (
              <option key={b.value} value={b.value}>
                {b.label}
              </option>
            ))}
          </select>
        </label>
      </div>
      <textarea
        aria-label="Message (optional)"
        placeholder="Anything the agency should know (optional)"
        rows={3}
        maxLength={1000}
        value={form.message}
        onChange={(e) => set({ message: e.target.value })}
        className={field(bad.includes("message"))}
      />
      <label className="flex items-start gap-2 text-xs text-k-muted">
        <input
          type="checkbox"
          checked={form.consent}
          onChange={(e) => set({ consent: e.target.checked })}
          className="aa-focus mt-0.5"
        />
        <span>{agency} may contact me about this trip.</span>
      </label>
      <label className="flex items-start gap-2 text-xs text-k-muted">
        <input
          type="checkbox"
          checked={form.marketing}
          onChange={(e) => set({ marketing: e.target.checked })}
          className="aa-focus mt-0.5"
        />
        <span>Send me travel ideas from {agency} (optional).</span>
      </label>
      <p className="text-[11px] text-k-muted">
        Your details go only to {agency}, who answers this request. The planner keeps no copy once it is delivered.
      </p>
      {message && (
        <p role="alert" className="rounded-lg bg-k-danger-soft px-2.5 py-1.5 text-xs font-medium text-k-danger">
          {message}
        </p>
      )}
      <Button type="submit" size="md" className="w-full" disabled={busy || !form.consent}>
        {busy ? "Sending…" : "Send my request"}
      </Button>
    </form>
  );
}
