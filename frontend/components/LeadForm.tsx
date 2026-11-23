"use client";

// AA-1038 R5 slice 3 (root ADR 0019 d11) — the trip request form of an agency tenant's planner (/{slug}/plan). The
// answers become one lead.submitted.v1 for that agency (lib/leadForm.ts, backend/assembly/leads.py); the traveller
// stays a guest and Adventure Asia keeps no copy once the request is delivered. The bare /plan and the AA demo tenant
// never show this form (TripPanel keeps the demo notice there).

import { useEffect, useState } from "react";
import { Button } from "@/ui/Button";
import {
  BUDGET_BANDS,
  EMPTY_LEAD,
  budgetLabel,
  canSendLead,
  leadBody,
  leadFormDirty,
  leadFormErrors,
  leadResultMessage,
  type LeadBody,
  type LeadForm as LeadFormState,
} from "@/lib/leadForm";
import { holdWafReload } from "@/lib/wafChallenge";
import { useBrand } from "@/components/BrandContext";
import { usePlannerLang, usePlannerT } from "@/components/PlannerLang";
import { LEAD } from "@/lib/planner.messages";

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
  // AA-1115: the agency's own legal pages (/{slug}/privacy, /{slug}/terms), next to the form that collects the details.
  const { legal } = useBrand();
  // AA-1160: the form speaks the planner's language (the agency's locale); the lead carries it as traveller.locale.
  const lang = usePlannerLang();
  const t = usePlannerT(LEAD);
  const bad = touched ? [...leadFormErrors(form), ...serverBad] : serverBad;
  // Typed input lives only here: while it does, a challenged search or narration answers with the bot-check notice
  // instead of reloading the planner (lib/wafChallenge.ts, review S272). Released on unmount or when cleared.
  const dirty = leadFormDirty(form);
  useEffect(() => (dirty ? holdWafReload() : undefined), [dirty]);
  const set = (patch: Partial<LeadFormState>) => {
    setForm({ ...form, ...patch });
    setServerBad([]);
  };

  const submit = async () => {
    setTouched(true);
    if (!canSendLead(form)) return;
    setBusy(true);
    const { status, body } = await onSend(leadBody(form, lang));
    setBusy(false);
    const text = leadResultMessage(status, body, agency, lang);
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
        {t("intro", agency)}
      </p>
      <input
        aria-label={t("fullName")}
        placeholder={t("fullName")}
        autoComplete="name"
        value={form.name}
        onChange={(e) => set({ name: e.target.value })}
        className={field(bad.includes("name"))}
      />
      <input
        aria-label={t("email")}
        placeholder={t("email")}
        type="email"
        autoComplete="email"
        value={form.email}
        onChange={(e) => set({ email: e.target.value })}
        className={field(bad.includes("email"))}
      />
      <input
        aria-label={t("phoneHint")}
        placeholder={t("phoneHint")}
        title={t("phoneTitle")}
        type="tel"
        autoComplete="tel"
        value={form.phone}
        onChange={(e) => set({ phone: e.target.value })}
        className={field(bad.includes("phone"))}
      />
      <div className="grid grid-cols-2 gap-2">
        <label className="text-[11px] text-k-muted">
          {t("adults")}
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
          {t("childAges")}
          <input
            placeholder={t("childAgesExample")}
            value={form.children}
            onChange={(e) => set({ children: e.target.value })}
            className={`${field(bad.includes("child_ages"))} mt-0.5`}
          />
        </label>
        <label className="text-[11px] text-k-muted">
          {t("startDate")}
          <input
            type="date"
            value={form.startDate}
            onChange={(e) => set({ startDate: e.target.value })}
            className={`${field(bad.includes("start_date"))} mt-0.5`}
          />
        </label>
        <label className="text-[11px] text-k-muted">
          {t("budget")}
          <select
            value={form.budget}
            onChange={(e) => set({ budget: e.target.value })}
            className={`${field(bad.includes("budget_band"))} mt-0.5`}
          >
            <option value="">{t("budgetChoose")}</option>
            {BUDGET_BANDS.map((b) => (
              <option key={b.value} value={b.value}>
                {budgetLabel(b.value, lang)}
              </option>
            ))}
          </select>
        </label>
      </div>
      <textarea
        aria-label={t("messageLabel")}
        placeholder={t("messagePlaceholder")}
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
        <span>{t("consent", agency)}</span>
      </label>
      <label className="flex items-start gap-2 text-xs text-k-muted">
        <input
          type="checkbox"
          checked={form.marketing}
          onChange={(e) => set({ marketing: e.target.checked })}
          className="aa-focus mt-0.5"
        />
        <span>{t("marketing", agency)}</span>
      </label>
      <p className="text-[11px] text-k-muted">
        {t("privacy", agency)}
      </p>
      <p className="text-[11px] text-k-muted" data-testid="lead-legal-links">
        {t("howUsed")}{" "}
        {legal.links.map((l, i) => (
          <span key={l.href}>
            {i > 0 && " · "}
            <a
              href={l.href}
              target="_blank"
              rel="noopener noreferrer"
              className="aa-focus font-semibold text-k-ink underline underline-offset-2 hover:text-k-accent-deep"
            >
              {l.label}
            </a>
          </span>
        ))}{" "}
        {t("draft")}
      </p>
      {message && (
        <p role="alert" className="rounded-lg bg-k-danger-soft px-2.5 py-1.5 text-xs font-medium text-k-danger">
          {message}
        </p>
      )}
      <Button type="submit" size="md" className="w-full" disabled={busy || !form.consent}>
        {busy ? t("sending") : t("send")}
      </Button>
    </form>
  );
}
