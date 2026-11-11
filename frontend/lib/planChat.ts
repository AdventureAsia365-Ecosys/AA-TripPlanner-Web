// AA-1041 slice 2 — the planning chat on the planner: the answer shapes of POST /api/trip/chat, how the panel reads
// them, what it sends back as history, how a proposal is applied, and the panel's copy.
//
// The chat writes nothing: an answer may carry a proposal that the server built from Adventure Asia's catalog only
// (component ids, names, places). The traveller applies it with one click, which runs the planner's own mutations: a
// remove per stop that leaves, then apply-route with the new order (it pins the missing stops and saves the order).
//
// Pure (no React, no fetch, no next/*): `npm test` runs lib/planChat.test.ts. English is the default; Vietnamese only
// when the page language is Vietnamese (`<html lang="vi">`), as lib/aiNotices.ts.
import { noticeLang, type AiNoticeLang } from "./aiNotices.ts";

export const MAX_MESSAGE_CHARS = 500; // backend/assembly/plan_chat.py MAX_MESSAGE_CHARS
export const HISTORY_TURNS = 6; // plan_chat.MAX_HISTORY_TURNS
export const HISTORY_CHARS = 600; // plan_chat.MAX_HISTORY_CHARS

export interface ChatStop {
  component_id: string;
  name: string | null;
  place: string | null;
  new: boolean;
}

export interface ChatProposal {
  action: "replace" | "add" | "remove";
  final_component_ids: string[];
  add_component_ids: string[];
  remove_component_ids: string[];
  stops: ChatStop[];
  removed: ChatStop[];
}

export type ApplyState = "idle" | "applying" | "applied" | "failed";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  proposal?: ChatProposal | null;
  aiGenerated?: boolean;
  apply?: ApplyState;
}

export type PauseReason = "budget" | "unavailable";

export type ChatAnswer =
  | { kind: "reply"; reply: string; proposal: ChatProposal | null; aiGenerated: boolean; outcome: string }
  | { kind: "paused"; reason: PauseReason }
  | { kind: "limited"; retryAfterS: number }
  | { kind: "botCheck" }
  | { kind: "error" };

type ChatBody = {
  status?: string;
  reason?: string;
  reply?: string;
  outcome?: string;
  ai_generated?: boolean;
  proposal?: ChatProposal | null;
  error?: string;
  retry_after_s?: number;
};

function isProposal(p: unknown): p is ChatProposal {
  const q = p as ChatProposal | null;
  return !!q && ["replace", "add", "remove"].includes(q.action) && Array.isArray(q.final_component_ids)
    && Array.isArray(q.remove_component_ids) && Array.isArray(q.stops) && Array.isArray(q.removed);
}

/** What the panel shows for one BFF answer. `challenged`: the web ACL answered its Challenge (HTTP 202). */
export function readChatAnswer(status: number, challenged: boolean, body: ChatBody | null): ChatAnswer {
  if (challenged || (status === 403 && body?.error === "bot_check_failed")) return { kind: "botCheck" };
  if (status === 429) return { kind: "limited", retryAfterS: Number(body?.retry_after_s) || 0 };
  if (status !== 200 || !body) return { kind: "error" };
  if (body.status === "paused") return { kind: "paused", reason: body.reason === "budget" ? "budget" : "unavailable" };
  if (body.status !== "ok" || typeof body.reply !== "string" || !body.reply.trim()) return { kind: "error" };
  return {
    kind: "reply",
    reply: body.reply,
    proposal: isProposal(body.proposal) ? body.proposal : null,
    aiGenerated: body.ai_generated === true,
    outcome: String(body.outcome ?? ""),
  };
}

/** The last turns the server gets as context (the pending message is sent apart, notices are not turns). */
export function historyForRequest(messages: ChatMessage[]): { role: "user" | "assistant"; text: string }[] {
  return messages
    .filter((m) => m.text.trim())
    .slice(-HISTORY_TURNS)
    .map((m) => ({ role: m.role, text: m.text.slice(0, HISTORY_CHARS) }));
}

/** How a proposal is applied: the stops to remove first, then the order to pin (empty = nothing to pin). */
export function applySteps(p: ChatProposal): { remove: string[]; route: string[] } {
  return { remove: [...p.remove_component_ids], route: p.final_component_ids.length ? [...p.final_component_ids] : [] };
}

/** The one-line summary of a proposal ("Adds 2 stops, removes 1."). */
export function proposalSummary(p: ChatProposal, lang: AiNoticeLang = "en"): string {
  const added = p.stops.filter((s) => s.new).length;
  const removed = p.removed.length;
  const c = CHAT_COPY[lang];
  const parts: string[] = [];
  if (p.action === "replace") parts.push(c.replaces(p.stops.length));
  else if (added) parts.push(c.adds(added));
  if (removed) parts.push(c.removes(removed));
  return parts.join(" ") || c.reorders;
}

export interface ChatCopy {
  title: string;
  intro: string;
  examples: string[];
  placeholder: string;
  send: string;
  sending: string;
  you: string;
  assistant: string;
  apply: string;
  applying: string;
  applied: string;
  applyFailed: string;
  pausedBudget: string;
  pausedUnavailable: string;
  limited: string;
  botCheck: string;
  error: string;
  tooLong: string;
  serverText: string;
  replaces: (n: number) => string;
  adds: (n: number) => string;
  removes: (n: number) => string;
  reorders: string;
}

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

export const CHAT_COPY: Record<AiNoticeLang, ChatCopy> = {
  en: {
    title: "Plan with chat",
    intro:
      "Ask for a route and the planner suggests one from Adventure Asia's own tours and places. Nothing changes " +
      "in your trip until you apply a suggestion.",
    examples: ["A 5-day route at an easy pace", "Add a cooking class", "Make my trip one day shorter"],
    placeholder: "Ask about a country, a number of days or an activity",
    send: "Send",
    sending: "Thinking",
    you: "You",
    assistant: "Planner",
    apply: "Apply to my trip",
    applying: "Applying",
    applied: "Applied to your trip",
    applyFailed: "We could not apply this. Your trip is unchanged; please try again.",
    pausedBudget:
      "The planning chat is paused: this month's chat limit is reached. You can still build your trip on the map " +
      "and with the route finder.",
    pausedUnavailable:
      "The planning chat is not available right now. You can still build your trip on the map and with the route " +
      "finder; please try the chat again later.",
    limited: "You have sent many messages in a short time. Please wait a little before asking again.",
    botCheck: "We could not confirm this message came from the planner page. Please reload the page and try again.",
    error: "Something went wrong. Please try again.",
    tooLong: `Please keep a message under ${MAX_MESSAGE_CHARS} characters.`,
    serverText: "Standard answer",
    replaces: (n) => `Your trip becomes ${plural(n, "stop", "stops")}.`,
    adds: (n) => `Adds ${plural(n, "stop", "stops")}.`,
    removes: (n) => `Removes ${plural(n, "stop", "stops")}.`,
    reorders: "Changes the order of your stops.",
  },
  vi: {
    title: "Lên kế hoạch bằng trò chuyện",
    intro:
      "Hãy hỏi một lộ trình, trợ lý sẽ gợi ý từ chính các tour và địa điểm của Adventure Asia. Chuyến đi của bạn " +
      "chỉ thay đổi khi bạn áp dụng gợi ý.",
    examples: ["Lộ trình 5 ngày, nhịp nhẹ nhàng", "Thêm một lớp học nấu ăn", "Rút ngắn chuyến đi một ngày"],
    placeholder: "Hỏi về một quốc gia, số ngày hoặc một hoạt động",
    send: "Gửi",
    sending: "Đang trả lời",
    you: "Bạn",
    assistant: "Trợ lý",
    apply: "Áp dụng vào chuyến đi",
    applying: "Đang áp dụng",
    applied: "Đã áp dụng vào chuyến đi",
    applyFailed: "Chưa áp dụng được. Chuyến đi của bạn không đổi; vui lòng thử lại.",
    pausedBudget:
      "Trò chuyện lên kế hoạch đang tạm dừng vì đã đạt giới hạn của tháng này. Bạn vẫn có thể tạo chuyến đi trên " +
      "bản đồ và bằng công cụ tìm lộ trình.",
    pausedUnavailable:
      "Trò chuyện lên kế hoạch tạm thời chưa dùng được. Bạn vẫn có thể tạo chuyến đi trên bản đồ và bằng công cụ " +
      "tìm lộ trình; vui lòng thử lại sau.",
    limited: "Bạn đã gửi nhiều tin nhắn trong thời gian ngắn. Vui lòng đợi một chút rồi hỏi lại.",
    botCheck: "Chúng tôi chưa xác nhận được tin nhắn này đến từ trang lập kế hoạch. Vui lòng tải lại trang rồi thử lại.",
    error: "Đã có lỗi. Vui lòng thử lại.",
    tooLong: `Vui lòng viết tin nhắn dưới ${MAX_MESSAGE_CHARS} ký tự.`,
    serverText: "Câu trả lời chuẩn",
    replaces: (n) => `Chuyến đi gồm ${n} điểm dừng.`,
    adds: (n) => `Thêm ${n} điểm dừng.`,
    removes: (n) => `Bỏ ${n} điểm dừng.`,
    reorders: "Đổi thứ tự các điểm dừng.",
  },
};

/** The copy in the page language (the `<html lang>` attribute in the browser, English elsewhere). */
export function chatCopy(lang?: string | null): ChatCopy {
  const tag = lang ?? (typeof document !== "undefined" ? document.documentElement.lang : "en");
  return CHAT_COPY[noticeLang(tag)];
}

/** The line a non-reply answer leaves in the conversation. */
export function noticeFor(answer: Exclude<ChatAnswer, { kind: "reply" }>, copy: ChatCopy): string {
  if (answer.kind === "paused") return answer.reason === "budget" ? copy.pausedBudget : copy.pausedUnavailable;
  if (answer.kind === "limited") return copy.limited;
  if (answer.kind === "botCheck") return copy.botCheck;
  return copy.error;
}
