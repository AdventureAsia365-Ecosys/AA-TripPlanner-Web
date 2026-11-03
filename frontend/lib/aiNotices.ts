// AA-642 S0 (root ADR 0009 decisions 5 and 6): the plain notices a traveller sees when the AI part of
// the planner is not used for a request — AI paused (the spend counter or the route config cannot be
// read, or the model provider is down) or the daily AI budget is spent, and (AA-1015) when this visitor reached the
// per-session narration limit or the per-IP search limit (429), or the bot check refused the request (403
// bot_check_failed, lib/botCheck.ts). The answer itself is still
// useful (basic day lines, keyword search), so a notice is a calm sentence, never a spinner or an error.
//
// English is the default (pages open in English); Vietnamese is used only when the page language is
// Vietnamese (`<html lang="vi">`). Both tables must have the same keys (lib/aiNotices.test.ts).

export type AiNoticeKey = "narrationPaused" | "narrationBudget" | "narrationLimit" | "searchLimit" | "botCheck";

export type AiNoticeLang = "en" | "vi";

export const AI_NOTICES: Record<AiNoticeLang, Record<AiNoticeKey, string>> = {
  en: {
    narrationPaused:
      "AI writing is paused right now, so days without Adventure Asia's own itinerary text show only " +
      "their places. Try again later; your advisor confirms every detail.",
    narrationBudget:
      "Today's AI writing limit is reached, so days without Adventure Asia's own itinerary text show only " +
      "their places. Your advisor confirms every detail.",
    narrationLimit:
      "You have asked for many narrations in a short time. Your trip is saved; please try again in a little while.",
    searchLimit:
      "Lots of searches in a short time. Please wait a minute before searching again; the map still works.",
    botCheck:
      "We could not confirm this request came from the planner page. Please reload the page and try again; " +
      "your trip is saved.",
  },
  vi: {
    narrationPaused:
      "Tính năng viết bằng AI đang tạm dừng, nên những ngày chưa có lịch trình riêng của Adventure Asia " +
      "chỉ hiện tên địa điểm. Bạn hãy thử lại sau; chuyên viên tư vấn sẽ xác nhận mọi chi tiết.",
    narrationBudget:
      "Hôm nay đã đạt giới hạn viết bằng AI, nên những ngày chưa có lịch trình riêng của Adventure Asia " +
      "chỉ hiện tên địa điểm. Chuyên viên tư vấn sẽ xác nhận mọi chi tiết.",
    narrationLimit:
      "Bạn đã yêu cầu viết lịch trình nhiều lần trong thời gian ngắn. Chuyến đi của bạn vẫn được lưu; " +
      "vui lòng thử lại sau ít phút.",
    searchLimit:
      "Bạn đã tìm kiếm nhiều lần trong thời gian ngắn. Vui lòng đợi một phút rồi tìm lại; bản đồ vẫn dùng bình thường.",
    botCheck:
      "Chúng tôi chưa xác nhận được yêu cầu này đến từ trang lập kế hoạch. Vui lòng tải lại trang rồi thử lại; " +
      "chuyến đi của bạn vẫn được lưu.",
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

// A 429 from the BFF (AA-1015): the limit notice of that route.
export function limitNotice(scope: "narrate" | "search"): AiNoticeKey {
  return scope === "narrate" ? "narrationLimit" : "searchLimit";
}

// AA-1040 (EU AI Act Art. 50(1)): which kit notice sits under a narration. `llm` and `mixed` answers have days the AI
// wrote on this request ("written by AI when you asked for it"); `master` / `master_basic` are Adventure Asia's own
// itinerary text, written earlier by AI from the tour notes (the traveller sentence). Unknown or missing: traveller.
export function narrationNoticeVariant(source: string | null | undefined): "narration" | "traveller" {
  return source === "llm" || source === "mixed" ? "narration" : "traveller";
}

// AA-1040 (EU AI Act Art. 50(2)): the machine-readable mark of a block of catalog tour text (summary, highlights,
// itinerary days), written by the CIS catalog writer model. Put on the text blocks only, never page-wide: a tour page
// also carries prices, photos and facts that are not AI output. `generatedAt` is the tour's last Master publish
// (`updated_at`), else its first (`published_at`); omitted when neither is known.
export type AiMarkAttrs = { "data-ai-generated": "true"; "data-ai-source": "catalog"; "data-ai-generated-at"?: string };

export function catalogTextMark(updatedAt?: string | null, publishedAt?: string | null): AiMarkAttrs {
  const at = updatedAt || publishedAt || null;
  return at
    ? { "data-ai-generated": "true", "data-ai-source": "catalog", "data-ai-generated-at": at }
    : { "data-ai-generated": "true", "data-ai-source": "catalog" };
}
