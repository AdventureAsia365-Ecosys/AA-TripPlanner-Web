// AA-1041 (R7 item 4) — quick reorders are saved one after another (lib/orderSaver.ts).
import { test } from "node:test";
import assert from "node:assert/strict";
import { createOrderSaver, type SaveOutcome } from "./orderSaver.ts";

type Stop = { component_id: string; tag?: string };
const trip = (ids: string, tag?: string): Stop[] => ids.split("").map((id) => ({ component_id: id, tag }));
const order = (it: readonly Stop[]) => it.map((d) => d.component_id).join("");
const reorderLocal = (base: Stop[], ids: string[]) => {
  const byId = new Map(base.map((d) => [d.component_id, d]));
  return ids.map((id) => byId.get(id) ?? { component_id: id });
};

// A fake trip Lambda: each send waits until the test answers it, in any order the test picks.
function harness() {
  const sent: { ids: string; resolve: (v: Stop[] | null) => void; reject: (e: Error) => void }[] = [];
  const shown: string[] = [];
  const outcomes: SaveOutcome[] = [];
  let lastShown: Stop[] = [];
  const saver = createOrderSaver<Stop>({
    send: (ids) =>
      new Promise((resolve, reject) => {
        sent.push({ ids: ids.join(""), resolve, reject });
      }),
    local: reorderLocal,
    show: (it) => {
      lastShown = it;
      shown.push(order(it));
    },
    settled: (o) => outcomes.push(o),
  });
  return { saver, sent, shown, outcomes, onScreen: () => lastShown };
}
const tick = () => new Promise((r) => setTimeout(r, 0));

test("two quick moves: the second starts from the first's drawn order, saves go one after another", async () => {
  const h = harness();
  const p1 = h.saver.save(trip("abc"), ["b", "a", "c"], false);
  await tick();
  // The second move is computed from the screen (bac), not from a server answer.
  const p2 = h.saver.save(trip("abc"), ["b", "c", "a"], false);
  assert.equal(order(h.onScreen()), "bca");
  await tick();
  // Only the first save is on the wire: the second waits for its answer.
  assert.deepEqual(h.sent.map((s) => s.ids), ["bac"]);
  h.sent[0].resolve(trip("bac", "server"));
  await p1;
  // The first answer is not drawn over the newer move.
  assert.equal(order(h.onScreen()), "bca");
  await tick();
  assert.deepEqual(h.sent.map((s) => s.ids), ["bac", "bca"]);
  h.sent[1].resolve(trip("bca", "server"));
  await p2;
  assert.equal(order(h.onScreen()), "bca");
  assert.equal(h.onScreen()[0].tag, "server");
  // Undo goes back to the order the server stored before the latest move.
  assert.deepEqual(h.outcomes, [{ ok: true, undo: ["b", "a", "c"] }]);
});

test("a burst of three quick moves: the first and the last leave, the middle one never does", async () => {
  // While the first save is on the wire the next two wait; when the first answer lands, the second is already
  // superseded by the third (which carries the full order), so it is dropped and the third is sent.
  const h = harness();
  const p1 = h.saver.save(trip("abc"), ["c", "a", "b"], false);
  await tick();
  const p2 = h.saver.save(trip("abc"), ["c", "b", "a"], false);
  const p3 = h.saver.save(trip("abc"), ["b", "c", "a"], false);
  assert.equal(order(h.onScreen()), "bca");
  await tick();
  assert.deepEqual(h.sent.map((s) => s.ids), ["cab"]);
  h.sent[0].resolve(trip("cab"));
  await p1;
  await p2;
  await tick();
  assert.equal(order(h.onScreen()), "bca", "the first answer is not drawn");
  assert.deepEqual(h.sent.map((s) => s.ids), ["cab", "bca"]);
  h.sent[1].resolve(trip("bca"));
  await p3;
  assert.deepEqual(h.shown, ["cab", "cba", "bca", "bca"]);
  // Undo points at the order the server stored before the last save that left.
  assert.deepEqual(h.outcomes, [{ ok: true, undo: ["c", "a", "b"] }]);
});

test("two moves in the same tick: only the second leaves", async () => {
  const h = harness();
  const p1 = h.saver.save(trip("abc"), ["b", "a", "c"], false);
  const p2 = h.saver.save(trip("abc"), ["b", "c", "a"], false);
  await p1;
  await tick();
  assert.deepEqual(h.sent.map((s) => s.ids), ["bca"]);
  h.sent[0].resolve(trip("bca"));
  await p2;
  assert.equal(order(h.onScreen()), "bca");
  assert.deepEqual(h.outcomes, [{ ok: true, undo: ["a", "b", "c"] }]);
});

test("a failed latest move draws the last order the server stored", async () => {
  const h = harness();
  const p1 = h.saver.save(trip("abc"), ["b", "a", "c"], false);
  await tick();
  const p2 = h.saver.save(trip("abc"), ["b", "c", "a"], false);
  await tick();
  h.sent[0].resolve(trip("bac"));
  await p1;
  await tick();
  h.sent[1].reject(new Error("network"));
  await p2;
  assert.equal(order(h.onScreen()), "bac");
  assert.deepEqual(h.outcomes, [{ ok: false }]);
});

test("a refused first move and a saved second one: undo points at a stored order", async () => {
  const h = harness();
  const p1 = h.saver.save(trip("abc"), ["b", "a", "c"], false);
  await tick();
  const p2 = h.saver.save(trip("abc"), ["b", "c", "a"], false);
  await tick();
  h.sent[0].resolve(null);
  await p1;
  assert.equal(order(h.onScreen()), "bca", "an earlier failure does not revert the newer move");
  await tick();
  h.sent[1].resolve(trip("bca"));
  await p2;
  // The server never stored "bac": undo goes back to "abc", the order before the burst.
  assert.deepEqual(h.outcomes, [{ ok: true, undo: ["a", "b", "c"] }]);
});

test("an undo save offers no further undo", async () => {
  const h = harness();
  const p = h.saver.save(trip("bac"), ["a", "b", "c"], true);
  await tick();
  h.sent[0].resolve(trip("abc"));
  await p;
  assert.deepEqual(h.outcomes, [{ ok: true, undo: null }]);
});

test("invalidate drops answers in flight; the next move starts from the itinerary as it is", async () => {
  const h = harness();
  const p1 = h.saver.save(trip("abc"), ["b", "a", "c"], false);
  await tick();
  h.saver.invalidate();
  const p2 = h.saver.save(trip("abcd"), ["d", "a", "b", "c"], false);
  assert.equal(order(h.onScreen()), "dabc");
  await tick();
  h.sent[0].resolve(trip("bac"));
  await p1;
  assert.equal(order(h.onScreen()), "dabc");
  await tick();
  h.sent[1].resolve(trip("dabc"));
  await p2;
  // The dropped answer did not count as stored: undo goes back to the itinerary the new move started from.
  assert.deepEqual(h.outcomes, [{ ok: true, undo: ["a", "b", "c", "d"] }]);
});

test("a save still waiting when the itinerary changes another way never leaves", async () => {
  // A stop added while a move waits for the previous answer: that move's order lacks the new stop, so it is dropped.
  const h = harness();
  const p1 = h.saver.save(trip("abc"), ["b", "a", "c"], false);
  await tick();
  const p2 = h.saver.save(trip("abc"), ["b", "c", "a"], false);
  h.saver.invalidate();
  h.sent[0].resolve(trip("bac"));
  await p1;
  await p2;
  await tick();
  assert.deepEqual(h.sent.map((s) => s.ids), ["bac"]);
  assert.deepEqual(h.shown, ["bac", "bca"]);
  assert.deepEqual(h.outcomes, []);
});
