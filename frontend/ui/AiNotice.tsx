// AA-1033 — the traveller notice under AI-written text: "Some descriptions are written with the help of AI and may
// contain mistakes. Prices, itineraries and visa conditions are confirmed in your official quote."
//
// The sentence is the ecosystem's one AI notice: it comes from the kit core copy (ui/core/aiNotice.messages.ts, the
// `tokens` profile of the CIS kit, ADR 0016), the same text the CIS kit AiNotice renders, so it cannot drift from
// the other apps. The look is TripPlanner's own (ADR 0016 decision 2: B2C components stay here): a quiet
// `<p role="note">` with an info icon, muted on the page, light on the navy footer band. No state, no client code: a
// server component renders it, and it takes its own line (no layout shift).
import { aiNoticeText } from "./core/aiNotice.messages";
import { Icon } from "./Icon";

export type AiNoticeTone = "page" | "band";

const TONE: Record<AiNoticeTone, { text: string; icon: string }> = {
  page: { text: "text-k-muted", icon: "text-k-muted" },
  band: { text: "text-k-on-band-muted", icon: "text-k-band-accent" },
};

export function AiNotice({
  tone = "page",
  size = "md",
  className = "",
  testId = "ai-notice",
}: {
  tone?: AiNoticeTone;
  /** "sm": the planner panel's 11px text; "md": the caption size of the site pages. */
  size?: "sm" | "md";
  className?: string;
  testId?: string;
}) {
  const text = size === "sm" ? "text-[11px] leading-snug" : "text-caption font-normal";
  return (
    <p role="note" data-testid={testId} data-ai-notice="traveller" className={`flex items-start gap-1.5 ${text} ${TONE[tone].text} ${className}`}>
      <Icon name="info" className={`mt-px h-3.5 w-3.5 shrink-0 ${TONE[tone].icon}`} />
      <span>{aiNoticeText("traveller", "en")}</span>
    </p>
  );
}
