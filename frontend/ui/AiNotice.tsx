// AA-1033 — the traveller notice under AI-written text: "Some descriptions are written with the help of AI and may
// contain mistakes. Prices, itineraries and visa conditions are confirmed in your official quote."
//
// The sentence is the ecosystem's one AI notice: it comes from the kit core copy (ui/core/aiNotice.messages.ts, the
// `tokens` profile of the CIS kit, ADR 0016), the same text the CIS kit AiNotice renders, so it cannot drift from
// the other apps. The look is TripPlanner's own (ADR 0016 decision 2: B2C components stay here): a quiet
// `<p role="note">` with an info icon, muted on the page, light on the navy footer band. No state, no client code: a
// server component renders it, and it takes its own line (no layout shift).
// AA-1040: `variant="narration"` says the text was written by AI when the traveller asked for it (trip narration, EU AI
// Act Art. 50(1)); the default `traveller` covers catalog text written earlier from Adventure Asia's tour notes.
// `termsHref` appends a "Use of AI" link to the terms' section, opened in a new tab: the planner pages have no site
// footer (where the site pages carry that link), and a new tab keeps the trip being built.
import { aiNoticeText, type AiNoticeVariant } from "./core/aiNotice.messages";
import { Icon } from "./Icon";

export type AiNoticeTone = "page" | "band";

/** The traveller variants of the kit wording (the staff / publisher ones are for the agency apps). */
export type TravellerAiNoticeVariant = Extract<AiNoticeVariant, "traveller" | "narration">;

const TONE: Record<AiNoticeTone, { text: string; icon: string }> = {
  page: { text: "text-k-muted", icon: "text-k-muted" },
  band: { text: "text-k-on-band-muted", icon: "text-k-band-accent" },
};

export function AiNotice({
  variant = "traveller",
  tone = "page",
  size = "md",
  className = "",
  testId = "ai-notice",
  termsHref,
}: {
  variant?: TravellerAiNoticeVariant;
  tone?: AiNoticeTone;
  /** "sm": the planner panel's 11px text; "md": the caption size of the site pages. */
  size?: "sm" | "md";
  className?: string;
  testId?: string;
  /** The terms' "Use of AI" section (TERMS_AI_PATH): appended as a link, new tab. For the planner, which has no site footer. */
  termsHref?: string;
}) {
  const text = size === "sm" ? "text-[11px] leading-snug" : "text-caption font-normal";
  return (
    <p role="note" data-testid={testId} data-ai-notice={variant} className={`flex items-start gap-1.5 ${text} ${TONE[tone].text} ${className}`}>
      <Icon name="info" className={`mt-px h-3.5 w-3.5 shrink-0 ${TONE[tone].icon}`} />
      <span>
        {aiNoticeText(variant, "en")}
        {termsHref && (
          <>
            {" "}
            <a
              href={termsHref}
              target="_blank"
              rel="noopener noreferrer"
              className="aa-focus font-semibold text-k-ink underline underline-offset-2 hover:text-k-accent-deep"
            >
              Use of AI
            </a>
          </>
        )}
      </span>
    </p>
  );
}
