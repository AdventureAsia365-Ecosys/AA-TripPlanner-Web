// AA-642 S0 (root ADR 0009 decisions 5 and 6): the plain notices a traveller sees when the AI part of
// the planner is not used for a request — AI paused (the spend counter or the route config cannot be
// read, or the model provider is down) or the daily AI budget is spent. The answer itself is still
// useful (basic day lines, keyword search), so a notice is a calm sentence, never a spinner or an error.
//
// English is the default (pages open in English); Vietnamese is used only when the page language is
// Vietnamese (`<html lang="vi">`). Both tables must have the same keys (lib/aiNotices.test.ts).

export type AiNoticeKey = "narrationPaused" | "narrationBudget";

export type AiNoticeLang = "en" | "vi";

export const AI_NOTICES: Record<AiNoticeLang, Record<AiNoticeKey, string>> = {
  en: {
    narrationPaused:
      "AI writing is paused right now, so days without Adventure Asia's own itinerary text show only " +
      "their places. Try again later; your advisor confirms every detail.",
    narrationBudget:
      "Today's AI writing limit is reached, so days without Adventure Asia's own itinerary text show only " +
      "their places. Your advisor confirms every detail.",
  },
  vi: {
    narrationPaused:
      "Tính năng viết bằng AI đang tạm dừng, nên những ngày chưa có lịch trình riêng của Adventure Asia " +
      "chỉ hiện tên địa điểm. Bạn hãy thử lại sau; chuyên viên tư vấn sẽ xác nhận mọi chi tiết.",
    narrationBudget:
      "Hôm nay đã đạt giới hạn viết bằng AI, nên những ngày chưa có lịch trình riêng của Adventure Asia " +
      "chỉ hiện tên địa điểm. Chuyên viên tư vấn sẽ xác nhận mọi chi tiết.",
  },
};

// The notice language from a BCP 47 tag ("vi", "vi-VN" -> vi); anything else is English.
export function noticeLang(tag: string | null | undefined): AiNoticeLang {
  return (tag ?? "").toLowerCase().split("-")[0] === "vi" ? "vi" : "en";
}

// The notice text in the page language (the `<html lang>` attribute in the browser, English elsewhere).
export function aiNotice(key: AiNoticeKey, lang?: string | null): string {
  const tag = lang ?? (typeof document !== "undefined" ? document.documentElement.lang : "en");
  return AI_NOTICES[noticeLang(tag)][key];
}

// Which notice a narration answer needs (POST /trip/{id}/narrate): `ai_paused` wins over the budget.
export function narrationNotice(body: { ai_paused?: boolean; ai_budget_exhausted?: boolean }): AiNoticeKey | null {
  if (body.ai_paused) return "narrationPaused";
  if (body.ai_budget_exhausted) return "narrationBudget";
  return null;
}
