// AA-642 S0: the AI paused / budget notices. Run: npm test.
import { test } from "node:test";
import assert from "node:assert/strict";
import { AI_NOTICES, aiNotice, limitNotice, narrationNotice, noticeLang } from "./aiNotices.ts";

test("English and Vietnamese carry the same notices, none empty", () => {
  assert.deepEqual(Object.keys(AI_NOTICES.vi).sort(), Object.keys(AI_NOTICES.en).sort());
  for (const table of Object.values(AI_NOTICES)) {
    for (const text of Object.values(table)) assert.ok(text.trim().length > 20);
  }
});

test("the page language picks the table; English is the default", () => {
  assert.equal(noticeLang("vi"), "vi");
  assert.equal(noticeLang("vi-VN"), "vi");
  assert.equal(noticeLang("en-GB"), "en");
  assert.equal(noticeLang(""), "en");
  assert.equal(noticeLang(undefined), "en");
  assert.equal(aiNotice("narrationPaused", "vi"), AI_NOTICES.vi.narrationPaused);
  assert.equal(aiNotice("narrationPaused"), AI_NOTICES.en.narrationPaused); // no document under node
});

test("a narration answer maps to its notice; paused wins over the budget", () => {
  assert.equal(narrationNotice({ ai_paused: true }), "narrationPaused");
  assert.equal(narrationNotice({ ai_paused: true, ai_budget_exhausted: true }), "narrationPaused");
  assert.equal(narrationNotice({ ai_budget_exhausted: true }), "narrationBudget");
  assert.equal(narrationNotice({}), null);
});

test("a 429 maps to the limit notice of its route, in both languages", () => {
  assert.equal(limitNotice("narrate"), "narrationLimit");
  assert.equal(limitNotice("search"), "searchLimit");
  assert.ok(AI_NOTICES.vi.searchLimit && AI_NOTICES.en.narrationLimit);
});

// AA-1126 L2: the traveller's notice for a used-up AI allowance is neutral: no "today" (the agency quota is monthly), no
// limit, quota or plan words the traveller cannot act on; the agency reads the real reason in its own bell.
test("the allowance notice is neutral for the traveller, in both languages", () => {
  const en = AI_NOTICES.en.narrationBudget.toLowerCase();
  for (const word of ["today", "limit", "quota", "plan's", "budget", "upgrade"]) assert.ok(!en.includes(word), word);
  const vi = AI_NOTICES.vi.narrationBudget.toLowerCase();
  for (const word of ["hôm nay", "giới hạn", "hạn mức", "ngân sách"]) assert.ok(!vi.includes(word), word);
});
