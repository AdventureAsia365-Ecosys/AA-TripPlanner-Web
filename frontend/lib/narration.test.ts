// AA-819 D3 (S239): an AA itinerary day's title ran into its prose ("Arrival in Agra A 2-3 hour").
// Run: npm test (node --test with type stripping; no extra dependencies).
import { test } from "node:test";
import assert from "node:assert/strict";
import { parseNarration } from "./narration.ts";

test("an AA itinerary day keeps its title apart from the prose", () => {
  const raw =
    "Day 1: Fatehpur Sikri Detour and Arrival in Agra\nA 2-3 hour private vehicle journey heads eastward to Agra.\n" +
    "Day 2: Jaipur: Amber Fort, City Palace & Hawa Mahal\nVisit Amber Fort, City Palace, and the Hawa Mahal.";
  assert.deepEqual(parseNarration(raw), [
    { day: 1, title: "Fatehpur Sikri Detour and Arrival in Agra", text: "A 2-3 hour private vehicle journey heads eastward to Agra." },
    { day: 2, title: "Jaipur: Amber Fort, City Palace & Hawa Mahal", text: "Visit Amber Fort, City Palace, and the Hawa Mahal." },
  ]);
});

test("an AI-written one-line day has no title", () => {
  assert.deepEqual(parseNarration("Day 3: A slow morning by the lake, then a boat to the old palace."), [
    { day: 3, title: null, text: "A slow morning by the lake, then a boat to the old palace." },
  ]);
});

test("a first line that reads like a sentence is prose, not a title", () => {
  const [b] = parseNarration("Day 4: Drive to Jodhpur.\nOvernight in Jodhpur.");
  assert.equal(b.title, null);
  assert.equal(b.text, "Drive to Jodhpur.\nOvernight in Jodhpur.");
});

test("a 'Day N' inside the prose does not start a new block", () => {
  const blocks = parseNarration("Day 1: Hanoi\nRest today; Day 2 - the trek - starts early.");
  assert.equal(blocks.length, 1);
  assert.equal(blocks[0].title, "Hanoi");
});

test("markdown leftovers are stripped and text without day markers stays one block", () => {
  assert.deepEqual(parseNarration("**Day 1:** Hanoi old quarter walk."), [{ day: 1, title: null, text: "Hanoi old quarter walk." }]);
  assert.deepEqual(parseNarration("A free-form note."), [{ day: null, title: null, text: "A free-form note." }]);
  assert.deepEqual(parseNarration("  "), []);
});
