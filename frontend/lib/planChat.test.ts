// AA-1041 slice 2: the planning chat's answer reading, history, apply steps and copy. Run: npm test.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  CHAT_COPY,
  HISTORY_TURNS,
  MAX_MESSAGE_CHARS,
  applySteps,
  chatCopy,
  historyForRequest,
  locksComposer,
  noticeFor,
  proposalSummary,
  readChatAnswer,
  showsAiLabel,
  type ChatMessage,
  type ChatProposal,
} from "./planChat.ts";

const read = (p: string) => readFileSync(new URL(p, import.meta.url), "utf8");

const PROPOSAL: ChatProposal = {
  action: "add",
  final_component_ids: ["a", "b"],
  add_component_ids: ["b"],
  remove_component_ids: [],
  stops: [
    { component_id: "a", name: "Lantern Market Walk", place: "Port Selby", new: false },
    { component_id: "b", name: "Saffron Kitchen Cooking Class", place: "Port Selby", new: true },
  ],
  removed: [],
};

test("a reply answer keeps the server's proposal only when it has the full shape", () => {
  const ok = readChatAnswer(200, false, { status: "ok", reply: "Here it is.", outcome: "answer", ai_generated: true,
    proposal: PROPOSAL });
  assert.deepEqual(ok, { kind: "reply", reply: "Here it is.", proposal: PROPOSAL, aiGenerated: true, outcome: "answer" });
  const broken = readChatAnswer(200, false, { status: "ok", reply: "x", proposal: { action: "add" } as unknown as ChatProposal });
  assert.equal(broken.kind === "reply" && broken.proposal, null);
  const server = readChatAnswer(200, false, { status: "ok", reply: "x", outcome: "fallback" });
  assert.equal(server.kind === "reply" && server.aiGenerated, false);
});

test("pauses, limits, the bot check and errors are told apart", () => {
  assert.deepEqual(readChatAnswer(200, false, { status: "paused", reason: "budget" }), { kind: "paused", reason: "budget" });
  assert.deepEqual(readChatAnswer(200, false, { status: "paused", reason: "x" }), { kind: "paused", reason: "unavailable" });
  assert.deepEqual(readChatAnswer(429, false, { error: "rate_limited", retry_after_s: 90 }), { kind: "limited", retryAfterS: 90 });
  assert.deepEqual(readChatAnswer(202, true, null), { kind: "botCheck" });
  assert.deepEqual(readChatAnswer(403, false, { error: "bot_check_failed" }), { kind: "botCheck" });
  assert.deepEqual(readChatAnswer(500, false, { error: "internal" }), { kind: "error" });
  assert.deepEqual(readChatAnswer(200, false, { status: "ok", reply: "  " }), { kind: "error" });
  assert.deepEqual(readChatAnswer(200, false, null), { kind: "error" });
});

test("only a money cap closes the composer; an unavailable pause or an error leaves it open", () => {
  assert.equal(locksComposer(readChatAnswer(200, false, { status: "paused", reason: "budget" })), true);
  assert.equal(locksComposer(readChatAnswer(200, false, { status: "paused", reason: "unavailable" })), false);
  assert.equal(locksComposer(readChatAnswer(200, false, { status: "paused" })), false);
  assert.equal(locksComposer(readChatAnswer(502, false, { error: "chat_failed" })), false);
  assert.equal(locksComposer(readChatAnswer(429, false, { retry_after_s: 30 })), false);
  assert.equal(locksComposer(readChatAnswer(200, false, { status: "ok", reply: "Here it is." })), false);
});

test("the history sent back is the last turns, capped", () => {
  const msgs: ChatMessage[] = Array.from({ length: 10 }, (_, i) => ({
    id: `m${i}`, role: i % 2 ? "assistant" : "user", text: i === 9 ? "x".repeat(900) : `turn ${i}`,
  }));
  const h = historyForRequest(msgs);
  assert.equal(h.length, HISTORY_TURNS);
  assert.equal(h[0].text, "turn 4");
  assert.equal(h[h.length - 1].text.length, 600);
  assert.deepEqual(historyForRequest([{ id: "e", role: "user", text: "  " }]), []);
});

test("applying a proposal removes first, then pins the new order", () => {
  assert.deepEqual(applySteps({ ...PROPOSAL, remove_component_ids: ["z"] }), { remove: ["z"], route: ["a", "b"] });
  assert.deepEqual(applySteps({ ...PROPOSAL, action: "remove", final_component_ids: [], remove_component_ids: ["a"] }),
    { remove: ["a"], route: [] });
});

test("the summary counts new and removed stops", () => {
  assert.equal(proposalSummary(PROPOSAL), "Adds 1 stop.");
  assert.equal(proposalSummary({ ...PROPOSAL, action: "replace" }), "Your trip becomes 2 stops.");
  assert.equal(proposalSummary({ ...PROPOSAL, action: "remove", stops: [PROPOSAL.stops[0]], removed: [PROPOSAL.stops[1]] }),
    "Removes 1 stop.");
  assert.equal(proposalSummary(PROPOSAL, "vi"), "Thêm 1 điểm dừng.");
});

test("English and Vietnamese copy carry the same keys; English is the default", () => {
  assert.deepEqual(Object.keys(CHAT_COPY.vi).sort(), Object.keys(CHAT_COPY.en).sort());
  assert.equal(chatCopy("vi-VN"), CHAT_COPY.vi);
  assert.equal(chatCopy(undefined), CHAT_COPY.en);
  assert.equal(noticeFor({ kind: "paused", reason: "budget" }, CHAT_COPY.en), CHAT_COPY.en.pausedBudget);
  assert.match(CHAT_COPY.en.pausedBudget, /paused/);
  assert.match(CHAT_COPY.en.tooLong, new RegExp(String(MAX_MESSAGE_CHARS)));
});

test("the limits match the server's (backend/assembly/plan_chat.py)", () => {
  const py = read("../../backend/assembly/plan_chat.py");
  assert.match(py, new RegExp(`^MAX_MESSAGE_CHARS = ${MAX_MESSAGE_CHARS}$`, "m"));
  assert.match(py, new RegExp(`^MAX_HISTORY_TURNS = ${HISTORY_TURNS}$`, "m"));
});

test("the chat goes through its protected path and never reloads the planner on a challenge", () => {
  const api = read("./api.ts");
  const chat = api.slice(api.indexOf("export async function sendChat"), api.indexOf("export interface Suggestion"));
  assert.match(chat, /\}, CHAT_PATH\);/);
  assert.doesNotMatch(chat, /throughWafEdge|reloadForWafToken/);
});

test("S277: only a reply the model wrote carries the AI label; server sentences and notices get none", () => {
  assert.equal(showsAiLabel({ role: "assistant", aiGenerated: true }), true);
  assert.equal(showsAiLabel({ role: "assistant", aiGenerated: false }), false);
  assert.equal(showsAiLabel({ role: "assistant" }), false);
  assert.equal(showsAiLabel({ role: "assistant", aiGenerated: true, notice: true }), false);
  assert.equal(showsAiLabel({ role: "user", aiGenerated: true }), false);
  const panel = read("../components/PlanChat.tsx");
  const header = panel.slice(panel.indexOf("<header"), panel.indexOf("</header>"));
  assert.doesNotMatch(header, /AiNotice/, "the header carries no AI notice");
  assert.match(panel, /\{showsAiLabel\(l\) && \(\s*<AiNotice[^>]*variant="narration"/, "the notice sits under an AI-written reply");
  assert.match(CHAT_COPY.en.intro, /AI assistant/);
});
