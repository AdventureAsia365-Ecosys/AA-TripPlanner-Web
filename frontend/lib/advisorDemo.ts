// AA-1034 R0 (root ADR 0019 d8/d11, B2B-only model) — "Send to an advisor" is a demo. Adventure Asia works with
// travel agencies only and keeps no traveller record of its own, so the planner sends nothing: the button shows
// DEMO_NOTICE instead of asking for contact details, and the assembly Lambda refuses the hand-off with 410
// `b2b_only` (backend config.ADVISOR_HANDOFF_DEMO) if an old bundle still calls it. Leads go to the agency's own CRM
// later (lead.submitted.v1). NEXT_PUBLIC_ADVISOR_HANDOFF=live (build time) turns the frozen hand-off back on.

/** Demo unless the value is exactly `live` (any case); unset or mistyped keeps the demo. */
export function isAdvisorDemo(raw: string | undefined): boolean {
  return (raw ?? "").trim().toLowerCase() !== "live";
}

// Referenced literally so Next.js inlines it at build time.
export const ADVISOR_HANDOFF_DEMO = isAdvisorDemo(process.env.NEXT_PUBLIC_ADVISOR_HANDOFF);

/** The Lambda's answer while the demo is on. */
export function isB2bOnly(status: number, body: { error?: string } | null | undefined): boolean {
  return status === 410 && body?.error === "b2b_only";
}

export const DEMO_NOTICE = {
  title: "This planner is a demo",
  body:
    "Adventure Asia plans and runs trips with travel agencies, so trips are not sent to an advisor from here and " +
    "no contact details are collected. Keep shaping the trip on the map; your travel agency can book it with us.",
};
