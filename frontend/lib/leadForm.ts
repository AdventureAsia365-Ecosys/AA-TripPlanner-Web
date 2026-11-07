// AA-1038 R5 slice 3 (root ADR 0019 d11) — "Send to advisor" on an agency tenant's planner (/{slug}/plan): the
// traveller's answers become the `lead` of POST /trip/{id}/send-to-advisor, which the assembly Lambda turns into one
// lead.submitted.v1 for that agency (backend/assembly/leads.py; contract docs/contracts/lead.submitted.v1.openapi.yaml
// of the root repo). Pure, so `npm test` runs it.
//
// The traveller is a guest: only a name, an e-mail, an optional phone, the party, an optional start date, budget band
// and message, and the contact consent (required). The bare /plan and the AA demo tenant keep the demo notice
// (lib/advisorDemo.ts): Adventure Asia keeps no traveller record of its own.

export const DEMO_TENANT_SLUG = "aa-tripplanner";

export const BUDGET_BANDS = [
  { value: "under_1000", label: "Under 1,000 USD" },
  { value: "1000_2000", label: "1,000 to 2,000 USD" },
  { value: "2000_3500", label: "2,000 to 3,500 USD" },
  { value: "3500_5000", label: "3,500 to 5,000 USD" },
  { value: "over_5000", label: "Over 5,000 USD" },
  { value: "not_sure", label: "Not sure yet" },
] as const;

export interface LeadForm {
  name: string;
  email: string;
  phone: string;
  adults: number;
  children: string; // ages, comma separated ("4, 9")
  startDate: string; // yyyy-mm-dd or ""
  budget: string; // one of BUDGET_BANDS or ""
  message: string;
  consent: boolean;
  marketing: boolean;
}

export interface LeadBody {
  traveller: { name: string; email: string; phone: string | null; locale: "en" | "vi" };
  party: { adults: number; child_ages: number[] };
  trip: { start_date: string | null; budget_band: string | null; message: string | null };
  consent: { contact: boolean; marketing: boolean };
}

export const EMPTY_LEAD: LeadForm = {
  name: "", email: "", phone: "", adults: 2, children: "", startDate: "", budget: "", message: "", consent: false,
  marketing: false,
};

const EMAIL_RE = /^[^@\s]{1,64}@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,63}$/;
// The contract's Traveller.phone: E.164 with its country code ("+" first), as backend/assembly/leads.py checks it. The
// agency's CRM cannot place a local number ("090 ...") without knowing the country.
export const PHONE_RE = /^\+[1-9][0-9 ()-]{5,39}$/;
export const PHONE_HINT = "Phone with country code, optional (e.g. +44 20 7946 0000)";

/** True on an agency tenant's planner: a lead goes to that agency. False on the bare path and the AA demo tenant. */
export function isAgencyPlanner(slug: string | null | undefined): boolean {
  return typeof slug === "string" && slug !== "" && slug !== DEMO_TENANT_SLUG;
}

export function childAges(raw: string): number[] | null {
  const parts = raw.split(",").map((p) => p.trim()).filter(Boolean);
  if (parts.length > 20) return null;
  const ages = parts.map((p) => (/^\d{1,2}$/.test(p) ? Number(p) : NaN));
  return ages.every((a) => Number.isInteger(a) && a >= 0 && a <= 17) ? ages : null;
}

/** Field names the form must fix before it can be sent (empty = ready). Consent is checked apart (the button). */
export function leadFormErrors(f: LeadForm): string[] {
  const bad: string[] = [];
  if (!f.name.trim() || f.name.trim().length > 120) bad.push("name");
  if (!EMAIL_RE.test(f.email.trim())) bad.push("email");
  if (f.phone.trim() && !PHONE_RE.test(f.phone.trim())) bad.push("phone");
  if (!Number.isInteger(f.adults) || f.adults < 1 || f.adults > 50) bad.push("adults");
  if (childAges(f.children) === null) bad.push("child_ages");
  if (f.message.length > 1000) bad.push("message");
  return bad;
}

export function canSendLead(f: LeadForm): boolean {
  return f.consent && leadFormErrors(f).length === 0;
}

export function leadBody(f: LeadForm, locale: "en" | "vi" = "en"): LeadBody {
  return {
    traveller: { name: f.name.trim(), email: f.email.trim(), phone: f.phone.trim() || null, locale },
    party: { adults: f.adults, child_ages: childAges(f.children) ?? [] },
    trip: {
      start_date: f.startDate || null,
      budget_band: f.budget || null,
      message: f.message.trim() || null,
    },
    consent: { contact: f.consent, marketing: f.marketing },
  };
}

/** The calm line the planner shows for the Lambda's answer to a lead. */
export function leadResultMessage(status: number, body: { error?: string } | null | undefined, agency: string): string {
  if (status === 200) return `Sent to ${agency}. They will contact you about this trip.`;
  switch (body?.error) {
    case "consent_required":
      return "Please tick the box so the agency may contact you.";
    case "invalid_lead":
      return "Some answers need a look: please check the highlighted fields. A phone starts with + and the country code.";
    case "lead_route_missing":
    case "lead_channel_unavailable":
    case "lead_route_unavailable":
      return `${agency} cannot take requests from the planner right now. Please contact them directly.`;
    case "empty_trip":
      return "Add a stop or a day to your trip first.";
    default:
      return "Something went wrong. Please try again.";
  }
}
